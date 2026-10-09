"""Print the memory (MB) to request for an xTB task on a pickled cell.

    python xtb_memory.py PICKLE PARAMS_DIR

The worker's estimate for the cell's atoms (ambuild.xtb.memoryEstimateMb) with 20% to
spare, rounded up to a whole MB. It is measured up to 944 atoms and extrapolated beyond
(docs/xtb-spec.md, Scaling), hence the margin. A build's later pickles hold more atoms, so
the fan-out job asks for the last one's.
"""
import math
import shutil
import sys
import tempfile

from ambuild import ab_util, xtb


def main(pickle, paramsDir):
    scratch = tempfile.mkdtemp()  # the restored cell's log, not the build's
    try:
        cell = ab_util.cellFromPickle(pickle, paramsDir=paramsDir, outputDir=scratch)
        atoms = len(cell._exportAtoms()[0])
        cell.close()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(int(math.ceil(1.2 * xtb.memoryEstimateMb(atoms))))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
