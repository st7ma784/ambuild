"""Check a pickled cell with xTB as a child run of the build that wrote it
(docs/xtb-spec.md).

    python xtb_task.py PICKLE CHILD_RUN_DIR PARAMS_DIR

The settings are the stage's (Cell.xtb), from the environment: AMBUILD_XTB_METHOD (default
gfn1), AMBUILD_XTB_MODE (single_point), AMBUILD_XTB_MAX_STEPS, AMBUILD_XTB_FMAX,
AMBUILD_XTB_MAX_ATOMS and AMBUILD_XTB_CHARGE; AMBUILD_XTB_POREBLAZER=1 (with mode relax and
POREBLAZER_EXE) compares Poreblazer's figures before and after. XTB_WORKER names the worker. The child run's
run.json records the build's run id as parent_run_id. The worker gets the task's CPUs as
threads, and when Slurm gives the task a memory limit, a cell too large for it fails at once
instead of being killed part way through.
"""
import os
import sys

from ambuild import ab_util

from poreblazer_task import slurmMemoryMb

SETTINGS = {"AMBUILD_XTB_METHOD": ("method", str), "AMBUILD_XTB_MODE": ("mode", str),
            "AMBUILD_XTB_MAX_STEPS": ("max_steps", int), "AMBUILD_XTB_FMAX": ("fmax", float),
            "AMBUILD_XTB_MAX_ATOMS": ("max_atoms", int), "AMBUILD_XTB_CHARGE": ("charge", int),
            "AMBUILD_XTB_POREBLAZER": ("poreblazer", lambda v: v.lower() in ("1", "true", "yes"))}


def settings(environ=os.environ):
    """Cell.xtb's keyword arguments from the environment; unset or empty variables are left out"""
    return {name: convert(environ[variable]) for variable, (name, convert) in SETTINGS.items()
            if environ.get(variable)}


def main(pickle, childDir, paramsDir):
    cell = ab_util.cellFromPickle(pickle, paramsDir=paramsDir, outputDir=childDir)
    cpus = os.environ.get("SLURM_CPUS_PER_TASK")
    with cell:
        cell.startRecording()
        result = cell.xtb(threads=int(cpus) if cpus else None, memory_limit_mb=slurmMemoryMb(), **settings())
    return 0 if result["returncode"] == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
