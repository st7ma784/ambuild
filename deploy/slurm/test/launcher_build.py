"""A recorded two-task build that checks the HOOMD-blue launcher set by ambuild_build.sbatch.

The test image has no HOOMD-blue, so instead of a HOOMD worker this runs a command through
the launcher and checks that it starts one process per Slurm task.
"""
import os
import subprocess
import sys

from ambuild import ab_cell, ab_hoomdlauncher

with ab_cell.Cell([20, 20, 20], paramsDir=os.environ["AMBUILD_PARAMS_DIR"],
                  outputDir=os.environ["AMBUILD_RUN_DIR"], recordRun=True,
                  runId=os.environ["AMBUILD_RUN_ID"]):
    launcher = ab_hoomdlauncher.launcherFromEnvironment()
    assert launcher == ["srun", "--ntasks=2"], launcher
    out = subprocess.check_output(
        launcher + [sys.executable, "-c", "import os; print(os.environ['SLURM_PROCID'])"]
    )
    ranks = sorted(int(r) for r in out.split())
    assert ranks == [0, 1], ranks
    print("launcher started ranks", ranks)
