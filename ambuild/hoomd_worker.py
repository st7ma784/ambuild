"""Run one HOOMD-blue calculation for ab_hoomdlauncher.HoomdLauncher.

    python -m ambuild.hoomd_worker JOB_FILE RESULT_FILE

Every MPI rank runs this module; HOOMD-blue decomposes the calculation across them.
Rank 0 writes the result.
"""
import os
import pickle
import sys


def main(argv):
    if len(argv) != 2:
        sys.stderr.write(__doc__)
        return 2
    jobFile, resultFile = argv
    # hoomd.context.initialize() parses the command line, so leave it nothing to parse
    sys.argv = sys.argv[:1]

    with open(jobFile, "rb") as f:
        job = pickle.load(f)

    import hoomd
    from ambuild.hoomd2 import Hoomd2

    engine = Hoomd2(job["paramsDir"], outputDir=job["outputDir"])
    engine.rCut = job["rCut"]
    d = {}
    ok = getattr(engine, job["method"])(job["data"], d=d, **job["kwargs"])
    result = engine.snapshotResult()  # Collective: every rank must call it
    if hoomd.comm.get_rank() == 0:
        result.update(ok=ok, d=d, ranks=hoomd.comm.get_num_ranks())
        tmp = resultFile + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(result, f)
        os.replace(tmp, resultFile)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
