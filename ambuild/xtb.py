"""xTB checks on a built structure: the forces a semi-empirical method (GFN-FF, GFN1-xTB or
GFN2-xTB) puts on the atoms at the geometry the force field made, and how far a fixed-cell
relaxation moves them (docs/xtb-spec.md).

Like Poreblazer and liminal, xTB is run as an external program: Ambuild runs a worker
(ambuild.xtb_worker, which imports tblite and ASE, or calls the xtb binary for GFN-FF) on a
checkpoint's exported structure (docs/export.md) and reads the results file it writes
(xtb.json, format "ambuild-xtb" version 1). Nothing else in Ambuild imports tblite or ASE.

The worker: XTB_WORKER, a command such as "/opt/xtb-env/bin/python -m ambuild.xtb_worker";
by default this interpreter, if it has what the method needs (tblite for gfn1 and gfn2; the
xtb binary, XTB_EXE or xtb on the PATH, for gfnff).

This module needs only the standard library (the web GUI and the campaign controller use
its metric names).
"""
import importlib.util
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)

EVENT = "xtb_result"  # data: summarise(), plus the files, directory and exit code
FORMAT, VERSION = "ambuild-xtb", 1
NAME_STEM = "xtb"  # xtb_<fileCount>/ in the run directory
RESULTS = "xtb.json"
RELAXED = "relaxed.xyz"

METHODS = {"gfnff": "GFN-FF", "gfn1": "GFN1-xTB", "gfn2": "GFN2-xTB"}
MODES = ["single_point", "relax"]
# Doubling the atoms took 5 to 6 times as long and 3 to 4 times the memory, for periodic
# GFN-FF as for tight binding (docs/xtb-spec.md, Scaling), so larger cells are refused
# unless the stage says otherwise
MAX_ATOMS = {"gfnff": 2000, "gfn1": 2000, "gfn2": 2000}
CHARGE_TOLERANCE = 0.01  # how far the atoms' charges may sum from a whole number (e)

# Peak memory of the worker: an upper bound through the two sizes measured for each method
# (docs/xtb-spec.md, Scaling: at most 579 MB at 472 atoms and 2062 MB at 944), so anything
# far above 944 atoms is extrapolated
MEMORY_BASE_MB, MEMORY_MB_AT_REFERENCE, MEMORY_REFERENCE_ATOMS, MEMORY_EXPONENT = 150.0, 550.0, 472, 1.85


def memoryEstimateMb(atoms):
    """An estimate of the worker's peak memory (MB) for a periodic cell of this many atoms"""
    return MEMORY_BASE_MB + MEMORY_MB_AT_REFERENCE * (float(atoms) / MEMORY_REFERENCE_ATOMS) ** MEMORY_EXPONENT

# Metrics for sweeps and campaigns: xtb_<field>
FIELDS = {
    "fmax": "largest force at the built geometry (eV/Å, xTB)",
    "frms": "RMS force at the built geometry (eV/Å, xTB)",
    "energy_per_atom": "energy per atom (eV, xTB)",
    "gap": "HOMO–LUMO gap (eV, xTB)",
    "relax_rmsd": "RMS displacement on relaxing (Å, xTB)",
    "relax_max_bond_change": "largest bond length change on relaxing (Å, xTB)",
    "relax_energy_drop": "energy released per atom on relaxing (eV, xTB)",
    "relax_reached_fmax": "relaxation reached its force threshold (1) or its step cap (0)",
    "d_surface_area": "change in accessible surface area on relaxing (m²/g, xTB)",
    "d_pld": "change in pore limiting diameter on relaxing (Å, xTB)",
}
# The Poreblazer figures compared between the built and the relaxed cell: metric field -> result key
PORE_CHANGES = {"d_surface_area": "surface_area_m2_g", "d_pld": "pore_limiting_diameter_A"}
PORE_KEYS = ("surface_area_m2_g", "pore_limiting_diameter_A", "maximum_pore_diameter_A", "helium_volume_cm3_g",
             "percolated_dimensions")
PREFIX = "xtb"
METRICS = ["{0}_{1}".format(PREFIX, f) for f in FIELDS]


def metricLabel(metric):
    return FIELDS.get(metric[len(PREFIX) + 1:], metric)


def metrics(summary):
    """{metric: value} from a run's latest xTB summary (None: no figures)"""
    summary = summary or {}
    relax = summary.get("relax") or {}
    atoms = summary.get("atoms")
    values = {
        "fmax": summary.get("fmax_eV_A"),
        "frms": summary.get("frms_eV_A"),
        "energy_per_atom": summary.get("energy_per_atom_eV"),
        "gap": summary.get("gap_eV"),
        "relax_rmsd": relax.get("rmsd_A"),
        "relax_max_bond_change": relax.get("max_bond_change_A"),
        "relax_energy_drop": None,
        "relax_reached_fmax": None if relax.get("reached_fmax") is None else int(relax["reached_fmax"]),
    }
    values.update(poreChanges(summary.get("poreblazer")))
    if atoms and relax.get("energy_eV") is not None and summary.get("energy_eV") is not None:
        values["relax_energy_drop"] = (summary["energy_eV"] - relax["energy_eV"]) / atoms
    return {"{0}_{1}".format(PREFIX, f): values[f] for f in FIELDS}


def poreChanges(comparison):
    """{d_surface_area, d_pld}: relaxed minus built, from the stage's Poreblazer comparison
    ({"built": {...}, "relaxed": {...}}); None where either run gave no figure"""
    built, relaxed = ((comparison or {}).get(k) or {} for k in ("built", "relaxed"))
    return {field: relaxed[key] - built[key] if built.get(key) is not None and relaxed.get(key) is not None else None
            for field, key in PORE_CHANGES.items()}


# --- settings

def checkSettings(method, mode):
    """Raise a ValueError for a method or mode this Ambuild can't run"""
    if method not in METHODS:
        raise ValueError("xtb method must be one of {0}, not {1!r}".format(", ".join(METHODS), method))
    if mode not in MODES:
        raise ValueError("xtb mode must be one of {0}, not {1!r}".format(", ".join(MODES), mode))


def netCharge(charges):
    """The whole-number charge (e) the atoms' charges sum to; a ValueError if they don't"""
    total = sum(charges)
    whole = int(round(total))
    if abs(total - whole) > CHARGE_TOLERANCE:
        raise ValueError("The atoms' charges sum to {0:.4f} e, which is not a whole number; give the stage the "
                         "cell's charge".format(total))
    return whole


# --- the programs

def _split(command):
    if os.name == "nt":  # POSIX splitting would eat the backslashes of Windows paths
        return [part.strip('"') for part in shlex.split(command, posix=False)]
    return shlex.split(command)


def xtbExecutable(xtbExe=None):
    """The xtb binary (for GFN-FF) as a list, or None if there is none"""
    command = xtbExe or os.environ.get("XTB_EXE")
    if command:
        return _split(command)
    found = shutil.which("xtb")
    return [found] if found else None


def workerCommand(method, worker=None):
    """The worker's command as a list, or None if nothing here can run the method"""
    command = worker or os.environ.get("XTB_WORKER")
    if command:
        return _split(command)
    if method == "gfnff":
        available = xtbExecutable() is not None
    else:
        available = importlib.util.find_spec("tblite") is not None
    return [sys.executable, "-m", "ambuild.xtb_worker"] if available else None


def missing(method):
    """What to tell someone whose recipe needs a worker that isn't there"""
    needs = "the xtb binary (XTB_EXE, or xtb on the PATH)" if method == "gfnff" else "tblite"
    return ("The recipe runs xTB ({0}): set XTB_WORKER to a worker command, or install {1} for this "
            "interpreter".format(METHODS.get(method, method), needs))


def runWorker(command, structure, out, log, method, mode="single_point", maxSteps=None, fmax=None, charge=None,
              topology=None, relaxed=None, threads=None):
    """Run the worker; returns its exit code. threads sets OMP_NUM_THREADS"""
    args = command + [structure, "--out", out, "--method", method, "--mode", mode]
    for flag, value in (("--max-steps", maxSteps), ("--fmax", fmax), ("--charge", charge),
                        ("--topology", topology), ("--relaxed", relaxed)):
        if value is not None:
            args += [flag, str(value)]
    env = None
    if threads is not None:
        env = dict(os.environ, OMP_NUM_THREADS=str(int(threads)))
    logger.info("Running the xTB worker: %s", " ".join(shlex.quote(a) for a in args))
    with open(log, "w") as f:
        return subprocess.run(args, stdout=f, stderr=subprocess.STDOUT, env=env).returncode


# --- the results file

def readResults(path):
    """xtb.json, checked to be the format this Ambuild reads"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("format") != FORMAT or data.get("version") != VERSION:
        raise RuntimeError("{0}: not {1} version {2} (format {3!r}, version {4!r}); this Ambuild reads only that".format(
            path, FORMAT, VERSION, data.get("format"), data.get("version")))
    return data


def summarise(data):
    """The figures Ambuild records from xtb.json"""
    keys = ("method", "program", "program_version", "atoms", "charge", "mode", "converged", "error", "seconds",
            "energy_eV", "fmax_eV_A", "frms_eV_A", "gap_eV", "worst_atoms", "relax")
    out = {k: data.get(k) for k in keys}
    atoms, energy = data.get("atoms"), data.get("energy_eV")
    out["energy_per_atom_eV"] = energy / atoms if atoms and energy is not None else None
    return out


# --- the structure file (export format version 1, docs/export.md)

def readStructure(path):
    """An extended XYZ file as Ambuild writes it: {"header", "lattice": [A, B, C],
    "columns": [name, ...], "symbols", "positions", "charges", "rows": the atoms' fields}"""
    with open(path, encoding="utf-8") as f:
        count = int(f.readline())
        header = f.readline().rstrip("\n")
        rows = [f.readline().split() for _ in range(count)]
    lattice = re.search(r'Lattice="([^"]*)"', header)
    properties = re.search(r"Properties=(\S+)", header)
    if not lattice or not properties:
        raise ValueError("{0}: no Lattice or Properties in its header".format(path))
    cell = [float(x) for x in lattice.group(1).split()]
    if len(cell) != 9 or any(abs(cell[i]) > 1e-9 for i in (1, 2, 3, 5, 6, 7)):
        raise ValueError("{0}: only orthorhombic cells are read".format(path))
    columns = []
    fields = properties.group(1).split(":")
    for name, _, width in zip(fields[0::3], fields[1::3], fields[2::3]):
        columns += [name] * int(width)
    if any(len(row) != len(columns) for row in rows):
        raise ValueError("{0}: an atom's fields don't match its Properties".format(path))
    pos = columns.index("pos")
    charge = columns.index("charge") if "charge" in columns else None
    return {
        "header": header, "lattice": [cell[0], cell[4], cell[8]], "columns": columns, "rows": rows,
        "symbols": [row[columns.index("species")] for row in rows],
        "positions": [[float(x) for x in row[pos:pos + 3]] for row in rows],
        "charges": [float(row[charge]) if charge is not None else 0.0 for row in rows],
    }


def writePlainXyz(path, structure):
    """The structure as a plain XYZ file for Poreblazer: elements and positions, wrapped
    into the cell (a relaxed structure's are not)"""
    with open(path, "w", newline="\n") as f:
        f.write("{0}\n\n".format(len(structure["symbols"])))
        for symbol, xyz in zip(structure["symbols"], structure["positions"]):
            wrapped = [x % L for x, L in zip(xyz, structure["lattice"])]
            f.write("{0} {1:.6f} {2:.6f} {3:.6f}\n".format(symbol.capitalize(), *wrapped))
    return path


def writeRelaxed(path, structure, positions, method):
    """The structure with new positions, in the same format, atom order and header, which
    gains relaxed_by. The positions are not wrapped back into the cell, so the topology's
    bond images still hold for atoms that crossed a face"""
    pos = structure["columns"].index("pos")
    with open(path, "w", newline="\n", encoding="utf-8") as f:
        f.write('{0}\n{1} relaxed_by="{2}"\n'.format(len(positions), structure["header"], method))
        for row, xyz in zip(structure["rows"], positions):
            f.write(" ".join(row[:pos] + ["{0:.6f}".format(x) for x in xyz] + row[pos + 3:]) + "\n")
    return path
