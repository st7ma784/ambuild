"""The conduction recipe stage: liminal's `conduct` run as an external program
(docs/conduction.md). A stand-in (fake_liminal.py) writes liminal's documented results
format, so these tests do not need liminal; testSp3Sp2 runs the real program when it is
installed."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from context import BLOCKS_DIR, PARAMS_DIR
from ambuild import campaign as ab_campaign
from ambuild import conduction
from ambuild import recipe as ab_recipe
from testIonMap import FAKE, events


def recipe(**stage):
    return {
        "recipe_version": 1, "name": "conduction test",
        "cell": {"box": [20, 20, 20]},
        "fragments": [{"type": "A", "car": os.path.join(BLOCKS_DIR, "benzene2.car"),
                       "csv": os.path.join(BLOCKS_DIR, "benzene2.csv"), "name": "benzene2"}],
        "params": {name: os.path.join(PARAMS_DIR, name) for name in (
            "angle_params.csv", "bond_params.csv", "dihedral_params.csv", "improper_params.csv", "pair_params.csv")},
        "bond_types": ["A:a-A:a"],
        "stages": [{"op": "seed", "count": 3}, dict({"op": "conduction"}, **stage)],
        "seed": 4,
    }


class ConductionStage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.rundir = os.path.join(self.tmp, "run")

    def testTheFiguresAreRecorded(self):
        ab_recipe.run(recipe(t_sp3=0.2, max_bridge=2), self.rundir, baseDir="/", liminalExe=FAKE)
        (event,) = events(self.rundir, conduction.EVENT)
        data = event["data"]
        self.assertEqual(data["returncode"], 0)
        self.assertAlmostEqual(data["gap"], 2.0)  # the fake's 10 x t_sp3: the setting reached liminal
        self.assertEqual(data["parameters"]["max_bridge"], 2)
        self.assertEqual((data["tunnelling_share"], data["axes"]), (0.75, {"x": 0.03, "y": 0.0, "z": 0.0}))
        self.assertEqual(data["results"], "conduction_1/conduct.json")
        self.assertIsNotNone(event["step"])
        # the structure and topology it was given, and the results, are kept with the run
        for name in ("structure.xyz", "structure.topology.json", "conduct.json"):
            self.assertTrue(os.path.isfile(os.path.join(self.rundir, "conduction_1", name)), name)
        kinds = {e["data"]["kind"] for e in events(self.rundir, "artifact")}
        self.assertTrue({"conduction", "conduction_structure", "conduction_topology"} <= kinds)
        metrics = conduction.metrics(data)
        self.assertEqual((metrics["el_gap"], metrics["el_conductance"]), (2.0, 0.01))
        self.assertEqual((metrics["el_log_transmission"], metrics["el_log_transmission_min"]), (-17.5, -18.2))
        self.assertEqual((metrics["el_log_hopping"], metrics["el_log_hopping_min"]), (-24.4, -26.7))
        self.assertEqual(data["hopping_carrier"], "holes")
        self.assertEqual(data["coherent_axes"], {"x": 3e-18, "y": 0.0, "z": 6e-19})

    def testThroughSpaceIsPassedOn(self):
        ab_recipe.run(recipe(through_space=True), self.rundir, baseDir="/", liminalExe=FAKE)
        with open(os.path.join(self.rundir, "conduction_1", "conduct.json")) as f:
            self.assertEqual(json.load(f)["flags"], ["--out", "--through-space"])

    def testUnsetSettingsAreLeftToLiminal(self):
        """Without settings, Ambuild passes none, so liminal's calibrated defaults apply"""
        ab_recipe.run(recipe(), self.rundir, baseDir="/", liminalExe=FAKE)
        with open(os.path.join(self.rundir, "conduction_1", "conduct.json")) as f:
            self.assertEqual(json.load(f)["flags"], ["--out"])

    def testAFailureStopsTheRecipe(self):
        with self.assertRaisesRegex(RuntimeError, "liminal conduct failed .exit code 4."):
            ab_recipe.run(recipe(t_sp3=9.5), self.rundir, baseDir="/", liminalExe=FAKE)
        (event,) = events(self.rundir, conduction.EVENT)
        self.assertEqual(event["data"]["returncode"], 4)
        with open(os.path.join(self.rundir, "run.json")) as f:
            self.assertEqual(json.load(f)["status"], "failed")

    def testAnotherFormatVersionIsRefused(self):
        with self.assertRaisesRegex(RuntimeError, "not liminal-conduction version 1"):
            ab_recipe.run(recipe(t_sp3=9.0), self.rundir, baseDir="/", liminalExe=FAKE)

    def testWithoutLiminalTheRecipeDoesNotStart(self):
        with self.assertRaisesRegex(RuntimeError, "set LIMINAL_EXE"):
            with mock.patch.dict(os.environ, {"PATH": "", "LIMINAL_EXE": ""}):
                ab_recipe.run(recipe(), self.rundir, baseDir="/")
        self.assertFalse(os.path.exists(self.rundir))

    def testTheStageIsValidated(self):
        self.assertEqual(ab_recipe.validate(recipe(t_sp3=0.3, sp3_decay=0.5, max_bridge=2, max_dense=100),
                                            allowPaths=True), [])
        self.assertTrue(ab_recipe.validate(recipe(max_bridge=0), allowPaths=True))
        self.assertTrue(ab_recipe.validate(recipe(ions=["Li+"]), allowPaths=True))


class Metrics(unittest.TestCase):
    def testNamesAndLabels(self):
        self.assertEqual(conduction.METRICS[:2], ["el_gap", "el_conductance"])
        self.assertEqual(conduction.metricLabel("el_gap"), "π gap (eV, Hückel)")
        self.assertEqual(conduction.metrics(None), {m: None for m in conduction.METRICS})
        self.assertLessEqual(set(conduction.METRICS), set(ab_campaign.METRICS))



if __name__ == "__main__":
    unittest.main()
