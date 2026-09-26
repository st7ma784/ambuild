"""
Newer HOOMD-blue engines give the same energies as HOOMD-blue 2 for the same cells.

test_data/hoomd_parity holds saved cells and their energies under HOOMD-blue 2
(written by make_reference.py there).
"""
import json
import os
import shutil
import tempfile
import unittest

from context import ab_util
from context import PARAMS_DIR, TESTDATA_DIR

PARITY_DIR = os.path.join(TESTDATA_DIR, "hoomd_parity")


@unittest.skipUnless(
    ab_util.HOOMDVERSION is not None and ab_util.HOOMDVERSION[0] >= 4,
    "Needs HOOMD-blue 4 or later",
)
class Test(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        with open(os.path.join(PARITY_DIR, "reference.json")) as f:
            self.reference = json.load(f)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def staticEnergy(self, name):
        case = self.reference["cases"][name]
        cell = ab_util.cellFromPickle(
            os.path.join(PARITY_DIR, case["pickle"]),
            paramsDir=PARAMS_DIR,
            outputDir=os.path.join(self.tmpdir, name),
        )
        # One step at a negligible timestep: the energy of the saved configuration
        cell.runMD(mdCycles=1, dt=1e-9, quiet=True, **case["kwargs"])
        energy = cell.analyse.last["potential_energy"]
        cell.close()
        return energy, case["potential_energy"]

    def assertParity(self, name):
        energy, reference = self.staticEnergy(name)
        # HOOMD-blue 2 in the reference container was built in single precision
        self.assertAlmostEqual(energy, reference, delta=1e-4 * abs(reference),
                               msg="{0}: {1} vs HOOMD-blue 2 {2}".format(name, energy, reference))

    def testAllAtom(self):
        self.assertParity("all_atom")

    def testAllAtomDihedrals(self):
        self.assertParity("all_atom_dihedrals")

    def testRigid(self):
        self.assertParity("rigid")

    def testAllAtomWalls(self):
        self.assertParity("all_atom_walls")


if __name__ == "__main__":
    unittest.main()
