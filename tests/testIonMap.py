"""The ion_map recipe stage: liminal run as an external program (docs/ion-maps.md). A
stand-in (fake_liminal.py) writes liminal's documented results format, so these tests do
not need liminal; the last one runs the real program when it is installed."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from context import BLOCKS_DIR, PARAMS_DIR
from ambuild import campaign as ab_campaign
from ambuild import ionmap
from ambuild import recipe as ab_recipe

FAKE = "{0} {1}".format(sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_liminal.py"))
REAL = os.environ.get("LIMINAL_EXE") or (
    "{0} -m liminal".format(sys.executable) if importlib.util.find_spec("liminal") else shutil.which("liminal"))


def recipe(ions=("Li+", "Na+", "K+"), **stage):
    return {
        "recipe_version": 1, "name": "ion map test",
        "cell": {"box": [20, 20, 20]},
        "fragments": [{"type": "A", "car": os.path.join(BLOCKS_DIR, "benzene2.car"),
                       "csv": os.path.join(BLOCKS_DIR, "benzene2.csv"), "name": "benzene2"}],
        "bond_types": ["A:a-A:a"],
        "stages": [{"op": "seed", "count": 3}, dict({"op": "ion_map", "ions": list(ions), "spacing": 1.0}, **stage)],
        "seed": 4,
    }


def events(rundir, etype):
    with open(os.path.join(rundir, "events.jsonl")) as f:
        return [e for e in (json.loads(line) for line in f) if e["type"] == etype]


class IonMapStage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.rundir = os.path.join(self.tmp, "run")

    def testEachIonsFiguresAreRecorded(self):
        ab_recipe.run(recipe(), self.rundir, baseDir="/", liminalExe=FAKE)
        results = {e["data"]["ion"]: e for e in events(self.rundir, ionmap.EVENT)}
        self.assertEqual(set(results), {"Li+", "Na+", "K+"})
        li = results["Li+"]["data"]
        self.assertEqual((li["sites"], li["escaping_sites"], li["paths"]), (3, 2, 1))
        self.assertEqual((li["site_energy"], li["escape_barrier"], li["lowest_barrier"]), (-2.0, 1.5, 0.2))
        self.assertAlmostEqual(li["median_barrier"], 0.85)
        self.assertEqual(results["K+"]["data"]["site_energy"], -4.0)
        self.assertEqual(li["map"], "ion_map_1/Li_plus/map.json")
        self.assertEqual(li["cube"], "ion_map_1/Li_plus/energy.cube")
        self.assertIsNotNone(results["Li+"]["step"])
        # the files are artifacts; the map's copy of the structure is not a viewer frame
        kinds = {e["data"]["relpath"]: e["data"]["kind"] for e in events(self.rundir, "artifact")}
        self.assertEqual(kinds["ion_map_1/Li_plus/map.json"], "ion_map")
        self.assertEqual(kinds["ion_map_1/Na_plus/energy.cube"], "ion_grid")
        self.assertEqual(kinds["ion_map_1/structure.xyz"], "ion_map_structure")
        self.assertEqual(kinds["ion_map_1/structure.topology.json"], "ion_map_topology")
        # and the metrics campaigns can aim at
        values = ionmap.metrics({ion: e["data"] for ion, e in results.items()})
        self.assertEqual((values["li_site_energy"], values["na_site_energy"], values["k_escape_barrier"]),
                         (-2.0, -3.0, 1.5))
        self.assertEqual(values["k_sites"], 3)

    def testAFailedIonFailsTheBuild(self):
        with self.assertRaisesRegex(RuntimeError, r"liminal failed for Xx\+ \(exit code 3\)"):
            ab_recipe.run(recipe(ions=("Li+", "Xx+")), self.rundir, baseDir="/", liminalExe=FAKE)
        with open(os.path.join(self.rundir, "run.json")) as f:
            run = json.load(f)
        self.assertEqual(run["status"], "failed")
        self.assertIn("Xx+", run["error"])

    def testAnotherResultsFormatVersionIsRefused(self):
        with self.assertRaisesRegex(RuntimeError, "not liminal-map version 1"):
            ab_recipe.run(recipe(ions=("Old+",)), self.rundir, baseDir="/", liminalExe=FAKE)

    def testWithoutLiminalTheRecipeStopsBeforeBuilding(self):
        with mock.patch.dict(os.environ, {"LIMINAL_EXE": ""}), mock.patch.object(ionmap.shutil, "which",
                                                                                 return_value=None):
            with self.assertRaisesRegex(RuntimeError, "set LIMINAL_EXE"):
                ab_recipe.run(recipe(), self.rundir, baseDir="/")
        self.assertFalse(os.path.exists(os.path.join(self.rundir, "run.json")))

    def testTheStageIsValidated(self):
        self.assertEqual(ab_recipe.validate(recipe(), allowPaths=True), [])
        errors = ab_recipe.validate(recipe(spacing=0.01), allowPaths=True)
        self.assertTrue(any("spacing" in e for e in errors), errors)

    def testCampaignsCanAimAtIonMetrics(self):
        self.assertIn("li_escape_barrier", ab_campaign.METRICS)
        self.assertEqual(ionmap.metricLabel("na_site_energy"), "Na+ lowest site energy (kcal/mol)")

    def testTheExampleRecipesAndCampaigns(self):
        """li_ion_carbon_ions and benzene_network_ions map three ions; the example
        campaigns aim at ion metrics and fit those recipes"""
        for name in ("li_ion_carbon_ions", "benzene_network_ions"):
            body = ab_recipe.example(name, blocksDir=BLOCKS_DIR)
            self.assertEqual(ab_recipe.validate(body, allowPaths=True), [], name)
            maps = [s for s in body["stages"] if s.get("op") == "ion_map"]
            self.assertEqual(maps[0]["ions"], ["Li+", "Na+", "K+"])
        examples = ab_campaign.examples()
        self.assertEqual(set(examples), {"easiest_li_transport", "ion_sieve"})
        # as the web GUI holds it: files by reference, not path
        body = ab_recipe.example("li_ion_carbon_ions", blocksDir=BLOCKS_DIR)
        ref = "sha256:" + "0" * 64
        for frag in body["fragments"]:
            frag["car"] = frag["csv"] = ref
        body["params"] = {name: ref for name in body["params"]}
        for name, spec in examples.items():
            self.assertEqual(ab_campaign.validate(spec, body), [], name)
        self.assertEqual(ab_campaign.objectiveMetric(examples["ion_sieve"]), "li_escape_barrier")

    @unittest.skipUnless(REAL, "needs liminal (LIMINAL_EXE, or liminal on the PATH)")
    def testWithTheRealLiminal(self):
        ab_recipe.run(recipe(ions=("Li+", "K+")), self.rundir, baseDir="/", liminalExe=REAL)
        results = {e["data"]["ion"]: e["data"] for e in events(self.rundir, ionmap.EVENT)}
        self.assertEqual(set(results), {"Li+", "K+"})
        for data in results.values():
            self.assertGreater(data["sites"], 0)
            self.assertTrue(data["tier"].startswith("classical"))


if __name__ == "__main__":
    unittest.main()
