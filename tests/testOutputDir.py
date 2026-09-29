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

    def testWriteXyzHasEveryAtom(self):
        """writeXyz writes atoms, not HOOMD rigid-body particles, whether or not HOOMD is installed"""
        mycell = self.makeCell(os.path.join(self.tmpdir, "run"))
        mycell.seed(3)
        mycell.writeXyz("cell.xyz")
        mycell.close()
        with open(os.path.join(self.tmpdir, "run", "cell.xyz")) as f:
            lines = f.read().splitlines()
        self.assertEqual(int(lines[0]), mycell.numAtoms())
        self.assertEqual(len(lines) - 2, mycell.numAtoms())
        self.assertGreater(mycell.numAtoms(), 0)

    def testWriteCar(self):
        """writeCar writes every atom with its block's label, type, symbol and charge"""
        mycell = self.makeCell(os.path.join(self.tmpdir, "run"))
        mycell.seed(3)
        mycell.writeCar("cell.car")
        mycell.writeCar("nopbc.car", periodic=False)
        mycell.writeCar("fromdata.car", data=mycell.cellData(noRigidParticles=True))
        expected = []
        for block in mycell.blocks.values():
            for i in range(block.numAtoms()):
                expected.append((block.label(i)[:5], block.type(i), block.symbol(i)))
        mycell.close()
        with open(os.path.join(self.tmpdir, "run", "cell.car")) as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[:2], ["!BIOSYM archive 3", "PBC=ON"])
        self.assertEqual(lines[4].split()[:4], ["PBC", "20.0000", "20.0000", "20.0000"])
        atoms = [l.split() for l in lines[5:] if l and l.split()[0] != "end"]
        self.assertEqual(len(atoms), mycell.numAtoms())
        self.assertEqual([(a[0], a[6], a[7]) for a in atoms], expected)
        with open(os.path.join(self.tmpdir, "run", "nopbc.car")) as f:
            nopbc = f.read().splitlines()
        self.assertEqual(nopbc[1], "PBC=OFF")
        self.assertEqual(len([l for l in nopbc[4:] if l and l.split()[0] != "end"]), mycell.numAtoms())
        with open(os.path.join(self.tmpdir, "run", "fromdata.car")) as f:
            fromdata = [l.split() for l in f.read().splitlines()[5:] if l and l.split()[0] != "end"]
        self.assertEqual(len(fromdata), mycell.numAtoms())
        self.assertEqual([a[0] for a in fromdata], [a[2] for a in expected])  # symbols as labels

    def testDumpWritesStructure(self):
        """dump() writes step_N.xyz: extended XYZ with the cell, wrapped positions, fragment
        types and block ids, readable back to the same atoms"""
        import numpy as np
        from context import xyz_core

        mycell = ab_cell.Cell([20, 20, 20], paramsDir=PARAMS_DIR, outputDir=os.path.join(self.tmpdir, "run"),
                              seed=5)
        mycell.libraryAddFragment(filename=os.path.join(BLOCKS_DIR, "ch4.car"), fragmentType="A")
        mycell.libraryAddFragment(filename=os.path.join(BLOCKS_DIR, "benzene2.car"), fragmentType="B")
        mycell.addBondType("A:a-B:a")
        mycell.seed(4)
        mycell.growBlocks(3, cellEndGroups=None, libraryEndGroups=None, maxTries=50)
        mycell.dump()
        expected = []
        for block in mycell.blocks.values():
            for i, coord in enumerate(block.iterCoord()):
                wrapped, _ = xyz_core.wrapCoord3(coord, np.array([20.0, 20.0, 20.0]), center=False)
                expected.append((block.symbol(i), wrapped, block.fragmentType(i), block.id))
        step = mycell.analyse.step
        mycell.close()
        with open(os.path.join(self.tmpdir, "run", "step_1.xyz")) as f:
            lines = f.read().splitlines()
        self.assertEqual(int(lines[0]), len(expected))
        self.assertIn('Lattice="20.000000 0.0 0.0 0.0 20.000000 0.0 0.0 0.0 20.000000"', lines[1])
        self.assertIn("Properties=species:S:1:pos:R:3:type:S:1:charge:R:1:fragment:S:1:block:I:1", lines[1])
        self.assertIn("step={0}".format(step), lines[1])
        self.assertEqual(len(lines) - 2, len(expected))
        for line, (symbol, wrapped, fragment, blockId) in zip(lines[2:], expected):
            fields = line.split()
            self.assertEqual(fields[0], symbol)
            np.testing.assert_allclose([float(x) for x in fields[1:4]], wrapped, atol=1e-6)
            self.assertTrue(all(0.0 <= float(x) < 20.0 for x in fields[1:4]))
            self.assertEqual(fields[6], fragment)
            self.assertEqual(int(fields[7]), blockId)
        # and its topology beside it (tests/testExport.py checks what is in it)
        self.assertTrue(os.path.isfile(os.path.join(self.tmpdir, "run", "step_1.topology.json")))
        self.assertEqual({e[2] for e in expected}, {"A", "B"})

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
