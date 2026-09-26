"""Print the memory (MB) to request for a Poreblazer task on a pickled cell.

    python poreblazer_memory.py PICKLE PARAMS_DIR

The estimate for Ambuild's Poreblazer fork at the default settings
(ab_poreblazer.memory_estimate_mb), with 10% to spare and room for the Python
process that runs beside Poreblazer, rounded up to a whole MB. Every pickle of a
build has the same cell, so the fan-out job asks for one.
"""
import math
import shutil
import sys
import tempfile

from ambuild import ab_poreblazer, ab_util

from poreblazer_task import PYTHON_MB


def main(pickle, paramsDir):
    scratch = tempfile.mkdtemp()  # the restored cell's log, not the build's
    try:
        cell = ab_util.cellFromPickle(pickle, paramsDir=paramsDir, outputDir=scratch)
        A, B, C = cell.dim[0], cell.dim[1], cell.dim[2]
        cell.close()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(int(math.ceil(1.1 * ab_poreblazer.memory_estimate_mb(A, B, C) + PYTHON_MB)))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
