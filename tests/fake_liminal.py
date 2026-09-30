"""A stand-in for liminal's `map` command, for Ambuild's tests: it writes map.json
("liminal-map" version 1, as liminal documents it) and a small energy.cube, with figures
derived from the structure's atom count and the ion, so each ion's are different.

    python fake_liminal.py map STRUCTURE --out DIR --ion ION [...]
    python fake_liminal.py conduct STRUCTURE --out conduct.json [--t-sp3 T ...]

Ion "Xx+" fails (exit code 3); ion "Old+" writes format version 0. conduct writes
"liminal-conduction" version 1 with a gap of 10 x t_sp3 (so each setting's differs);
--t-sp3 9.5 fails (exit code 4) and --t-sp3 9.0 writes version 0.
"""
import argparse
import json
import os
import sys


def conduct(args):
    if args.t_sp3 == 9.5:
        print("conduct failed", file=sys.stderr)
        return 4
    with open(args.structure) as f:
        atoms = int(f.readline())
    data = {"format": "liminal-conduction", "version": 0 if args.t_sp3 == 9.0 else 1, "liminal_version": "fake",
            "tier": "C0: fake", "structure": os.path.basename(args.structure), "run_id": None, "step": None,
            "parameters": {"t_sp3": args.t_sp3, "sp3_decay": args.sp3_decay, "max_bridge": args.max_bridge},
            "sites": atoms // 2, "sp_sites": 0, "nitrogen_sites": 0, "sp3_bridges": 4, "domains": 3,
            "largest_domain": atoms // 4, "largest_domain_fraction": 0.5, "percolating_domains": 1,
            "percolates": ["x"], "open_shell_domains": 0, "radical_domains": 0, "homo": -5 * args.t_sp3,
            "lumo": 5 * args.t_sp3, "gap": 10 * args.t_sp3, "median_domain_gap": 5.0,
            "axes": {"x": {"conductance": 0.03}, "y": {"conductance": 0.0}, "z": {"conductance": 0.0}},
            "conductance": 0.01, "conductance_min": 0.0, "conjugated_conductance": 0.0025, "tunnelling_share": 0.75,
            "log10_transmission": -17.5, "log10_transmission_min": -18.2,
            "coherent": {"axes": {"x": {"thermal": 3e-18}, "y": {"thermal": 0.0}, "z": {"thermal": 6e-19}}},
            "bridge_couplings": {"C": args.t_sp3}, "uncoupled_bridge_atoms": {},
            "flags": [a for a in sys.argv[3:] if a.startswith("--")]}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(data, f)
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "conduct":
        parser = argparse.ArgumentParser()
        parser.add_argument("command")
        parser.add_argument("structure")
        parser.add_argument("--out", required=True)
        parser.add_argument("--t-sp3", type=float, default=0.3)
        parser.add_argument("--sp3-decay", type=float, default=0.455)
        parser.add_argument("--max-bridge", type=int, default=3)
        parser.add_argument("--max-dense", type=int, default=8000)
        return conduct(parser.parse_args())
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
