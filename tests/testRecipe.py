"""Recipes (ambuild.recipe): validation, and builds from a recipe that are recorded and
reproducible, whether files are given by path or by sha256 reference."""
import copy
import glob
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from context import ab_cell
from context import BLOCKS_DIR, PARAMS_DIR
from ambuild import recipe as ab_recipe

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def example(ref=lambda name: name):
    """A small build without HOOMD-blue; ref maps a file name to its reference"""
    return {
        "recipe_version": 1,
        "name": "benzene test",
        "cell": {"box": [25, 25, 25]},
        "fragments": [{"type": "A", "car": ref("benzene.car"), "csv": ref("benzene.csv"), "name": "benzene"}],
        "params": {name: ref(name) for name in ab_recipe.PARAMS_FILES},
        "bond_types": ["A:a-A:a"],
        "stages": [
            {"op": "seed", "count": 4},
            {"repeat": 2, "stages": [{"op": "grow", "count": 2, "max_tries": 20}]},
            {"op": "zip", "bond_margin": 1.0, "bond_angle_margin": 30},
        ],
        "seed": 7,
    }


def lastStructure(rundir):
    """Text of the last checkpoint's structure file, without its header's run id and recipe
    hash: two runs of the same build write the same atoms, but those can differ"""
    files = sorted(glob.glob(os.path.join(rundir, "step_*.xyz")),
                   key=lambda p: int(os.path.basename(p)[5:-4]))
    with open(files[-1]) as f:
        return re.sub(r' (run_id|recipe_sha256)="[^"]*"', "", f.read())


class Validation(unittest.TestCase):
    def setUp(self):
        self.recipe = example(lambda name: "sha256:" + sha(os.path.join(
            PARAMS_DIR if name.endswith("_params.csv") else BLOCKS_DIR, name)))

    def errors(self, change, allowPaths=False):
        r = copy.deepcopy(self.recipe)
        change(r)
        return ab_recipe.validate(r, allowPaths=allowPaths)

    def testExampleIsValid(self):
        self.assertEqual(ab_recipe.validate(self.recipe), [])

    def testProblemsNameWhereTheyAre(self):
        cases = [
            (lambda r: r.update(recipe_version=2), "recipe_version: must be 1"),
            (lambda r: r.update(colour="red"), "colour: unknown field"),
            (lambda r: r["cell"].update(box=[25, 25]), "cell.box: must be a list of 3 positive numbers"),
            (lambda r: r["stages"].append({"op": "explode"}), "stages[3].op: must be one of"),
            (lambda r: r["stages"][0].update(count=0), "stages[0].count: must be at least 1"),
            (lambda r: r["stages"][0].pop("count"), "stages[0].count: required"),
            (lambda r: r["stages"][1]["stages"][0].update(count="2"), "stages[1].stages[0].count: must be an integer"),
            (lambda r: r["stages"][1]["stages"][0].update(maxTries=3), "stages[1].stages[0].maxTries: unknown argument"),
            (lambda r: r["stages"][1].update(repeat=0), "stages[1].repeat: must be an integer from 1"),
            (lambda r: r["fragments"][0].update(car="benzene.car"), "fragments[0].car: must be a file reference"),
            (lambda r: r["bond_types"].append("A:a-C:a"), "bond_types[1]: A:a-C:a names a fragment type"),
            (lambda r: r.update(max_bonds={"B:b-B:b": 1}), "max_bonds.B:b-B:b: not one of bond_types"),
            (lambda r: r["params"].pop("bond_params.csv"), "params: null"),
            (lambda r: r.update(seed=-1), "seed: must be null or a non-negative integer"),
            (lambda r: r.update(resources={"time": "2 hours"}), "resources.time: must be a Slurm time"),
            (lambda r: r["stages"][2].update(bond_margin=True), "stages[2].bond_margin: must be a number"),
            (lambda r: r["stages"][0].update(fragment_type="Z"), "stages[0].fragment_type: Z is not a fragment type"),
            (lambda r: r["stages"].append({"op": "delete_blocks", "fragment_types": ["A", "Q"]}),
             "stages[3].fragment_types: Q is not a fragment type"),
        ]
        for change, expected in cases:
            errors = self.errors(change)
            self.assertTrue(any(e.startswith(expected) for e in errors), (expected, errors))

    def testPathsOnlyWhenAllowed(self):
        withPaths = example()
        self.assertTrue(ab_recipe.validate(withPaths))
        self.assertEqual(ab_recipe.validate(withPaths, allowPaths=True), [])

    def testReferencesAndHash(self):
        refs = ab_recipe.references(self.recipe)
        self.assertIn(sha(os.path.join(BLOCKS_DIR, "benzene.car")), refs)
        self.assertEqual(len(refs), 2 + len(ab_recipe.PARAMS_FILES))
        reordered = json.loads(json.dumps(self.recipe, sort_keys=True))
        self.assertEqual(ab_recipe.recipeHash(reordered), ab_recipe.recipeHash(self.recipe))
        self.assertEqual(ab_recipe.countSteps(self.recipe["stages"]), 4)

    def testOperationsMatchCellMethods(self):
        """Every operation calls a Cell method with keywords it accepts"""
        for name, spec in ab_recipe.OPERATIONS.items():
            method = getattr(ab_cell.Cell, spec["method"])
            params = inspect.signature(method).parameters
            takesKw = any(p.kind == p.VAR_KEYWORD for p in params.values())
            for arg in spec["args"]:
                if arg["name"] == "settings":
                    continue
                keyword = arg["kwarg"] or arg["name"]
                self.assertTrue(keyword in params or takesKw, "{0}.{1}".format(name, keyword))
        json.dumps(ab_recipe.describe())

    def testImportNeedsOnlyTheStandardLibrary(self):
        out = subprocess.check_output(
            [sys.executable, "-c", "import sys, ambuild.recipe; print('numpy' in sys.modules)"], cwd=ROOT)
        self.assertEqual(out.decode().strip(), "False")


class Running(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        # Files by path, next to the recipe
        self.inputs = os.path.join(self.tmp, "inputs")
        os.makedirs(self.inputs)
        for name in ("benzene.car", "benzene.csv"):
            shutil.copy(os.path.join(BLOCKS_DIR, name), self.inputs)
        for name in ab_recipe.PARAMS_FILES:
            shutil.copy(os.path.join(PARAMS_DIR, name), self.inputs)
        # and by content, in a blob directory
        self.blobs = os.path.join(self.tmp, "blobs")
        os.makedirs(self.blobs)
        for name in os.listdir(self.inputs):
            shutil.copy(os.path.join(self.inputs, name), os.path.join(self.blobs, sha(os.path.join(self.inputs, name))))
        self.byPath = example()
        self.byRef = example(lambda name: "sha256:" + sha(os.path.join(self.inputs, name)))

    def testRecordedRun(self):
        rundir = os.path.join(self.tmp, "run")
        runId = ab_recipe.run(self.byRef, rundir, blobDirs=[self.blobs], runId="11111111-2222-3333-4444-555555555555")
        self.assertEqual(runId, "11111111-2222-3333-4444-555555555555")
        with open(os.path.join(rundir, "run.json")) as f:
            run = json.load(f)
        self.assertEqual(run["status"], "finished")
        self.assertEqual(run["random"]["seed"], 7)
        kinds = [i["kind"] for i in run["inputs"]]
        self.assertNotIn("script", kinds)
        recipeInput = next(i for i in run["inputs"] if i["kind"] == "recipe")
        self.assertEqual(recipeInput["name"], "benzene test")
        self.assertEqual(recipeInput["recipe_sha256"], ab_recipe.recipeHash(self.byRef))
        with open(os.path.join(rundir, recipeInput["path"])) as f:
            self.assertEqual(json.load(f), self.byRef)
        # a checkpoint after the seed, each pass of the repeat, and the zip
        self.assertEqual(len(glob.glob(os.path.join(rundir, "step_*.xyz"))), 4)
        self.assertEqual(len(glob.glob(os.path.join(rundir, "step_*.pkl.gz"))), 4)

    def testSameStructureByPathOrReference(self):
        a, b = os.path.join(self.tmp, "a"), os.path.join(self.tmp, "b")
        ab_recipe.run(self.byPath, a, baseDir=self.inputs)
        ab_recipe.run(self.byRef, b, blobDirs=[self.blobs])
        self.assertEqual(lastStructure(a), lastStructure(b))
        c = os.path.join(self.tmp, "c")
        ab_recipe.run(self.byRef, c, blobDirs=[self.blobs], seed=8)
        self.assertNotEqual(lastStructure(a), lastStructure(c))

    def testCommandLineMatchesInProcess(self):
        recipeFile = os.path.join(self.inputs, "recipe.json")
        with open(recipeFile, "w") as f:
            json.dump(self.byRef, f)
        inProcess = os.path.join(self.tmp, "in")
        ab_recipe.run(self.byRef, inProcess, blobDirs=[self.blobs])
        cli = os.path.join(self.tmp, "cli")
        env = dict(os.environ, PYTHONHASHSEED="123")
        out = subprocess.run([sys.executable, "-m", "ambuild.recipe", "run", recipeFile, "--output", cli,
                              "--blobs", self.blobs], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(lastStructure(inProcess), lastStructure(cli))

    def testInvalidRecipeFromTheCommandLine(self):
        recipeFile = os.path.join(self.tmp, "bad.json")
        with open(recipeFile, "w") as f:
            json.dump({"recipe_version": 1}, f)
        out = subprocess.run([sys.executable, "-m", "ambuild.recipe", "run", recipeFile, "--output",
                              os.path.join(self.tmp, "x")], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertIn("name: required", out.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "x")))

    def testMissingBlobFailsBeforeBuilding(self):
        with self.assertRaises(RuntimeError):
            ab_recipe.run(self.byRef, os.path.join(self.tmp, "run"), blobDirs=[self.inputs])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "run", "run.json")))

    def testFailedBuildIsRecordedAsFailed(self):
        bad = copy.deepcopy(self.byRef)
        bad["stages"].append({"op": "poreblazer"})
        rundir = os.path.join(self.tmp, "run")
        with self.assertRaises(Exception):  # no such executable
            ab_recipe.run(bad, rundir, blobDirs=[self.blobs], poreblazerExe=os.path.join(self.tmp, "missing.exe"))
        with open(os.path.join(rundir, "run.json")) as f:
            self.assertEqual(json.load(f)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
