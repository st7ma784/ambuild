"""The example recipe li_ion_carbon: a carbon network of benzene rings joined by alkyne
(ethynylene) linkers, whose pores should let lithium ions through.

The build is checked everywhere; with Poreblazer (the ambuild-poreblazer image, run by
CI's image job) its pores are checked against a lithium ion: a pore limiting diameter
above a bare Li+ (twice its Shannon ionic radius, 0.76 A) and a pore network that
percolates, so an ion can pass through the cell, not just sit in a cavity.
"""
import collections
import glob
import json
import os
import shutil
import tempfile
import unittest

from context import BLOCKS_DIR
from ambuild import recipe as ab_recipe

LITHIUM_ION_DIAMETER = 2 * 0.76  # A, bare Li+ (Shannon radius, six-coordinate)


def lastStructure(rundir):
    files = sorted(glob.glob(os.path.join(rundir, "step_*.xyz")), key=lambda p: int(os.path.basename(p)[5:-4]))
    with open(files[-1]) as f:
        lines = f.read().splitlines()
    n = int(lines[0])
    return [line.split() for line in lines[2:2 + n]]


class LiIonCarbon(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.recipe = ab_recipe.example("li_ion_carbon", blocksDir=BLOCKS_DIR)

    def testIsAShippedExample(self):
        self.assertIn("li_ion_carbon", ab_recipe.examples())
        self.assertEqual(ab_recipe.validate(self.recipe, allowPaths=True), [])
        self.assertEqual({f["name"] for f in self.recipe["fragments"]}, {"benzene_135", "acetylene"})

    def testBuildsAnAlkyneLinkedNetwork(self):
        body = dict(self.recipe, stages=self.recipe["stages"][:-1])  # without Poreblazer
        rundir = os.path.join(self.tmp, "run")
        ab_recipe.run(body, rundir, baseDir="/")
        atoms = lastStructure(rundir)
        byBlock = collections.defaultdict(collections.Counter)
        for symbol, x, y, z, fragment, block in atoms:
            byBlock[block][fragment] += 1
        rings = sum(c["A"] for c in byBlock.values()) // 9     # C6H3 once linked (12 atoms less 3 caps)
        linkers = sum(c["B"] for c in byBlock.values())
        self.assertGreater(rings, 20)
        self.assertGreater(linkers, 20)
        # the rings grew into networks: each block holds rings and linkers, and there are
        # far fewer blocks than fragments
        self.assertLessEqual(len(byBlock), 10)
        self.assertTrue(all(c["A"] and c["B"] for c in byBlock.values()), dict(byBlock))
        with open(os.path.join(rundir, "run.json")) as f:
            self.assertEqual(json.load(f)["status"], "finished")

    @unittest.skipUnless(os.environ.get("POREBLAZER_EXE") or shutil.which("poreblazer.exe")
                         or shutil.which("poreblazer"), "Needs Poreblazer")
    def testPoresLetLithiumIonsThrough(self):
        rundir = os.path.join(self.tmp, "run")
        ab_recipe.run(self.recipe, rundir, baseDir="/")
        with open(os.path.join(rundir, "events.jsonl")) as f:
            results = [json.loads(line)["data"] for line in f if '"pore_result"' in line]
        self.assertEqual(len(results), 1)
        pore = results[0]
        self.assertEqual(pore["returncode"], 0)
        self.assertGreater(pore["pore_limiting_diameter_A"], LITHIUM_ION_DIAMETER)
        self.assertGreaterEqual(pore["percolated_dimensions"], 1)


if __name__ == "__main__":
    unittest.main()
