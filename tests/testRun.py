"""
Tests for recording a build as a self-contained run directory
"""
import csv
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock
import uuid

from context import ab_analyse
from context import ab_cell
from context import ab_util
from context import BLOCKS_DIR, PARAMS_DIR

from ambuild import ab_run


def readEvents(rundir):
    with open(os.path.join(rundir, ab_run.EVENTS_FILE)) as f:
        return [json.loads(line) for line in f]


def readRun(rundir):
    with open(os.path.join(rundir, ab_run.RUN_FILE)) as f:
        return json.load(f)


class Test(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.rundir = os.path.join(self.tmpdir, "run")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def recordBuild(self, runId=None):
        """Record a small build; return the cell"""
        ch4 = os.path.join(BLOCKS_DIR, "ch4.car")
        with ab_cell.Cell(
            [20, 20, 20],
            paramsDir=PARAMS_DIR,
            outputDir=self.rundir,
            recordRun=True,
            runId=runId,
        ) as cell:
            cell.libraryAddFragment(filename=ch4, fragmentType="A")
            cell.libraryAddFragment(filename=ch4, fragmentType="B")
            cell.addBondType("A:a-B:a")
            cell.seed(3)
            cell.growBlocks(3)
            cell.dump()
            cell.writeXyz("final.xyz")
        return cell

    def testRunJson(self):
        cell = self.recordBuild()
        run = readRun(self.rundir)

        self.assertEqual(run["schema_version"], ab_run.SCHEMA_VERSION)
        self.assertEqual(run["run_id"], cell.runId)
        uuid.UUID(run["run_id"])
        self.assertEqual(run["status"], "finished")
        self.assertIsNone(run["error"])
        self.assertIsNotNone(run["finished"])
        self.assertEqual(run["cell"]["box_dim"], [20.0, 20.0, 20.0])
        self.assertIsNotNone(run["environment"]["python"])

        params = [i for i in run["inputs"] if i["kind"] == "params"]
        self.assertEqual(
            sorted(os.path.basename(i["path"]) for i in params), sorted(os.listdir(PARAMS_DIR))
        )
        blocks = [i for i in run["inputs"] if i["kind"] == "blocks"]
        self.assertEqual(
            [(i["fragmentType"], i["path"]) for i in blocks],
            [
                ("A", "inputs/blocks/ch4.car"),
                ("A", "inputs/blocks/ch4.csv"),
                ("B", "inputs/blocks/ch4.car"),
                ("B", "inputs/blocks/ch4.csv"),
            ],
        )
        for entry in run["inputs"]:
            with open(os.path.join(self.rundir, entry["path"]), "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), entry["sha256"])

    def testRunId(self):
        runId = str(uuid.uuid4())
        cell = self.recordBuild(runId=runId)
        self.assertEqual(cell.runId, runId)
        self.assertEqual(readRun(self.rundir)["run_id"], runId)

    def testEvents(self):
        self.recordBuild()
        events = readEvents(self.rundir)
        types = [e["type"] for e in events]

        self.assertEqual(types[0], ab_run.RUN_STARTED)
        self.assertEqual(types[-1], ab_run.RUN_FINISHED)
        self.assertEqual(events[-1]["data"], {"status": "finished", "error": None})
        self.assertIn(ab_run.INPUT, types)
        self.assertIn(ab_analyse.STEP, types)
        artifacts = [e["data"] for e in events if e["type"] == ab_analyse.ARTIFACT]
        self.assertEqual([a["relpath"] for a in artifacts], ["step_1.pkl.gz", "step_1.xyz", "final.xyz"])
        self.assertEqual([a["kind"] for a in artifacts], ["pickle", "structure", "xyz"])

        # The step events match the csv file
        with open(os.path.join(self.rundir, "ambuild.csv"), newline="") as f:
            nrows = len(list(csv.DictReader(f)))
        self.assertEqual(nrows, types.count(ab_analyse.STEP))

    def testFailedRun(self):
        with self.assertRaises(ValueError):
            with ab_cell.Cell(
                [20, 20, 20], paramsDir=PARAMS_DIR, outputDir=self.rundir, recordRun=True
            ):
                raise ValueError("boom")
        run = readRun(self.rundir)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error"], "ValueError: boom")
        self.assertEqual(readEvents(self.rundir)[-1]["data"]["status"], "failed")

    def testNeedsOutputDir(self):
        with self.assertRaises(ValueError):
            ab_cell.Cell([20, 20, 20], paramsDir=PARAMS_DIR, recordRun=True)

    def testRefusesExistingRun(self):
        self.recordBuild()
        with self.assertRaises(RuntimeError):
            ab_cell.Cell([20, 20, 20], paramsDir=PARAMS_DIR, outputDir=self.rundir, recordRun=True)

    def testChildRunFromPickle(self):
        """A cell restored from a pickle can be recorded as a child of the original run"""
        parent = self.recordBuild()
        pkl = os.path.join(self.rundir, "step_1.pkl.gz")
        childDir = os.path.join(self.tmpdir, "child")
        cell = ab_util.cellFromPickle(pkl, paramsDir=PARAMS_DIR, outputDir=childDir)
        self.assertEqual(cell.runId, parent.runId)  # Restored from the pickle
        childId = cell.startRecording()
        with self.assertRaises(RuntimeError):
            cell.startRecording()
        cell.growBlocks(1)
        cell.close()

        run = readRun(childDir)
        self.assertEqual(run["run_id"], childId)
        self.assertNotEqual(childId, parent.runId)
        self.assertEqual(run["parent_run_id"], parent.runId)
        self.assertEqual(run["status"], "finished")
        self.assertIsNone(readRun(self.rundir)["parent_run_id"])

    def testSlurmVariables(self):
        slurm = {"SLURM_JOB_ID": "1234", "SLURM_ARRAY_TASK_ID": "7", "SLURM_CLUSTER_NAME": "hpc"}
        with mock.patch.dict(os.environ, slurm):
            self.recordBuild()
        scheduler = readRun(self.rundir)["scheduler"]
        self.assertEqual(scheduler["type"], "slurm")
        self.assertEqual(scheduler["variables"], slurm)

    def testNoScheduler(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("SLURM_JOB_ID", None)
            self.recordBuild()
        self.assertIsNone(readRun(self.rundir)["scheduler"])

    def testReconstructFromRunDirectory(self):
        """Everything needed to restore the build is in the run directory"""
        self.recordBuild()
        moved = os.path.join(self.tmpdir, "moved")
        shutil.copytree(self.rundir, moved)
        shutil.rmtree(self.rundir)

        run = readRun(moved)
        events = readEvents(moved)
        paramsDir = os.path.join(moved, "inputs", "params")
        pickles = [
            e["data"]["relpath"]
            for e in events
            if e["type"] == ab_analyse.ARTIFACT and e["data"]["kind"] == "pickle"
        ]
        restartDir = os.path.join(self.tmpdir, "restart")
        cell = ab_util.cellFromPickle(
            os.path.join(moved, pickles[-1]), paramsDir=paramsDir, outputDir=restartDir
        )
        cell.close()
        lastStep = [e["data"] for e in events if e["type"] == ab_analyse.STEP][-1]
        self.assertEqual(cell.numBlocks(), lastStep["num_blocks"])
        self.assertEqual(cell.outputDir, restartDir)
        self.assertTrue(os.path.isfile(os.path.join(restartDir, "ambuild_1.csv")))
        self.assertFalse(os.path.exists(self.rundir))

        # The copied building blocks load into a new cell
        fresh = ab_cell.Cell(run["cell"]["box_dim"], paramsDir=paramsDir)
        for entry in run["inputs"]:
            if entry["path"].endswith(".car") and entry["fragmentType"] == "A":
                fresh.libraryAddFragment(os.path.join(moved, entry["path"]), "A")
        fresh.close()
        self.assertEqual(fresh.fragmentTypes(), {})


if __name__ == "__main__":
    unittest.main()
