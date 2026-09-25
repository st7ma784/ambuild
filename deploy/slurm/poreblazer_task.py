"""Run Poreblazer on a pickled cell as a child run of the build that wrote it.

    python poreblazer_task.py PICKLE CHILD_RUN_DIR PARAMS_DIR

POREBLAZER_EXE names the executable. The child run's run.json records the
build's run id as parent_run_id.
"""
import os
import sys

from ambuild import ab_util


def main(pickle, childDir, paramsDir):
    cell = ab_util.cellFromPickle(pickle, paramsDir=paramsDir, outputDir=childDir)
    with cell:
        cell.startRecording()
        results = cell.poreblazer(os.environ["POREBLAZER_EXE"])
    return 0 if results["returncode"] == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
