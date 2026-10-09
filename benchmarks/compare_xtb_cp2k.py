"""GFN1-xTB from tblite against CP2K's on the same periodic cell (docs/xtb-spec.md, X0):
the question is whether the forces the xtb stage reports are the method's, not one
program's.

    python compare_xtb_cp2k.py input STRUCTURE.xyz OUT.inp      # a CP2K input for the cell
    python compare_xtb_cp2k.py compare XTB.json CP2K.out FORCES.xyz

STRUCTURE.xyz is an exported structure (docs/export.md); XTB.json is the worker's result
for it, written with --forces (python -m ambuild.xtb_worker STRUCTURE.xyz --forces ...),
CP2K.out and FORCES.xyz CP2K's output and the forces file the input asks for. Prints, as
JSON: both energies, both largest forces, and the largest and RMS difference between the
two programs' forces on an atom (eV/Å).

Energies are not expected to agree to better than a few meV per atom: CP2K's GFN1-xTB
runs at zero electronic temperature and treats dispersion and the Coulomb sums with its
own cut-offs, where tblite uses 300 K. The forces are the check.
"""
import json
import math
import re
import sys

from ambuild import xtb

HARTREE_EV = 27.211386245988
BOHR_A = 0.529177210903

TEMPLATE = """&GLOBAL
  PROJECT cell
  RUN_TYPE ENERGY_FORCE
  PRINT_LEVEL LOW
&END GLOBAL
&FORCE_EVAL
  METHOD QS
  &DFT
    CHARGE {charge}
    &QS
      METHOD xTB
      &xTB
        DO_EWALD T
        CHECK_ATOMIC_CHARGES F
      &END xTB
    &END QS
    &SCF
      SCF_GUESS MOPAC
      EPS_SCF 1.0E-7
      MAX_SCF 200
      &OT
        PRECONDITIONER FULL_SINGLE_INVERSE
        MINIMIZER DIIS
      &END OT
      &OUTER_SCF
        MAX_SCF 10
        EPS_SCF 1.0E-7
      &END OUTER_SCF
    &END SCF
  &END DFT
  &SUBSYS
    &CELL
      ABC {a:.6f} {b:.6f} {c:.6f}
      PERIODIC XYZ
    &END CELL
    &COORD
{coords}
    &END COORD
  &END SUBSYS
  &PRINT
    &FORCES
      FILENAME =forces.xyz
    &END FORCES
  &END PRINT
&END FORCE_EVAL
"""


def writeInput(structureFile, out):
    s = xtb.readStructure(structureFile)
    a, b, c = s["lattice"]
    coords = "\n".join("      {0} {1:.6f} {2:.6f} {3:.6f}".format(sym.capitalize(), *xyz)
                       for sym, xyz in zip(s["symbols"], s["positions"]))
    with open(out, "w", newline="\n") as f:
        f.write(TEMPLATE.format(charge=xtb.netCharge(s["charges"]), a=a, b=b, c=c, coords=coords))


def cp2kEnergy(path):
    with open(path, errors="replace") as f:
        found = re.findall(r"ENERGY\| Total FORCE_EVAL \( QS \) energy \[\S+\]:?\s+(-?[\d.]+)", f.read())
    return float(found[-1]) * HARTREE_EV


def cp2kForces(path):
    """Forces (eV/Å) from CP2K's forces file: rows of atom, kind, element, x, y, z in Eh/bohr"""
    forces = []
    with open(path, errors="replace") as f:
        for line in f:
            fields = line.split()
            if len(fields) == 6 and fields[0].isdigit() and fields[1].isdigit():
                forces.append([float(x) * HARTREE_EV / BOHR_A for x in fields[3:]])
    return forces


def compare(xtbFile, cp2kOut, cp2kForcesFile):
    with open(xtbFile) as f:
        ours = json.load(f)
    theirs = cp2kForces(cp2kForcesFile)
    mine = ours["forces_eV_A"]
    if len(mine) != len(theirs):
        raise SystemExit("{0} forces from tblite, {1} from CP2K".format(len(mine), len(theirs)))
    norm = lambda v: math.sqrt(sum(x * x for x in v))
    differences = [norm([a - b for a, b in zip(m, t)]) for m, t in zip(mine, theirs)]
    energy = cp2kEnergy(cp2kOut)
    return {
        "atoms": len(mine), "method": ours["method"], "tblite": ours["program_version"],
        "tblite_energy_eV": ours["energy_eV"], "cp2k_energy_eV": energy,
        "energy_difference_meV_per_atom": 1000 * (ours["energy_eV"] - energy) / len(mine),
        "tblite_fmax_eV_A": max(map(norm, mine)), "cp2k_fmax_eV_A": max(map(norm, theirs)),
        "tblite_worst_atom": max(range(len(mine)), key=lambda i: norm(mine[i])),
        "cp2k_worst_atom": max(range(len(theirs)), key=lambda i: norm(theirs[i])),
        "largest_force_difference_eV_A": max(differences),
        "rms_force_difference_eV_A": math.sqrt(sum(d * d for d in differences) / len(differences)),
    }


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "input":
        writeInput(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 5 and sys.argv[1] == "compare":
        print(json.dumps(compare(*sys.argv[2:]), indent=1))
    else:
        sys.exit(__doc__)
