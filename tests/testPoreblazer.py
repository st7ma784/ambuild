"""
Created on 4 Mar 2018

@author: jmht
"""
import os
import shutil
import tempfile
import unittest

from context import ab_cell
from context import ab_poreblazer
from context import PARAMS_DIR, BLOCKS_DIR, TESTDATA_DIR

POREBLAZER_EXE = os.environ.get("POREBLAZER_EXE")


class Test(unittest.TestCase):
    @unittest.skipUnless(os.path.isfile("/bin/cat"), "Needs /bin/cat as a dummy executable")
    def testDummy(self):
        """Run with a dummy executable just to make sure the code runs"""
        boxWidth = 20.0
        boxDim = [boxWidth, boxWidth, boxWidth]
        mycell = ab_cell.Cell(boxDim, paramsDir=PARAMS_DIR)
        ch4Car = os.path.join(BLOCKS_DIR, "ch4.car")
        mycell.libraryAddFragment(filename=ch4Car, fragmentType="A")
        mycell.addBondType("A:a-A:a")
        mycell.seed(
            3,
            fragmentType="A",
            point=[boxWidth / 2, boxWidth / 2, boxWidth / 2],
            radius=5,
        )
        mycell.growBlocks(4)
        # Run with a dummy executable
        results = mycell.poreblazer("/bin/cat")
        self.assertEqual(results["returncode"], 0)
        self.assertIsNone(results["surface_area_m2_g"])
        pdir = "{}_{}".format(ab_poreblazer.NAME_STEM, 0)
        self.assertTrue(os.path.isfile(os.path.join(pdir, "ambuild.xyz")))
        self.assertTrue(os.path.isfile(os.path.join(pdir, "defaults.dat")))
        os.unlink("ambuild.csv")
        os.unlink("ambuild.log")
        shutil.rmtree(pdir)
        return

    @unittest.skipUnless(os.path.isfile("/bin/cat"), "Needs /bin/cat as a dummy executable")
    def testOutputDir(self):
        """Poreblazer runs inside the cell's outputDir without changing the working directory"""
        owd = os.getcwd()
        rundir = tempfile.mkdtemp()
        mycell = ab_cell.Cell([20.0, 20.0, 20.0], paramsDir=PARAMS_DIR, outputDir=rundir)
        mycell.libraryAddFragment(filename=os.path.join(BLOCKS_DIR, "ch4.car"), fragmentType="A")
        mycell.seed(3)
        mycell.poreblazer("/bin/cat")
        mycell.close()
        pdir = os.path.join(rundir, "{}_{}".format(ab_poreblazer.NAME_STEM, 0))
        self.assertEqual(os.getcwd(), owd)
        for name in ["ambuild.xyz", "defaults.dat", "input.dat", "UFF.atoms", "poreblazer.log"]:
            self.assertTrue(os.path.isfile(os.path.join(pdir, name)), name)
        shutil.rmtree(rundir)
        return


    def testParseOutput(self):
        """Parse the output of a Poreblazer v3.0.5 run"""
        results = ab_poreblazer.parse_output(os.path.join(TESTDATA_DIR, "poreblazer"))
        expected = {
            "version": "3.0.5",
            "system_volume_A3": 8000.0,
            "system_mass_g_mol": 772.0,
            "system_density_g_cm3": 0.16,
            "helium_volume_A3": 6964.364,
            "helium_volume_cm3_g": 5.433,
            "geometric_volume_A3": 7106.824,
            "geometric_volume_cm3_g": 5.544,
            "surface_area_A2": 1721.62,
            "surface_area_m2_cm3": 2152.02,
            "surface_area_m2_g": 13429.83,
            "pore_limiting_diameter_A": 9.59,
            "maximum_pore_diameter_A": 13.25,
            "percolated_dimensions": 1,
        }
        for key, value in expected.items():
            self.assertEqual(results[key], value, key)
        self.assertEqual(len(results["psd"]), 78)
        self.assertEqual(results["psd"][18], [4.625, 2.00033188e-04])
        self.assertEqual(len(results["psd_cumulative"]), 80)
        self.assertEqual(results["psd_cumulative"][0], [-0.125, 1.0])

    def testParseMissingOutput(self):
        """A directory without Poreblazer output gives None for every result"""
        rundir = tempfile.mkdtemp()
        results = ab_poreblazer.parse_output(rundir)
        shutil.rmtree(rundir)
        self.assertTrue(all(value is None for value in results.values()), results)

    @unittest.skipUnless(
        POREBLAZER_EXE and os.path.isfile(POREBLAZER_EXE), "Set POREBLAZER_EXE to run"
    )
    def testRealPoreblazer(self):
        """Run a real Poreblazer executable on a small benzene cell"""
        rundir = tempfile.mkdtemp()
        mycell = ab_cell.Cell([20.0, 20.0, 20.0], paramsDir=PARAMS_DIR, outputDir=rundir)
        mycell.libraryAddFragment(filename=os.path.join(BLOCKS_DIR, "benzene.car"), fragmentType="A")
        mycell.addBondType("A:a-A:a")
        mycell.seed(6)
        results = mycell.poreblazer(POREBLAZER_EXE)
        mycell.close()
        self.addCleanup(shutil.rmtree, rundir)

        self.assertEqual(results["returncode"], 0)
        self.assertIsNotNone(results["version"])
        self.assertGreater(results["surface_area_m2_g"], 0.0)
        self.assertGreater(results["system_volume_A3"], 7999.0)
        self.assertLessEqual(
            results["pore_limiting_diameter_A"], results["maximum_pore_diameter_A"]
        )
        self.assertTrue(results["psd"])
        self.assertTrue(results["psd_cumulative"])
        self.assertFalse(os.path.exists(os.path.join(results["directory"], "nitrogen_network.grd")))


    def testDefaultsDatMatchesHistoricalFile(self):
        """The generated defaults.dat reproduces the file Ambuild always wrote"""
        historical = """UFF.atoms
2.58, 10.22, 3.314, 298
12.8, 500
0.2
20.0, 0.25
21908391
2

! Default forcefield: UFF
! Helium atom sigma (A), helium atom epsilon (K), nitrogen atom sigma (A), temperature (K)
! Cutoff distance (A), accessible surface area coefficient (1.0 for hard sphere
! surface, 1.122 for potential minimum surface), number of trials for surface area
! calculation
! 0.2: Cubelet size (A)
! Largest anticipated pore diameter (A), size of the bin for PSD (A)
! Random number seed

! Do not change these values unless you know what you are doing
"""
        self.assertEqual(ab_poreblazer.DEFAULTS_DAT, historical)

    def testSettings(self):
        lines = ab_poreblazer.defaults_dat(cubelet_size=0.3, surface_trials=200).splitlines()
        self.assertEqual(lines[2], "12.8, 200")
        self.assertEqual(lines[3], "0.3")
        self.assertEqual(lines[6], "0")  # visualisation defaults to none
        self.assertEqual(ab_poreblazer.defaults_dat(visualisation="both").splitlines()[6], "3")
        with self.assertRaises(ValueError):
            ab_poreblazer.settings(cubelet=0.3)
        with self.assertRaises(ValueError):
            ab_poreblazer.settings(visualisation="png")

    @unittest.skipUnless(os.path.isfile("/bin/cat"), "Needs /bin/cat as a dummy executable")
    def testSettingsReachPoreblazer(self):
        rundir = tempfile.mkdtemp()
        mycell = ab_cell.Cell([20.0, 20.0, 20.0], paramsDir=PARAMS_DIR, outputDir=rundir)
        mycell.libraryAddFragment(filename=os.path.join(BLOCKS_DIR, "ch4.car"), fragmentType="A")
        mycell.seed(3)
        with self.assertRaises(ValueError):
            mycell.poreblazer("/bin/cat", cubelet=0.3)
        self.assertFalse(os.path.exists(os.path.join(rundir, "poreblazer_0")))
        results = mycell.poreblazer("/bin/cat", cubelet_size=0.3)
        mycell.close()
        with open(os.path.join(results["directory"], "defaults.dat")) as f:
            self.assertEqual(f.read().splitlines()[3], "0.3")
        self.assertEqual(results["settings"]["cubelet_size"], 0.3)
        self.assertEqual(results["settings"]["visualisation"], "none")
        shutil.rmtree(rundir)

if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
