"""Time Poreblazer on fixed cells, to compare builds with different compiler flags.

    python3 bench_poreblazer.py prepare PICKLE_DIR [--sizes 20 30 40]
    python3 bench_poreblazer.py run PICKLE_DIR LABEL OUT.json [--repeats 3]

prepare builds one benzene cell per size (fixed density) and saves it; run restores
each saved cell and times Cell.poreblazer() with $POREBLAZER_EXE, so every build of
Poreblazer sees the same structures. Results are appended to OUT.json.
"""
import argparse
import glob
import json
import os
import random
import statistics
import sys
import time

from ambuild import ab_cell, ab_util

TESTS = os.environ.get("AMBUILD_TESTS_DIR", os.path.join(os.path.dirname(__file__), "..", "tests"))
PARAMS = os.path.join(TESTS, "params")
RESULT_KEYS = ["surface_area_m2_g", "pore_limiting_diameter_A", "maximum_pore_diameter_A",
               "helium_volume_cm3_g", "geometric_volume_cm3_g"]


def prepare(args):
    os.makedirs(args.pickle_dir, exist_ok=True)
    for box in args.sizes:
        random.seed(11)
        cell = ab_cell.Cell([box] * 3, paramsDir=PARAMS, outputDir=os.path.join(args.pickle_dir, "build_%d" % box))
        cell.libraryAddFragment(os.path.join(TESTS, "blocks", "benzene.car"), fragmentType="A")
        cell.addBondType("A:a-A:a")
        cell.seed(int(round(6 * (box / 30.0) ** 3)))
        pkl = cell.dump()
        os.replace(pkl, os.path.join(args.pickle_dir, "cell_%03d.pkl.gz" % box))
        print("box %g: %d atoms" % (box, cell.numAtoms()), flush=True)
        cell.close()


def run(args):
    exe = os.environ["POREBLAZER_EXE"]
    records = []
    if os.path.isfile(args.out):
        with open(args.out) as f:
            records = json.load(f)
    for pkl in sorted(glob.glob(os.path.join(args.pickle_dir, "cell_*.pkl.gz"))):
        box = int(os.path.basename(pkl)[5:8])
        times, results = [], None
        for i in range(args.repeats):
            outdir = os.path.join("/tmp/bench_pb", args.label, "%d_%d" % (box, i))
            cell = ab_util.cellFromPickle(pkl, paramsDir=PARAMS, outputDir=outdir)
            start = time.time()
            results = cell.poreblazer(exe)
            times.append(time.time() - start)
            atoms = cell.numAtoms()
            cell.close()
        record = {"label": args.label, "box": box, "atoms": atoms, "seconds": statistics.median(times),
                  "all_seconds": times, "returncode": results["returncode"]}
        record.update({k: results[k] for k in RESULT_KEYS})
        records.append(record)
        print("%-12s box %3d  %5d atoms  median %7.2f s  SA %s m2/g  PLD %s A" % (
            args.label, box, atoms, record["seconds"], results["surface_area_m2_g"],
            results["pore_limiting_diameter_A"]), flush=True)
        with open(args.out, "w") as f:
            json.dump(records, f, indent=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("prepare")
    p.add_argument("pickle_dir")
    p.add_argument("--sizes", type=int, nargs="+", default=[20, 30, 40])
    r = sub.add_parser("run")
    r.add_argument("pickle_dir")
    r.add_argument("label")
    r.add_argument("out")
    r.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args)
    elif args.command == "run":
        run(args)
    else:
        parser.error("choose prepare or run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
