"""A stand-in for liminal's `map` command, for Ambuild's tests: it writes map.json
("liminal-map" version 1, as liminal documents it) and a small energy.cube, with figures
derived from the structure's atom count and the ion, so each ion's are different.

    python fake_liminal.py map STRUCTURE --out DIR --ion ION [...]

Ion "Xx+" fails (exit code 3); ion "Old+" writes format version 0.
"""
import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["map"])
    parser.add_argument("structure")
    parser.add_argument("--out", required=True)
    parser.add_argument("--ion", default="Li+")
    parser.add_argument("--spacing", type=float)
    parser.add_argument("--cutoff", type=float)
    parser.add_argument("--max-energy", type=float)
    parser.add_argument("--max-paths", type=int)
    args = parser.parse_args()
    if args.ion == "Xx+":
        print("unknown ion", file=sys.stderr)
        return 3
    with open(args.structure) as f:
        atoms = int(f.readline())
    os.makedirs(args.out, exist_ok=True)
    depth = {"Li+": -2.0, "Na+": -3.0, "K+": -4.0}.get(args.ion, -1.0)
    sites = [{"position": [1.0, 2.0, 3.0], "index": [2, 4, 6], "energy": depth, "escape": depth + 1.5,
              "barrier": 1.5, "direction": [1, 0, 0], "path": 0},
             {"position": [5.0, 5.0, 5.0], "index": [10, 10, 10], "energy": depth + 0.5, "escape": None,
              "barrier": None, "direction": None, "path": None},
             {"position": [7.0, 1.0, 2.0], "index": [14, 2, 4], "energy": depth + 1.0, "escape": depth + 1.2,
              "barrier": 0.2, "direction": [0, 1, 0], "path": None}]
    data = {"format": "liminal-map", "version": 0 if args.ion == "Old+" else 1, "liminal_version": "fake",
            "tier": "classical: fake", "structure": os.path.basename(args.structure), "structure_sha256": "0" * 64,
            "run_id": None, "step": None, "ion": args.ion, "units": {"energy": "kcal/mol", "length": "angstrom"},
            "grid": {"file": "energy.cube", "shape": [2, 2, 2], "spacing": [args.spacing] * 3, "lengths": [1, 1, 1],
                     "cutoff": args.cutoff, "polarisation": False, "minimum": depth, "atoms": atoms},
            "sites": sites,
            "paths": [{"site": 0, "direction": [1, 0, 0], "barrier": 1.5,
                       "bottleneck": {"position": [2.0, 2.0, 3.0], "energy": depth + 1.5},
                       "points": [[1.0, 2.0, 3.0], [2.0, 2.0, 3.0], [11.0, 2.0, 3.0]]}]}
    with open(os.path.join(args.out, "map.json"), "w") as f:
        json.dump(data, f)
    with open(os.path.join(args.out, "energy.cube"), "w") as f:
        f.write("fake\nfake\n    0 0.0 0.0 0.0\n    2 1.0 0.0 0.0\n    2 0.0 1.0 0.0\n    2 0.0 0.0 1.0\n")
        f.write("1 2 3 4 5 6\n7 8\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
