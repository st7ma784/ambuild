"""
Tests for running HOOMD-blue calculations in worker processes (ab_hoomdlauncher)
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

from context import ab_cell
from context import ab_util
from context import BLOCKS_DIR, PARAMS_DIR

from ambuild import ab_hoomdlauncher

# A stand-in for "python -m ambuild.hoomd_worker JOB RESULT": the launcher runs it with
# those arguments, and it writes the result passed to it as the "expected" option.
FAKE_WORKER = """
import pickle, sys
import numpy as np
job_file, result_file = sys.argv[-2:]
job = pickle.load(open(job_file, "rb"))
assert sys.argv[2:5] == ["-m", "ambuild.hoomd_worker", job_file], sys.argv
if job["kwargs"].get("fail"):
    sys.exit(3)
result = job["kwargs"]["expected"]
result.update(ok=True, d={"potential_energy": -1.5, "method": job["method"]}, ranks=2)
pickle.dump(result, open(result_file, "wb"))
"""


def cellResult(cell, shift=(0.0, 0.0, 0.0)):
    """The result HOOMD would return for the cell's coordinates moved by shift"""
    box = np.array(cell.dim, dtype=float)
    coords = allCoords(cell) + np.array(shift)
    images = np.floor(coords / box)
    return {
        "box": list(box),
        "positions": coords - images * box - box / 2,
        "images": images.astype(int),
        "offset": 0,
    }


def allCoords(cell):
    return np.array([block.coord(i) for block in cell.blocks.values() for i in range(block.numAtoms())])


class Test(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cell = ab_cell.Cell([20, 20, 20], paramsDir=PARAMS_DIR, outputDir=self.tmpdir)
        self.cell.libraryAddFragment(os.path.join(BLOCKS_DIR, "benzene.car"), fragmentType="A")
        self.cell.addBondType("A:a-A:a")
        self.cell.seed(3)

    def tearDown(self):
        self.cell.close()
        shutil.rmtree(self.tmpdir)

    def testLauncherFromEnvironment(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(ab_hoomdlauncher.LAUNCHER_ENV, None)
            self.assertIsNone(ab_hoomdlauncher.launcherFromEnvironment())
            os.environ[ab_hoomdlauncher.LAUNCHER_ENV] = ""
            self.assertEqual(ab_hoomdlauncher.launcherFromEnvironment(), [])
            os.environ[ab_hoomdlauncher.LAUNCHER_ENV] = "srun --ntasks=4 --mpi=pmix"
            self.assertEqual(
                ab_hoomdlauncher.launcherFromEnvironment(), ["srun", "--ntasks=4", "--mpi=pmix"]
            )

    def testApplyResult(self):
        before = allCoords(self.cell)
        result = cellResult(self.cell)
        ab_hoomdlauncher.applyResult(self.cell, result)
        np.testing.assert_allclose(allCoords(self.cell), before, atol=1e-9)

        ab_hoomdlauncher.applyResult(self.cell, cellResult(self.cell, shift=(0.0, 0.5, 0.0)))
        np.testing.assert_allclose(allCoords(self.cell), before + [0.0, 0.5, 0.0], atol=1e-9)

    def testApplyResultChecksParticleCount(self):
        result = cellResult(self.cell)
        result["positions"] = np.vstack([result["positions"], [[0.0, 0.0, 0.0]]])
        result["images"] = np.vstack([result["images"], [[0, 0, 0]]])
        with self.assertRaises(RuntimeError):
            ab_hoomdlauncher.applyResult(self.cell, result)

    def fakeEngine(self):
        worker = os.path.join(self.tmpdir, "fake_worker.py")
        with open(worker, "w") as f:
            f.write(FAKE_WORKER)
        # The launcher prefixes the worker command: run the fake with that command as arguments
        return ab_hoomdlauncher.HoomdLauncher(
            PARAMS_DIR, outputDir=self.tmpdir, launcher=[sys.executable, worker]
        )

    def testLauncherRoundTrip(self):
        engine = self.fakeEngine()
        before = allCoords(self.cell)
        d = {}
        ok = engine.optimiseGeometry(
            "cell data", d=d, expected=cellResult(self.cell, shift=(0.25, 0.0, 0.0))
        )
        engine.updateCell(self.cell)

        self.assertTrue(ok)
        self.assertEqual(d, {"potential_energy": -1.5, "method": "optimiseGeometry"})
        np.testing.assert_allclose(allCoords(self.cell), before + [0.25, 0.0, 0.0], atol=1e-9)
        self.assertEqual([n for n in os.listdir(self.tmpdir) if n.startswith("hoomd_job_")], [])

    def testLauncherFailureKeepsJobFiles(self):
        engine = self.fakeEngine()
        with self.assertRaises(RuntimeError):
            engine.runMD("cell data", fail=True)
        jobs = [n for n in os.listdir(self.tmpdir) if n.startswith("hoomd_job_")]
        self.assertEqual(len(jobs), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.tmpdir, jobs[0], "job.pkl")))

    def testRigidBodyRunsWithoutLauncher(self):
        """Rigid-body calculations skip the MPI launcher; all-atom ones use it"""
        engine = ab_hoomdlauncher.HoomdLauncher(
            PARAMS_DIR, outputDir=self.tmpdir, launcher=["srun", "--ntasks=4"]
        )
        commands = []

        def fakeCall(cmd, env=None):
            commands.append(cmd)
            return 1  # No result file: the engine raises, which is fine here

        with mock.patch.object(ab_hoomdlauncher.subprocess, "call", fakeCall):
            for rigidBody in (True, False):
                with self.assertRaises(RuntimeError):
                    engine.optimiseGeometry("cell data", rigidBody=rigidBody)
        self.assertEqual(commands[0][:2], [sys.executable, "-m"])
        self.assertEqual(commands[1][:3], ["srun", "--ntasks=4", sys.executable])

    def testCellUsesLauncherFromEnvironment(self):
        with mock.patch.dict(os.environ, {ab_hoomdlauncher.LAUNCHER_ENV: "mpirun -n 2"}):
            self.cell.setMdEngineCls([2, 9, 3])
        self.assertIs(self.cell.mdEngineCls.func, ab_hoomdlauncher.HoomdLauncher)
        # The launcher is fixed when the engine is chosen, not when a calculation runs
        engine = self.cell.mdEngineCls(PARAMS_DIR, outputDir=self.tmpdir)
        self.assertEqual(engine.launcher, ["mpirun", "-n", "2"])
        # Without HOOMD in this process there is no engine, launcher or not
        self.cell.mdEngineCls = None
        with mock.patch.dict(os.environ, {ab_hoomdlauncher.LAUNCHER_ENV: "mpirun -n 2"}):
            self.cell.setMdEngineCls(None)
        self.assertIsNone(self.cell.mdEngineCls)


@unittest.skipUnless(ab_util.HOOMDVERSION is not None, "Need HOOMD-BLUE to run")
class TestWithHoomd(unittest.TestCase):
    """A worker gives the same answer as running HOOMD in this process.

    AMBUILD_TEST_HOOMD_LAUNCHER sets the launcher to test (default "", one separate
    process), e.g. "mpirun -n 2" with an MPI build of HOOMD; AMBUILD_TEST_HOOMD_RANKS is
    the number of MPI ranks the worker must report (default 1).
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def restored(self, pkl, name, launcher):
        env = {} if launcher is None else {ab_hoomdlauncher.LAUNCHER_ENV: launcher}
        with mock.patch.dict(os.environ, env):
            if launcher is None:
                os.environ.pop(ab_hoomdlauncher.LAUNCHER_ENV, None)
            cell = ab_util.cellFromPickle(
                pkl, paramsDir=PARAMS_DIR, outputDir=os.path.join(self.tmpdir, name)
            )
            engine = cell.mdEngineCls
        return cell, engine

    def testWorkerMatchesInProcess(self):
        launcher = os.environ.get("AMBUILD_TEST_HOOMD_LAUNCHER", "")
        cell = ab_cell.Cell([30, 30, 30], paramsDir=PARAMS_DIR, outputDir=os.path.join(self.tmpdir, "build"))
        cell.libraryAddFragment(os.path.join(BLOCKS_DIR, "benzene.car"), fragmentType="A")
        cell.addBondType("A:a-A:a")
        cell.seed(6)
        cell.growBlocks(4)
        pkl = cell.dump()
        cell.close()

        local, localEngine = self.restored(pkl, "local", None)
        worker, workerEngine = self.restored(pkl, "worker", launcher)
        self.assertIs(workerEngine.func, ab_hoomdlauncher.HoomdLauncher)
        self.assertIsNot(getattr(localEngine, "func", localEngine), ab_hoomdlauncher.HoomdLauncher)
        before = allCoords(local)
        np.testing.assert_allclose(allCoords(worker), before)

        # The energy of one configuration is the same however it is decomposed (a single
        # step at a negligible timestep, so the atoms do not move)
        ranks = int(os.environ.get("AMBUILD_TEST_HOOMD_RANKS", "1"))
        static = dict(rigidBody=False, mdCycles=1, dt=1e-9, quiet=True)
        local.runMD(**static)
        worker.runMD(**static)
        self.assertEqual(ab_hoomdlauncher.HoomdLauncher.lastResult["ranks"], ranks)
        localEnergy = local.analyse.last["potential_energy"]
        self.assertAlmostEqual(
            worker.analyse.last["potential_energy"], localEnergy, delta=1e-6 * abs(localEnergy)
        )

        # All-atom, the mode HOOMD-blue 2 can decompose across MPI ranks
        kw = dict(rigidBody=False, optCycles=200, quiet=True)
        self.assertEqual(local.optimiseGeometry(**kw), worker.optimiseGeometry(**kw))
        self.assertLess(worker.analyse.last["potential_energy"], localEnergy)
        mdkw = dict(rigidBody=False, mdCycles=100, quiet=True)
        local.runMD(**mdkw)
        worker.runMD(**mdkw)
        self.assertEqual(ab_hoomdlauncher.HoomdLauncher.lastResult["ranks"], ranks)
        if ranks == 1:
            # One process reproduces the in-process run exactly. Across MPI ranks the order
            # of floating-point sums changes and the minimiser's trajectory diverges, so only
            # the static energy above is compared.
            np.testing.assert_allclose(allCoords(worker), allCoords(local), atol=1e-4)
        # ...and the calculation really moved the atoms
        self.assertGreater(np.abs(allCoords(worker) - before).max(), 1e-3)
        local.close()
        worker.close()


if __name__ == "__main__":
    unittest.main()
