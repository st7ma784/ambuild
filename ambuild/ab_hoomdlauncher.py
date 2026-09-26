"""Run HOOMD-blue calculations in separate processes, e.g. across the MPI tasks of a Slurm job.

Ambuild itself stays a single process: its building steps make random choices that
would differ between MPI ranks. Each optimisation or MD run is instead handed to
ambuild.hoomd_worker, started with the command in AMBUILD_HOOMD_LAUNCHER:

    AMBUILD_HOOMD_LAUNCHER unset          HOOMD runs in this process (the default)
    AMBUILD_HOOMD_LAUNCHER=""             HOOMD runs in one separate process
    AMBUILD_HOOMD_LAUNCHER="srun --ntasks=4"   ...in 4 MPI ranks of the Slurm job
    AMBUILD_HOOMD_LAUNCHER="mpirun -n 4"       ...in 4 MPI ranks outside Slurm

The worker's HOOMD build must support MPI for more than one rank (conda-forge's
does not). Rigid-body calculations (Ambuild's default) run in a single worker process
without the launcher: HOOMD-blue 2's domain decomposition failed for Ambuild's bonded
rigid bodies ("Error during communication", "Error in bond calculation") and this is
untested with HOOMD-blue 4+, while all-atom calculations (rigidBody=False) run across
the ranks. This module does not import hoomd.
"""
import logging
import os
import pickle
import shlex
import shutil
import subprocess
import sys
import tempfile

import numpy as np

from ambuild import xyz_core

logger = logging.getLogger(__name__)

LAUNCHER_ENV = "AMBUILD_HOOMD_LAUNCHER"


def launcherFromEnvironment():
    """Return the launcher command as a list, or None when HOOMD should run in-process"""
    value = os.environ.get(LAUNCHER_ENV)
    if value is None:
        return None
    return shlex.split(value)


def applyResult(cell, result):
    """Update the cell's coordinates and box from a HOOMD result.

    result is the dict from an engine's snapshotResult(): box [Lx, Ly, Lz], positions and
    images of every particle, and offset, the number of rigid-body centre particles
    that precede the atoms.
    """
    box = np.array(result["box"])
    positions = result["positions"]
    images = result["images"]
    atomIdx = result["offset"]
    for block in cell.blocks.values():
        for i in range(block.numAtoms()):
            coord = xyz_core.unWrapCoord3(positions[atomIdx], images[atomIdx], box, centered=True)
            block.coord(i, coord)
            atomIdx += 1
    if atomIdx != len(positions):
        raise RuntimeError(
            "Read {0} positions but there were {1} particles!".format(atomIdx, len(positions))
        )
    # If we are running (e.g.) an NPT simulation, the cell size may have changed. In this case we need to update
    # our cell parameters. Repopulate cells will then update the halo cells and add the new blocks
    if not np.allclose(box, cell.dim):
        logger.info(
            "Changing cell dimensions after HOOMD-blue simulation from: {0} to: {1}".format(
                cell.dim, box
            )
        )
        cell.dim = box
    # Now have the new coordinates, so we need to put the atoms in their new cells
    cell.repopulateCells()
    return


class HoomdLauncher:
    """MD engine (see ab_mdengine) that runs each calculation in a worker process"""

    # The most recent result, for diagnostics and tests (e.g. result["ranks"])
    lastResult = None

    def __init__(self, paramsDir, outputDir=None, launcher=None):
        self.paramsDir = paramsDir
        self.outputDir = outputDir
        if launcher is None:
            launcher = launcherFromEnvironment() or []
        self.launcher = launcher
        self.rCut = 5.0  # The engines' default; Cell.setRcut may change it
        self._result = None

    def optimiseGeometry(self, data, **kw):
        return self._run("optimiseGeometry", data, kw)

    def runMD(self, data, **kw):
        return self._run("runMD", data, kw)

    def updateCell(self, cell):
        if self._result is None:
            raise RuntimeError("No HOOMD result to update the cell from")
        applyResult(cell, self._result)

    def _run(self, method, data, kw):
        d = kw.pop("d", None)
        workdir = tempfile.mkdtemp(prefix="hoomd_job_", dir=self.outputDir or os.getcwd())
        jobFile = os.path.join(workdir, "job.pkl")
        resultFile = os.path.join(workdir, "result.pkl")
        job = {
            "method": method,
            "paramsDir": os.path.abspath(self.paramsDir),
            "outputDir": self.outputDir,
            "rCut": self.rCut,
            "data": data,
            "kwargs": kw,
        }
        with open(jobFile, "wb") as f:
            pickle.dump(job, f)
        launcher = self.launcher
        if launcher and kw.get("rigidBody"):
            logger.warning(
                "Running rigid-body %s in one process: MPI domain decomposition of "
                "Ambuild's bonded rigid bodies is unsupported (launcher %s ignored)",
                method,
                " ".join(launcher),
            )
            launcher = []
        cmd = launcher + [sys.executable, "-m", "ambuild.hoomd_worker", jobFile, resultFile]
        logger.info("Running HOOMD-blue %s: %s", method, " ".join(cmd))
        # Importing an MPI build of hoomd in this process starts an MPI singleton, which sets
        # OMPI_*/PMIX_* variables at the C level. Children inherit those and each rank then
        # runs as its own singleton, so launch the worker with os.environ, which Python
        # captured before they were set.
        returncode = subprocess.call(cmd, env=dict(os.environ))
        if returncode != 0 or not os.path.isfile(resultFile):
            raise RuntimeError(
                "HOOMD-blue worker failed with return code {0}; job files are in {1}".format(
                    returncode, workdir
                )
            )
        with open(resultFile, "rb") as f:
            self._result = pickle.load(f)
        HoomdLauncher.lastResult = self._result
        shutil.rmtree(workdir)
        logger.info("HOOMD-blue %s ran on %d MPI rank(s)", method, self._result["ranks"])
        if d is not None:
            d.update(self._result["d"])
        return self._result["ok"]
