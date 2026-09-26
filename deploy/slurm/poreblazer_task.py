"""Run Poreblazer on a pickled cell as a child run of the build that wrote it.

    python poreblazer_task.py PICKLE CHILD_RUN_DIR PARAMS_DIR

POREBLAZER_EXE names the executable. The child run's run.json records the
build's run id as parent_run_id. When Slurm gives the task a memory limit, a cell
too large for it fails at once instead of being killed part way through.
"""
import os
import sys

from ambuild import ab_util

PYTHON_MB = 256  # headroom for this Python process, which runs beside Poreblazer


def slurmMemoryMb():
    """The task's memory (MB) from Slurm's environment, or None"""
    if os.environ.get("SLURM_MEM_PER_NODE"):
        return float(os.environ["SLURM_MEM_PER_NODE"])
    if os.environ.get("SLURM_MEM_PER_CPU"):
        return float(os.environ["SLURM_MEM_PER_CPU"]) * int(os.environ.get("SLURM_CPUS_PER_TASK", "1"))
    return None


def main(pickle, childDir, paramsDir):
    cell = ab_util.cellFromPickle(pickle, paramsDir=paramsDir, outputDir=childDir)
    memory = slurmMemoryMb()
    with cell:
        cell.startRecording()
        results = cell.poreblazer(
            os.environ["POREBLAZER_EXE"],
            memory_limit_mb=None if memory is None else memory - PYTHON_MB,
        )
    return 0 if results["returncode"] == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
