"""Time HOOMD-blue calculations in-process, in one worker process and across MPI ranks.

Run inside an MPI build of HOOMD-blue (see docs/benchmarks.md, measured with HOOMD 2.9.3):

    python3 bench_hoomd.py OUT.json [--sizes 30 60 90] [--ranks 2 4 8] [--repeats 3]

For each cell size it builds one benzene cell at a fixed density and saves it, then
restores that same cell for every measurement:

    overhead   runMD, 1 step, all-atom: the fixed cost of a calculation
    md         runMD, --md-steps steps, all-atom: the part MPI can divide
    rigid-md   runMD, --md-steps steps, rigid bodies (Ambuild's default; never MPI)

each in-process, in a worker process, and (all-atom only) with mpirun -n R.
"""
import argparse
import json
import os
import random
import statistics
import sys
import time

from ambuild import ab_cell, ab_hoomdlauncher, ab_util

TESTS = os.environ.get("AMBUILD_TESTS_DIR", os.path.join(os.path.dirname(__file__), "..", "tests"))
PARAMS = os.path.join(TESTS, "params")
BLOCKS_PER_30A = 6  # benzene blocks in a 30 A cell; kept constant per volume


def buildCell(box, workdir):
    random.seed(7)
    start = time.time()
    cell = ab_cell.Cell([box] * 3, paramsDir=PARAMS, outputDir=os.path.join(workdir, "build_%d" % box))
    cell.libraryAddFragment(os.path.join(TESTS, "blocks", "benzene.car"), fragmentType="A")
    cell.addBondType("A:a-A:a")
    cell.seed(int(round(BLOCKS_PER_30A * (box / 30.0) ** 3)))
    pkl = cell.dump()
    info = {"atoms": cell.numAtoms(), "blocks": cell.numBlocks(), "build_seconds": time.time() - start}
    cell.close()
    return pkl, info


def timeCase(pkl, workdir, launcher, method, kwargs):
    """Restore the cell and time one calculation; return (seconds, ranks)"""
    os.environ.pop(ab_hoomdlauncher.LAUNCHER_ENV, None)
    if launcher is not None:
        os.environ[ab_hoomdlauncher.LAUNCHER_ENV] = launcher
    ab_hoomdlauncher.HoomdLauncher.lastResult = None
    outdir = os.path.join(workdir, "run_%d" % int(time.time() * 1e6))
    cell = ab_util.cellFromPickle(pkl, paramsDir=PARAMS, outputDir=outdir)
    start = time.time()
    getattr(cell, method)(quiet=True, **kwargs)
    seconds = time.time() - start
    cell.close()
    result = ab_hoomdlauncher.HoomdLauncher.lastResult
    return seconds, (result["ranks"] if result else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out")
    parser.add_argument("--sizes", type=float, nargs="+", default=[30, 60, 90])
    parser.add_argument("--ranks", type=int, nargs="+", default=[2, 4, 8])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--md-steps", type=int, default=2000)
    parser.add_argument("--mpirun", default="mpirun --oversubscribe -n {n}")
    parser.add_argument("--workdir", default="/tmp/bench_hoomd")
    args = parser.parse_args()
    # HOOMD-blue 2 parsed the command line in context.initialize(); harmless for 4+
    sys.argv = sys.argv[:1]

    cases = [
        ("overhead", "runMD", dict(rigidBody=False, mdCycles=1, dt=1e-9), True),
        ("md", "runMD", dict(rigidBody=False, mdCycles=args.md_steps), True),
        ("rigid-md", "runMD", dict(rigidBody=True, mdCycles=args.md_steps), False),
    ]
    modes = [("in-process", None), ("worker", "")]
    modes += [("mpi-%d" % n, args.mpirun.format(n=n)) for n in args.ranks]

    records = []
    for box in args.sizes:
        pkl, info = buildCell(box, args.workdir)
        print("box %g: %d atoms, %d blocks, built in %.1f s" % (box, info["atoms"], info["blocks"], info["build_seconds"]), flush=True)
        for case, method, kwargs, mpiOk in cases:
            for mode, launcher in modes:
                if mode.startswith("mpi") and not mpiOk:
                    continue
                times, ranks = [], None
                for _ in range(args.repeats):
                    seconds, ranks = timeCase(pkl, args.workdir, launcher, method, kwargs)
                    times.append(seconds)
                record = dict(info, box=box, case=case, mode=mode, ranks=ranks,
                              seconds=statistics.median(times), all_seconds=times)
                records.append(record)
                print("  %-9s %-10s ranks=%-2d median %.2f s  %s" % (
                    case, mode, ranks, record["seconds"], " ".join("%.2f" % t for t in times)), flush=True)
                with open(args.out, "w") as f:
                    json.dump(records, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
