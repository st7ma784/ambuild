"""Make a few small recorded runs for the web demo (deploy/docker-compose.yml, profile web).

    python make_runs.py RUNS_DIR

Three builds with different seeds, each ending with a Poreblazer analysis, and one that
fails part way, so the demo has finished and failed runs to browse. Runs that already
exist are kept, so restarting the demo does not build them again.
"""
import os
import sys

from ambuild import ab_cell, ab_util


def build(runsDir, name, seed, fail=False):
    outputDir = os.path.join(runsDir, name)
    if os.path.isfile(os.path.join(outputDir, "run.json")):
        print("keeping", outputDir)
        return
    blocks = ab_util.blocksDir()
    try:
        with ab_cell.Cell([25, 25, 25], paramsDir=ab_util.paramsDir(), outputDir=outputDir,
                          recordRun=True, seed=seed) as cell:
            cell.libraryAddFragment(os.path.join(blocks, "ch4.car"), fragmentType="A")
            cell.libraryAddFragment(os.path.join(blocks, "benzene2.car"), fragmentType="B")
            cell.addBondType("A:a-B:a")
            cell.addBondType("B:a-B:a")
            cell.seed(6)
            cell.dump()
            cell.growBlocks(10, cellEndGroups=None, libraryEndGroups=None, maxTries=50)
            cell.zipBlocks(bondMargin=1.0, bondAngleMargin=30)
            cell.dump()
            if fail:
                raise RuntimeError("deliberate failure, to show a failed run in the demo")
            cell.writeXyz("final.xyz")
            cell.poreblazer(ab_util.poreblazerExe(), threads=2)
    except RuntimeError as exc:
        if not fail:
            raise
        print("failed as intended:", exc)
    print("made", outputDir)


def main(runsDir):
    os.makedirs(runsDir, exist_ok=True)
    for seed in (1, 2, 3):
        build(runsDir, "demo-seed-{0}".format(seed), seed)
    build(runsDir, "demo-failed", 4, fail=True)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
