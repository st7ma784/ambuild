"""A stand-in for Ambuild's xTB worker (ambuild.xtb_worker), for tests without tblite: it
writes xtb.json ("ambuild-xtb" version 1) with figures derived from the structure's atom
count, and the arguments it was given.

    python fake_xtb.py STRUCTURE --out xtb.json [--method M] [--mode MODE] [--max-steps N] ...

--max-steps 99 fails (exit code 5); 98 writes format version 0; 97 doesn't converge.
"""
import argparse
import json
import os
import shutil
import sys

METHODS = {"gfnff": "GFN-FF", "gfn1": "GFN1-xTB", "gfn2": "GFN2-xTB"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("structure")
    parser.add_argument("--out", required=True)
    parser.add_argument("--method", default="gfn1")
    parser.add_argument("--mode", default="single_point")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--fmax", type=float)
    parser.add_argument("--charge", type=int)
    parser.add_argument("--topology")
    parser.add_argument("--relaxed")
    args = parser.parse_args()
    if args.max_steps == 99:
        print("xtb failed", file=sys.stderr)
        return 5
    with open(args.structure) as f:
        atoms = int(f.readline())
    data = {"format": "ambuild-xtb", "version": 0 if args.max_steps == 98 else 1, "method": METHODS[args.method],
            "program": "fake", "program_version": "0", "structure": os.path.basename(args.structure),
            "structure_sha256": "0" * 64, "atoms": atoms, "charge": args.charge, "mode": args.mode,
            "converged": True, "error": None, "seconds": 0.01,
            "energy_eV": -10.0 * atoms, "fmax_eV_A": 1.5, "frms_eV_A": 0.5, "gap_eV": 2.0,
            "worst_atoms": [[3, 1.5], [0, 0.75]], "relax": None,
            "flags": [a for a in sys.argv[2:] if a.startswith("--")], "threads": os.environ.get("OMP_NUM_THREADS")}
    if args.max_steps == 97:
        data.update(converged=False, error="SCF not converged", energy_eV=None, fmax_eV_A=None, frms_eV_A=None,
                    gap_eV=None, worst_atoms=None)
    elif args.mode == "relax":
        shutil.copyfile(args.structure, args.relaxed)
        data["relax"] = {"steps": args.max_steps, "max_steps": args.max_steps, "fmax_threshold_eV_A": args.fmax,
                         "energy_eV": -10.0 * atoms - 0.25 * atoms, "fmax_eV_A": 0.2, "frms_eV_A": 0.1,
                         "reached_fmax": False, "rmsd_A": 0.125, "max_displacement_A": 0.5, "bonds": 6,
                         "max_bond_change_A": 0.0625, "worst_bonds": [[0, 1, 1.5, 1.4375]],
                         "structure": os.path.basename(args.relaxed)}
    with open(args.out, "w") as f:
        json.dump(data, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
