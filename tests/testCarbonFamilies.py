"""Porous aromatic frameworks and graphyne/graphdiyne (docs/carbon-families.md), and the
recipe gallery (ambuild.gallery).

The recipes build 60 A cells, too large for the test suite, so the build tests run each
scaled down (a 30 A cell, two passes); docs/carbon-families.md records the full builds.
"""
import collections
import copy
import glob
import math
import os
import shutil
import tempfile
import unittest

import numpy as np

from context import BLOCKS_DIR, ab_cell, ab_util
from ambuild import campaign as ab_campaign
from ambuild import gallery as ab_gallery
from ambuild import recipe as ab_recipe
from ambuild import xyz_util
import testCarbonLinkers as linkers

RECIPES = ("paf1_large", "paf_adamantane_large", "graphyne_large", "graphdiyne_large")


def paramsDir(body):
    return os.path.dirname(body["params"]["bond_params.csv"])


def scaledDown(name, optimise):
    body = ab_recipe.example(name, blocksDir=BLOCKS_DIR)
    body["cell"]["box"] = [30, 30, 30]
    stages = []
    for s in body["stages"]:
        if s.get("op") in ("poreblazer", "conduction"):
            continue
        s = copy.deepcopy(s)
        if s.get("op") == "seed":
            s["count"] = 2
        if "repeat" in s:
            s["repeat"] = 2
            s["stages"] = [x for x in s["stages"] if optimise or x["op"] != "optimise"]
            for x in s["stages"]:
                if x["op"] == "grow":
                    x["count"] = 10
        stages.append(s)
    body["stages"] = stages
    tmp = tempfile.mkdtemp()
    try:
        run = os.path.join(tmp, "run")
        ab_recipe.run(body, run, baseDir="/")
        last = max(glob.glob(os.path.join(run, "step_*.pkl.gz")), key=lambda p: int(os.path.basename(p)[5:].split(".")[0]))
        return ab_util.cellFromPickle(last, paramsDir=paramsDir(body)), paramsDir(body)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def fragment(name, params):
    cell = ab_cell.Cell([30, 30, 30], paramsDir=params)
    cell.libraryAddFragment(filename=os.path.join(BLOCKS_DIR, name + ".car"), fragmentType="X")
    f = cell._fragmentLibrary["X"]
    return f, [np.asarray(c) for c in f.iterCoord()]


PAF = os.path.join(os.path.dirname(os.path.abspath(ab_recipe.__file__)), "recipes", "params", "gaff_paf")
CARBON = linkers.PARAMS


class Blocks(unittest.TestCase):
    def testTetrahedralNode(self):
        f, c = fragment("carbon_tetrahedral", PAF)
        self.assertEqual(f.type(0), "c3")
        self.assertEqual(len(f.endGroups()), 4)
        for i in range(1, 5):
            self.assertAlmostEqual(np.linalg.norm(c[i] - c[0]), 1.09, places=3)
            for j in range(i + 1, 5):
                self.assertAlmostEqual(linkers.angle(c[i] - c[0], c[j] - c[0]), 109.47, places=1)

    def testAdamantane(self):
        f, c = fragment("adamantane", PAF)
        self.assertEqual(len(c), 26)  # C10H16
        self.assertEqual(len(f.endGroups()), 4)
        carbons = c[:10]
        bonds = [(i, j) for i in range(10) for j in range(i + 1, 10) if np.linalg.norm(carbons[i] - carbons[j]) < 1.6]
        self.assertEqual(len(bonds), 12)  # the cage's C-C bonds
        self.assertTrue(all(abs(np.linalg.norm(carbons[i] - carbons[j]) - 1.54) < 1e-6 for i, j in bonds))
        for e in f.endGroups():  # links at the bridgeheads, outward
            self.assertLess(e.fragmentEndGroupIdx, 4)

    def testBiphenyl(self):
        f, c = fragment("biphenyl", PAF)
        self.assertEqual(len(c), 22)
        self.assertAlmostEqual(np.linalg.norm(c[3] - c[9]), 1.4854, places=4)  # the inter-ring bond
        n1 = np.cross(c[1] - c[0], c[2] - c[0])
        n2 = np.cross(c[7] - c[6], c[8] - c[6])
        self.assertAlmostEqual(math.degrees(math.acos(abs(n1 @ n2) / np.linalg.norm(n1) / np.linalg.norm(n2))), 44.4,
                               places=1)
        self.assertEqual(sorted(e.fragmentEndGroupIdx for e in f.endGroups()), [0, 6])  # the outer para carbons

    def testButadiyne(self):
        f, c = fragment("butadiyne", CARBON)
        self.assertEqual([f.type(i) for i in range(4)], ["cg", "ch", "ch", "cg"])
        self.assertAlmostEqual(np.linalg.norm(c[1] - c[0]), 1.217, places=3)
        self.assertAlmostEqual(np.linalg.norm(c[2] - c[1]), 1.384, places=3)
        self.assertEqual(sorted(e.fragmentEndGroupIdx for e in f.endGroups()), [0, 3])


class Recipes(unittest.TestCase):
    def testEachAllowsOnlyItsTopology(self):
        expected = {"paf1_large": ["Q:q-L:b"], "paf_adamantane_large": ["M:q-L:b"],
                    "graphyne_large": ["A:a-B:g", "A:a-B:h"], "graphdiyne_large": ["A:a-D:d"]}
        for name in RECIPES:
            body = ab_recipe.example(name, blocksDir=BLOCKS_DIR)
            self.assertEqual(ab_recipe.validate(body, allowPaths=True), [], name)
            self.assertEqual(body["bond_types"], expected[name])
            self.assertEqual(body["cell"]["box"], [60, 60, 60])

    def testEveryAngleAndDihedralAJoinCanMakeHasParameters(self):
        missing = set()
        for name in RECIPES:
            body = ab_recipe.example(name, blocksDir=BLOCKS_DIR)
            params = paramsDir(body)
            with open(os.path.join(params, "angle_params.csv")) as f:
                angles = {line.split(",")[0] for line in f.read().splitlines()[1:]}
            with open(os.path.join(params, "dihedral_params.csv")) as f:
                dihedrals = {line.split(",")[0] for line in f.read().splitlines()[1:]}
            old, linkers.PARAMS = linkers.PARAMS, params
            try:
                needAngles, needDihedrals = linkers.possibleTerms(body)
            finally:
                linkers.PARAMS = old
            missing |= {name + ": angle " + "-".join(a) for a in needAngles
                        if "-".join(a) not in angles and "-".join(a[::-1]) not in angles}
            missing |= {name + ": dihedral " + "-".join(d) for d in needDihedrals
                        if "-".join(d) not in dihedrals and "-".join(d[::-1]) not in dihedrals}
        self.assertEqual(sorted(missing), [])


class Builds(unittest.TestCase):
    def testScaledDownBuildsAreOneFrameworkOfTheRightJoins(self):
        kinds = {"paf1_large": {("L:cp", "Q:c3")}, "paf_adamantane_large": {("L:cp", "M:c3")},
                 "graphyne_large": {("A:cp", "B:cg"), ("A:cp", "B:ch")}, "graphdiyne_large": {("A:cp", "D:cg")}}
        for name in RECIPES:
            cell, params = scaledDown(name, optimise=False)
            lengths = xyz_util.BondLength(os.path.join(params, "bond_params.csv"), typed=True)
            self.assertEqual(len(cell.blocks), 1, (name, sorted(b.numAtoms() for b in cell.blocks.values())))
            found = collections.defaultdict(list)
            for block, a, b in linkers.junctions(cell):
                found[linkers.kind(block, a, b)].append(linkers.length(cell, block, a, b))
            self.assertLessEqual(set(found), kinds[name], name)  # nothing else joins
            self.assertTrue(found, name)
            for k, ds in found.items():
                r0 = lengths.bondLength(k[0].split(":")[1], k[1].split(":")[1])
                self.assertGreater(sum(abs(d - r0) < 0.005 for d in ds), 0.8 * len(ds), (name, k))

    @unittest.skipUnless(ab_util.HOOMDVERSION, "Needs HOOMD-blue")
    def testOptimisedJoinsKeepTheirGeometry(self):
        """Every join within 0.05 A of r0 after optimisation; angles within 10 degrees of
        109.5 at the sp3 nodes, 120 at ring carbons, 180 along alkynes"""
        ideal = {"c3": 109.5, "cp": 120.0, "cg": 180.0, "ch": 180.0}
        # adamantane-phenyl joins stretch 0.03-0.06 A: the cage's CH2 hydrogens and the
        # phenyl's ortho hydrogens are 5-6 bonds apart (so they interact) and only 1.9-2.2 A
        # apart, inside H...H contact, and rigid blocks cannot flex to relieve it
        tolerance = {"paf_adamantane_large": 0.08}
        for name in RECIPES:
            cell, params = scaledDown(name, optimise=True)
            lengths = xyz_util.BondLength(os.path.join(params, "bond_params.csv"), typed=True)
            worst = collections.defaultdict(float)
            for block, a, b in linkers.junctions(cell):
                d = linkers.length(cell, block, a, b)
                self.assertLess(abs(d - lengths.bondLength(block.type(a), block.type(b))), tolerance.get(name, 0.05),
                                (name, d))
                for atom, other in ((a, b), (b, a)):
                    for value in linkers.anglesAt(block, atom, other, cell):
                        worst[block.type(atom)] = max(worst[block.type(atom)], abs(value - ideal[block.type(atom)]))
            for t, deviation in worst.items():
                self.assertLess(deviation, 10.0, (name, t, deviation))


class Gallery(unittest.TestCase):
    def testEveryExampleIsInOneFamily(self):
        listed = [r for f in ab_gallery.catalogue() for r in f["recipes"]]
        self.assertEqual(sorted(listed), sorted(ab_recipe.examples()))
        self.assertEqual(len(listed), len(set(listed)))
        campaigns = {c for f in ab_gallery.catalogue() for c in f.get("campaigns", [])}
        self.assertLessEqual(campaigns, set(ab_campaign.examples()))

    def testSummaries(self):
        paf = ab_gallery.summarise("paf1_large")
        self.assertEqual(paf["blocks"], ["carbon_tetrahedral", "biphenyl"])
        self.assertEqual((paf["seeds"], paf["passes"], paf["params"]), (8, 10, "gaff_paf"))
        self.assertTrue(paf["optimised"])
        ions = ab_gallery.summarise("li_ion_carbon_ions")
        self.assertIn("ion maps: Li+, Na+, K+", ions["measures"])


if __name__ == "__main__":
    unittest.main()
