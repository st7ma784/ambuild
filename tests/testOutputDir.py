"""
Tests for writing all of a cell's output into a separate directory
"""
import csv
import os
import shutil
import tempfile
import unittest

from context import ab_cell
from context import ab_util
from context import BLOCKS_DIR, PARAMS_DIR


class Test(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmpdir)

    def makeCell(self, outputDir):
        mycell = ab_cell.Cell([20, 20, 20], paramsDir=PARAMS_DIR, outputDir=outputDir)
        mycell.libraryAddFragment(filename=os.path.join(BLOCKS_DIR, "ch4.car"), fragmentType="A")
        mycell.addBondType("A:a-A:a")
        return mycell

    def csvRows(self, path):
        with open(path) as f:
            return list(csv.DictReader(f))

    def testFilesWrittenToOutputDir(self):
        rundir = os.path.join(self.tmpdir, "run")
        mycell = self.makeCell(rundir)
        mycell.seed(3)
        pkl = mycell.dump()
        mycell.writeXyz("cell.xyz")
        mycell.writeCml("cell.cml")
        mycell.close()

        self.assertEqual(os.path.dirname(pkl), rundir)
        for name in ["ambuild.log", "ambuild.csv", "step_1.pkl.gz", "cell.xyz", "cell.cml"]:
            self.assertTrue(os.path.isfile(os.path.join(rundir, name)), name)
        self.assertEqual(self.csvRows(mycell.logcsv)[-1]["type"], "seed")
        self.assertEqual(os.getcwd(), self.cwd)

    def testTwoCellsSeparateDirs(self):
        cell1 = self.makeCell(os.path.join(self.tmpdir, "run1"))
        cell2 = self.makeCell(os.path.join(self.tmpdir, "run2"))
        cell1.seed(2)
        cell2.seed(3)
        cell2.growBlocks(1)
        cell1.close()
        cell2.close()

        self.assertNotEqual(cell1.logcsv, cell2.logcsv)
        self.assertEqual({r["type"] for r in self.csvRows(cell1.logcsv)}, {"seed"})
        self.assertEqual({r["type"] for r in self.csvRows(cell2.logcsv)}, {"seed", "grow"})

    def testPickleRestoresOutputDir(self):
        rundir = os.path.join(self.tmpdir, "run")
        mycell = self.makeCell(rundir)
        mycell.seed(2)
        pkl = mycell.dump()
        mycell.close()

        restored = ab_util.cellFromPickle(pkl, paramsDir=PARAMS_DIR)
        restored.growBlocks(1)
        restored.close()

        self.assertEqual(restored.outputDir, rundir)
        self.assertEqual(restored.logcsv, os.path.join(rundir, "ambuild_1.csv"))
        self.assertEqual(restored.logfile, os.path.join(rundir, "ambuild_1.log"))
        self.assertEqual(self.csvRows(restored.logcsv)[-1]["type"], "grow")

    @unittest.skipUnless(ab_util.HOOMDVERSION is not None, "Need HOOMD-BLUE to run")
    def testHoomdOutputInOutputDir(self):
        rundir = os.path.join(self.tmpdir, "run")
        mycell = self.makeCell(rundir)
        mycell.seed(2, center=True, random=False)
        mycell.growBlocks(2, endGroupType=None, maxTries=1, random=False)
        mycell.optimiseGeometry(rigidBody=True, optCycles=10)
        mycell.runMD(rigidBody=True, mdCycles=10)
        mycell.close()

        self.assertEqual(os.getcwd(), self.cwd)
        for name in ["geomopt.tsv", "runmd.log"]:
            self.assertTrue(os.path.isfile(os.path.join(rundir, name)), name)

    def testDefaultIsWorkingDirectory(self):
        os.chdir(self.tmpdir)
        mycell = self.makeCell(None)
        mycell.writeXyz("cell.xyz")
        mycell.close()

        self.assertIsNone(mycell.outputDir)
        self.assertEqual(mycell.logcsv, "ambuild.csv")
        for name in ["ambuild.log", "ambuild.csv", "cell.xyz"]:
            self.assertTrue(os.path.isfile(os.path.join(self.tmpdir, name)), name)


if __name__ == "__main__":
    unittest.main()
