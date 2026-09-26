"""Compare Poreblazer builds on identical structures: results, output files and speed.

    python3 compare_poreblazer.py prepare CASE_DIR
    python3 compare_poreblazer.py run CASE_DIR LABEL OUT.json [--threads 1 2 4 8]

prepare writes the structures once (Ambuild's builds differ between processes, so
every build must read the same files). run times $POREBLAZER_EXE on each with
OMP_NUM_THREADS set to each thread count, and records the parsed results and the
sha256 of psd.txt and psd_cumulative.txt, so builds and thread counts can be checked
for identical output.
"""
import argparse
import glob
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import profile_poreblazer as pp

CASES = [(20, 2), (30, 6), (30, 48), (40, 14)]  # (box A, benzene blocks)


def prepare(args):
    for box, blocks in CASES:
        case = os.path.join(args.case_dir, "box%d_blocks%d" % (box, blocks))
        xyz, atoms, added = pp.buildCell(float(box), blocks, os.path.join(case, "build"))
        shutil.copy(xyz, os.path.join(case, "ambuild.xyz"))
        with open(os.path.join(case, "case.json"), "w") as f:
            json.dump({"box": box, "blocks": added, "atoms": atoms}, f)
        print("prepared", case, atoms, "atoms", flush=True)


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest() if os.path.isfile(path) else None


def run(args):
    exe = os.environ["POREBLAZER_EXE"]
    records = []
    for case in sorted(glob.glob(os.path.join(args.case_dir, "box*"))):
        with open(os.path.join(case, "case.json")) as f:
            info = json.load(f)
        for threads in args.threads:
            os.environ["OMP_NUM_THREADS"] = str(threads)
            rundir = os.path.join("/tmp/compare_pb", args.label, os.path.basename(case), str(threads))
            shutil.rmtree(rundir, ignore_errors=True)
            os.makedirs(rundir)
            xyz = os.path.join(rundir, "input.xyz")
            shutil.copy(os.path.join(case, "ambuild.xyz"), xyz)
            r = pp.runPoreblazer(exe, xyz, float(info["box"]), 0.2, rundir, False)
            r.update(info, label=args.label, threads=threads, case=os.path.basename(case),
                     psd_sha256=sha256(os.path.join(rundir, "psd.txt")),
                     psd_cumulative_sha256=sha256(os.path.join(rundir, "psd_cumulative.txt")))
            records.append(r)
            print("%-9s %-16s threads %d  wall %7.2f s  lattice %6.2f  psd %6.2f  SA %s  PLD %s  psd %s" % (
                args.label, r["case"], threads, r["wall_seconds"], r["steps"].get("lattice", 0),
                r["steps"].get("pore_distribution", 0), r["results"]["surface_area_m2_g"],
                r["results"]["pore_limiting_diameter_A"], (r["psd_sha256"] or "-")[:10]), flush=True)
            with open(args.out, "w") as f:
                json.dump(records, f, indent=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("prepare")
    p.add_argument("case_dir")
    r = sub.add_parser("run")
    r.add_argument("case_dir")
    r.add_argument("label")
    r.add_argument("out")
    r.add_argument("--threads", type=int, nargs="+", default=[1, 2, 4, 8])
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
