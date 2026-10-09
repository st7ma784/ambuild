"""The xTB worker: one calculation on one exported structure (ambuild.xtb, docs/xtb-spec.md).

    python -m ambuild.xtb_worker STRUCTURE --out xtb.json [--method gfn1] [--mode single_point]
        [--max-steps 50] [--fmax 0.05] [--charge N] [--topology FILE] [--relaxed relaxed.xyz]
        [--forces]

It writes xtb.json (format "ambuild-xtb" version 1): the energy, the forces' largest and
RMS values and the atoms under the largest, at the structure's geometry; and with --mode
relax, how far a fixed-cell relaxation moved the atoms and the topology's bonds, with the
relaxed structure in --relaxed. A calculation that doesn't converge is recorded in the file
("converged": false), with exit code 0; any other failure is a non-zero exit code.

gfn1 and gfn2 need tblite (and ASE to relax, with LBFGS); gfnff needs the xtb binary
(XTB_EXE, or xtb on the PATH), 6.7 or later, and relaxes with xtb's own optimiser, whose
level is chosen from --fmax. Threads: OMP_NUM_THREADS.
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from ambuild import xtb

HARTREE_EV = 27.211386245988
BOHR_A = 0.529177210903
MAX_SCF = 500
WORST = 10  # atoms and bonds listed
ELEMENTS = ("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr "
            "Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb "
            "Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn").split()


class NotConverged(Exception):
    """The calculation ran but gave no answer"""


def atomicNumbers(symbols):
    numbers = []
    for s in symbols:
        if s.capitalize() not in ELEMENTS:
            raise ValueError("No element {0!r}".format(s))
        numbers.append(ELEMENTS.index(s.capitalize()) + 1)
    return numbers


def forceFigures(forces):
    """{fmax, frms, worst atoms} from the atoms' forces (eV/Å)"""
    norms = [math.sqrt(fx * fx + fy * fy + fz * fz) for fx, fy, fz in forces]
    worst = sorted(range(len(norms)), key=lambda i: (-norms[i], i))[:WORST]
    return {
        "fmax_eV_A": max(norms),
        "frms_eV_A": math.sqrt(sum(n * n for n in norms) / len(norms)),
        "worst_atoms": [[i, round(norms[i], 6)] for i in worst],
    }


# --- tblite (GFN1-xTB, GFN2-xTB)

def tbliteVersion():
    from tblite.library import get_version

    return ".".join(str(v) for v in get_version())


def tbliteSinglePoint(structure, method, charge):
    """(energy eV, forces eV/Å, gap eV or None) from tblite's own interface"""
    import numpy as np
    from tblite.interface import Calculator

    lattice = np.diag(structure["lattice"]) / BOHR_A
    calc = Calculator(xtb.METHODS[method], np.array(atomicNumbers(structure["symbols"])),
                      np.array(structure["positions"]) / BOHR_A, charge=charge, lattice=lattice,
                      periodic=np.array([True, True, True]))
    calc.set("max-iter", MAX_SCF)
    try:
        res = calc.singlepoint()
    except RuntimeError as e:  # tblite raises it for an SCF that doesn't converge
        raise NotConverged(str(e))
    forces = -res.get("gradient") * (HARTREE_EV / BOHR_A)
    gap = None
    energies, occupations = res.get("orbital-energies"), res.get("orbital-occupations")
    if energies.ndim == 1:  # one spin channel: orbitals hold up to 2 electrons
        occupied, empty = energies[occupations >= 1.0], energies[occupations < 1.0]
        if len(occupied) and len(empty):
            gap = float(empty.min() - occupied.max()) * HARTREE_EV
    return float(res.get("energy")) * HARTREE_EV, forces.tolist(), gap


def tbliteRelax(structure, method, charge, maxSteps, fmax):
    """A fixed-cell relaxation with ASE's LBFGS: (steps, energy eV, forces eV/Å, positions Å).
    The positions are not wrapped, so each atom's displacement is its move"""
    from ase import Atoms
    from ase.optimize import LBFGS
    from tblite.ase import TBLite

    atoms = Atoms(symbols=[s.capitalize() for s in structure["symbols"]], positions=structure["positions"],
                  cell=structure["lattice"], pbc=True)
    atoms.calc = TBLite(method=xtb.METHODS[method], charge=charge, max_iterations=MAX_SCF, verbosity=0)
    optimiser = LBFGS(atoms, logfile="-")
    try:
        optimiser.run(fmax=fmax, steps=maxSteps)
        return optimiser.nsteps, float(atoms.get_potential_energy()), atoms.get_forces().tolist(), \
            atoms.get_positions().tolist()
    except Exception as e:  # ASE wraps tblite's errors in its own
        raise NotConverged("after {0} steps: {1}".format(optimiser.nsteps, e))


# --- the xtb binary (GFN-FF)

def writeCoord(path, structure):
    """A Turbomole coord file with the cell, which xtb reads without reordering the atoms"""
    with open(path, "w", newline="\n") as f:
        f.write("$coord angs\n")
        for symbol, (x, y, z) in zip(structure["symbols"], structure["positions"]):
            f.write("{0:.8f} {1:.8f} {2:.8f} {3}\n".format(x, y, z, symbol.lower()))
        A, B, C = structure["lattice"]
        f.write("$periodic 3\n$lattice angs\n{0:.8f} 0.0 0.0\n0.0 {1:.8f} 0.0\n0.0 0.0 {2:.8f}\n$end\n".format(A, B, C))


def readEngrad(path):
    """(energy Eh, gradient Eh/bohr per atom) from the .engrad file xtb --grad writes: blocks
    of numbers between comment lines, the atom count, the energy and the gradient"""
    blocks, block = [], []
    with open(path) as f:
        for line in f:
            if line.startswith("#"):
                if block:
                    blocks.append(block)
                    block = []
            elif line.strip():
                block.append(line.split())
    if block:
        blocks.append(block)
    count, energy = int(blocks[0][0][0]), float(blocks[1][0][0])
    flat = [float(row[0]) for row in blocks[2]]
    if len(flat) != 3 * count:
        raise ValueError("{0}: {1} gradient components for {2} atoms".format(path, len(flat), count))
    return energy, [flat[3 * i:3 * i + 3] for i in range(count)]


def xtbVersion(command):
    out = subprocess.run(command + ["--version"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "xtb version" in line:
            return line.split("xtb version")[1].split()[0]
    return None


def _unlimitedStack():
    """xtb keeps large arrays on the stack: with the usual 8 MB limit it segfaults on cells
    of about a thousand atoms. Raise the limit as far as this process may"""
    import resource

    hard = resource.getrlimit(resource.RLIMIT_STACK)[1]
    resource.setrlimit(resource.RLIMIT_STACK, (hard, hard))


def runXtb(command, work, args):
    """Run the xtb binary in work, its output going to this process's (the stage's log);
    returns (exit code, output)"""
    args = command + args
    print(" ".join(args), flush=True)
    out = os.path.join(work, "xtb.out")
    with open(out, "w") as f:
        code = subprocess.run(args, cwd=work, stdout=f, stderr=subprocess.STDOUT,
                              env=dict(os.environ, OMP_STACKSIZE=os.environ.get("OMP_STACKSIZE", "1G")),
                              preexec_fn=_unlimitedStack if os.name == "posix" else None).returncode
    with open(out, errors="replace") as f:
        text = f.read()
    sys.stdout.write(text)
    sys.stdout.flush()
    return code, text


def _gfnffForces(structure, charge, command, work):
    """(energy eV, forces eV/Å) of the structure, written to work as structure.coord"""
    writeCoord(os.path.join(work, "structure.coord"), structure)
    engrad = os.path.join(work, "structure.engrad")
    if os.path.isfile(engrad):
        os.remove(engrad)
    code, _ = runXtb(command, work, ["structure.coord", "--gfnff", "--grad", "--chrg", str(charge)])
    if code != 0 or not os.path.isfile(engrad):
        raise RuntimeError("xtb failed (exit code {0})".format(code))
    energy, gradient = readEngrad(engrad)
    scale = HARTREE_EV / BOHR_A
    return energy * HARTREE_EV, [[-g * scale for g in row] for row in gradient]


def gfnffSinglePoint(structure, charge, command):
    """(energy eV, forces eV/Å, None) from the xtb binary, run in a scratch directory"""
    work = tempfile.mkdtemp(prefix="ambuild-xtb-")
    try:
        energy, forces = _gfnffForces(structure, charge, command, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return energy, forces, None


def readCoord(path):
    """(positions, lattice vectors or None), in Å, from a Turbomole coord file; each block
    is in bohr unless it says angs"""
    positions, lattice, scale, block = [], [], BOHR_A, None
    with open(path) as f:
        for line in f:
            if line.startswith("$"):
                block = line.split()[0]
                if "frac" in line:
                    raise ValueError("{0}: fractional coordinates are not read".format(path))
                scale = 1.0 if "angs" in line else BOHR_A
            elif block == "$coord" and len(line.split()) >= 4:
                positions.append([float(x) * scale for x in line.split()[:3]])
            elif block == "$lattice" and len(line.split()) == 3:
                lattice.append([float(x) * scale for x in line.split()])
    return positions, lattice or None


def unwrapped(positions, reference, lattice):
    """positions, each moved by whole cells to the image nearest its atom in reference: xtb
    may wrap an atom back into the cell, and a relaxation moves no atom half a cell"""
    return [[p[k] - lattice[k] * round((p[k] - r[k]) / lattice[k]) for k in range(3)]
            for p, r in zip(positions, reference)]


def gfnffLevel(fmax):
    """xtb's optimisation level for a force threshold (eV/Å). Its levels converge on the
    gradient's norm, not the largest force, so whether fmax was reached is measured after"""
    return "tight" if fmax < 0.03 else ("normal" if fmax < 0.1 else "crude")


def gfnffRelax(structure, charge, command, maxSteps, fmax):
    """A fixed-cell relaxation with xtb's own optimiser: (steps, energy eV, forces eV/Å,
    positions Å, not wrapped). --nocellopt keeps the cell: by default xtb relaxes a
    periodic cell's lattice with its atoms. xtb exits non-zero at its cycle cap, having
    written the last geometry, which is used as a capped relaxation's is"""
    work = tempfile.mkdtemp(prefix="ambuild-xtb-")
    try:
        writeCoord(os.path.join(work, "structure.coord"), structure)
        code, text = runXtb(command, work, ["structure.coord", "--gfnff", "--opt", gfnffLevel(fmax), "--cycles",
                                            str(maxSteps), "--nocellopt", "--chrg", str(charge)])
        found = [p for p in (os.path.join(work, n) for n in ("xtbopt.coord", "xtblast.coord")) if os.path.isfile(p)]
        if not found:
            raise NotConverged("xtb wrote no optimised geometry (exit code {0})".format(code))
        positions, lattice = readCoord(found[0])
        if lattice and any(abs(lattice[k][k] - structure["lattice"][k]) > 1e-4 for k in range(3)):
            raise RuntimeError("xtb changed the cell to {0}: it must stay as built".format(
                " x ".join("{0:.4f}".format(lattice[k][k]) for k in range(3))))
        if len(positions) != len(structure["positions"]):
            raise RuntimeError("{0}: {1} atoms, not {2}".format(found[0], len(positions), len(structure["positions"])))
        positions = unwrapped(positions, structure["positions"], structure["lattice"])
        steps = maxSteps
        for pattern in (r"CONVERGED AFTER\s+(\d+)\s+ITERATIONS", r"OPTIMIZATION IN\s+(\d+)\s+ITERATIONS"):
            match = re.search(pattern, text)
            if match:
                steps = int(match.group(1))
                break
    finally:
        shutil.rmtree(work, ignore_errors=True)
    # in a directory of its own: xtb would reuse the topology file the optimisation left
    energy, forces, _ = gfnffSinglePoint(dict(structure, positions=positions), charge, command)
    return steps, energy, forces, positions


# --- relaxation figures

def bondLength(positions, lattice, bond):
    i, j, image = bond
    return math.sqrt(sum((positions[j][k] + image[k] * lattice[k] - positions[i][k]) ** 2 for k in range(3)))


def relaxFigures(structure, positions, bonds):
    """How far the atoms and the bonds moved between the structure and positions"""
    moves = [math.sqrt(sum((b - a) ** 2 for a, b in zip(old, new)))
             for old, new in zip(structure["positions"], positions)]
    figures = {"rmsd_A": math.sqrt(sum(m * m for m in moves) / len(moves)), "max_displacement_A": max(moves),
               "bonds": len(bonds) if bonds is not None else None, "max_bond_change_A": None, "worst_bonds": None}
    if bonds:
        lattice = structure["lattice"]
        changes = [(bondLength(structure["positions"], lattice, b), bondLength(positions, lattice, b), b)
                   for b in bonds]
        changes.sort(key=lambda c: (-abs(c[1] - c[0]), c[2][0], c[2][1]))
        figures["max_bond_change_A"] = abs(changes[0][1] - changes[0][0])
        figures["worst_bonds"] = [[b[0], b[1], round(before, 4), round(after, 4)]
                                  for before, after, b in changes[:WORST]]
    return figures


def readBonds(path):
    """The topology's bonds, [[i, j, image], ...] (docs/export.md)"""
    with open(path, encoding="utf-8") as f:
        topology = json.load(f)
    if topology.get("format") != "ambuild-topology" or topology.get("version") != 1:
        raise ValueError("{0}: not ambuild-topology version 1".format(path))
    return topology["bonds"]


# --- the worker

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("structure")
    parser.add_argument("--out", required=True)
    parser.add_argument("--method", choices=list(xtb.METHODS), default="gfn1")
    parser.add_argument("--mode", choices=xtb.MODES, default="single_point")
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument("--fmax", type=float, default=0.05)
    parser.add_argument("--charge", type=int)
    parser.add_argument("--topology")
    parser.add_argument("--relaxed")
    parser.add_argument("--forces", action="store_true",
                        help="also write every atom's force at the structure's geometry (forces_eV_A)")
    args = parser.parse_args(argv)
    xtb.checkSettings(args.method, args.mode)

    structure = xtb.readStructure(args.structure)
    charge = args.charge if args.charge is not None else xtb.netCharge(structure["charges"])
    bonds = readBonds(args.topology) if args.topology else None
    start = time.time()
    if args.method == "gfnff":
        command = xtb.xtbExecutable()
        if command is None:
            sys.exit("gfnff needs the xtb binary: set XTB_EXE, or put xtb on the PATH")
        program, version = "xtb", xtbVersion(command)
    else:
        program, version = "tblite", tbliteVersion()
    result = {"format": xtb.FORMAT, "version": xtb.VERSION, "method": xtb.METHODS[args.method], "program": program,
              "program_version": version, "structure": os.path.basename(args.structure),
              "structure_sha256": sha256(args.structure), "atoms": len(structure["symbols"]), "charge": charge,
              "mode": args.mode, "converged": True, "error": None,
              "energy_eV": None, "fmax_eV_A": None, "frms_eV_A": None, "gap_eV": None, "worst_atoms": None,
              "relax": None}
    try:
        if args.method == "gfnff":
            energy, forces, gap = gfnffSinglePoint(structure, charge, command)
        else:
            energy, forces, gap = tbliteSinglePoint(structure, args.method, charge)
        result.update(forceFigures(forces), energy_eV=energy, gap_eV=gap)
        if args.forces:  # for comparing programs (benchmarks/compare_xtb_cp2k.py); not recorded
            result["forces_eV_A"] = forces
        if args.mode == "relax":
            if args.method == "gfnff":
                steps, energy, forces, positions = gfnffRelax(structure, charge, command, args.max_steps, args.fmax)
            else:
                steps, energy, forces, positions = tbliteRelax(structure, args.method, charge, args.max_steps,
                                                               args.fmax)
            relaxed = forceFigures(forces)
            result["relax"] = dict(relaxFigures(structure, positions, bonds), steps=steps, energy_eV=energy,
                                   fmax_eV_A=relaxed["fmax_eV_A"], frms_eV_A=relaxed["frms_eV_A"],
                                   reached_fmax=relaxed["fmax_eV_A"] < args.fmax, max_steps=args.max_steps,
                                   fmax_threshold_eV_A=args.fmax, structure=None)
            if args.relaxed:
                xtb.writeRelaxed(args.relaxed, structure, positions, xtb.METHODS[args.method])
                result["relax"]["structure"] = os.path.basename(args.relaxed)
    except NotConverged as e:
        print("Not converged: {0}".format(e), flush=True)
        result.update(converged=False, error=str(e))
    result["seconds"] = round(time.time() - start, 2)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
        f.write("\n")
    return 0


def sha256(path):
    import hashlib

    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


if __name__ == "__main__":
    sys.exit(main())
