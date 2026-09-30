"""Covalent triazine frameworks (docs/ctf-networks.md): the triazine block, the large
recipes ctf1_large, ctf_alkyne_large and ctf_mixed_large, and their GAFF 1.81 parameters
(ambuild/recipes/params/gaff_ctf).

The recipes build 60 A cells, too large for the test suite, so the build tests run each
recipe scaled down (a 30 A cell, two passes); docs/ctf-networks.md records the full
builds.
"""
import collections
import copy
import math
import os
import shutil
import tempfile
import glob
import unittest

import numpy as np

from context import BLOCKS_DIR, ab_cell, ab_util
from ambuild import recipe as ab_recipe
from ambuild import xyz_util
import testCarbonLinkers as linkers

PARAMS = os.path.join(os.path.dirname(os.path.abspath(ab_recipe.__file__)), "recipes", "params", "gaff_ctf")
RECIPES = ("ctf1_large", "ctf_alkyne_large", "ctf_mixed_large")
FORBIDDEN = {frozenset(("cg", "ch"))}  # an alkyne's cg end to another's ch end: GAFF's triple bond


def scaledDown(name, optimise):
    """The recipe in a 30 A cell with two passes of 10 grows, seeding a fifth as many"""
    body = ab_recipe.example(name, blocksDir=BLOCKS_DIR)
    body["cell"]["box"] = [30, 30, 30]
    stages = []
    for s in body["stages"]:
        if s.get("op") == "poreblazer":
            continue
        s = copy.deepcopy(s)
        if s.get("op") == "seed":
            s["count"] = max(2, s["count"] // 5)
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
        return ab_util.cellFromPickle(last, paramsDir=PARAMS)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def joinKinds(cell):
    out = collections.defaultdict(list)
    for block, a, b in linkers.junctions(cell):
        d = linkers.length(cell, block, a, b)
        out[linkers.kind(block, a, b)].append((block, a, b, d))
    return out


class Triazine(unittest.TestCase):
    def testGeometryAndTyping(self):
        cell = ab_cell.Cell([30, 30, 30], paramsDir=PARAMS)
        cell.libraryAddFragment(filename=os.path.join(BLOCKS_DIR, "triazine.car"), fragmentType="T")
        frag = cell._fragmentLibrary["T"]
        c = [np.asarray(x) for x in frag.iterCoord()]
        self.assertEqual([frag.type(i) for i in range(6)], ["cp", "nb"] * 3)
        for i in range(6):  # the ring: C-N 1.338
            self.assertAlmostEqual(np.linalg.norm(c[(i + 1) % 6] - c[i]), 1.338, places=3)
        self.assertAlmostEqual(linkers.angle(c[1] - c[0], c[5] - c[0]), 126.8, places=2)  # N-C-N
        self.assertAlmostEqual(linkers.angle(c[0] - c[1], c[2] - c[1]), 113.2, places=2)  # C-N-C
        self.assertEqual(sorted(e.type() for e in frag.endGroups()), ["T:t"] * 3)
        normal = np.cross(c[2] - c[0], c[4] - c[0])
        self.assertTrue(all(abs(normal @ (x - c[0])) < 1e-6 for x in c))  # planar, caps included


class Recipes(unittest.TestCase):
    def testTheyAreShippedAndSizedForLargeCells(self):
        for name in RECIPES:
            body = ab_recipe.example(name, blocksDir=BLOCKS_DIR)
            self.assertEqual(ab_recipe.validate(body, allowPaths=True), [], name)
            self.assertEqual(body["cell"]["box"], [60, 60, 60])
            self.assertEqual(body["resources"]["cpus"], 8)
            pore = [s for s in body["stages"] if s.get("op") == "poreblazer"][0]
            self.assertEqual(pore["settings"]["cubelet_size"], 0.3)  # about 340 MB for 60 A

    def testJoinsHaveSingleBondLengthsAndCtf1Alternates(self):
        lengths = xyz_util.BondLength(os.path.join(PARAMS, "bond_params.csv"), typed=True)
        for name in RECIPES:
            body = ab_recipe.example(name, blocksDir=BLOCKS_DIR)
            ends = {}
            for frag in body["fragments"]:
                types, _, fends = linkers.fragmentsOf({"fragments": [frag]})[frag["type"]]
                for t, atom, _ in fends:
                    ends["{0}:{1}".format(frag["type"], t)] = types[atom]
            for bt in body["bond_types"]:
                t1, t2 = (ends[e] for e in bt.split("-"))
                self.assertNotIn(frozenset((t1, t2)), FORBIDDEN, (name, bt))
                self.assertTrue(1.3 < lengths.bondLength(t1, t2) < 1.55, (name, bt))
        self.assertEqual(ab_recipe.example("ctf1_large", blocksDir=BLOCKS_DIR)["bond_types"], ["P:a-T:t"])

    def testEveryAngleAndDihedralAJoinCanMakeHasParameters(self):
        with open(os.path.join(PARAMS, "angle_params.csv")) as f:
            angles = {line.split(",")[0] for line in f.read().splitlines()[1:]}
        with open(os.path.join(PARAMS, "dihedral_params.csv")) as f:
            dihedrals = {line.split(",")[0] for line in f.read().splitlines()[1:]}
        missing = set()
        for name in RECIPES:
            needAngles, needDihedrals = linkers.possibleTerms(ab_recipe.example(name, blocksDir=BLOCKS_DIR))
            missing |= {"angle " + "-".join(a) for a in needAngles
                        if "-".join(a) not in angles and "-".join(a[::-1]) not in angles}
            missing |= {"dihedral " + "-".join(d) for d in needDihedrals
                        if "-".join(d) not in dihedrals and "-".join(d[::-1]) not in dihedrals}
        self.assertEqual(sorted(missing), [])


class Builds(unittest.TestCase):
    def testScaledDownBuildsPlaceEveryJoinAtItsLength(self):
        lengths = xyz_util.BondLength(os.path.join(PARAMS, "bond_params.csv"), typed=True)
        expected = {
            "ctf1_large": {("P:cp", "T:cp")},
            "ctf_alkyne_large": {("B:cg", "T:cp"), ("B:ch", "T:cp")},
            "ctf_mixed_large": set(),  # the kinds vary with the seed; at least five of them
        }
        for name in RECIPES:
            cell = scaledDown(name, optimise=False)
            # the join steps bond the seeds' clusters together: one framework
            self.assertEqual(len(cell.blocks), 1, (name, sorted(b.numAtoms() for b in cell.blocks.values())))
            found = joinKinds(cell)
            self.assertGreaterEqual(set(found), expected[name], name)
            if name == "ctf_mixed_large":
                self.assertGreaterEqual(len(found), 5, sorted(found))
            if name == "ctf1_large":  # strictly alternating
                self.assertEqual(set(found), {("P:cp", "T:cp")})
            for k, joins in found.items():
                self.assertNotIn(frozenset(t.split(":")[1] for t in k), FORBIDDEN, (name, k))
                r0 = lengths.bondLength(k[0].split(":")[1], k[1].split(":")[1])
                placed = sum(abs(d - r0) < 0.005 for *_, d in joins)
                self.assertGreater(placed, 0.8 * len(joins), (name, k))

    @unittest.skipUnless(ab_util.HOOMDVERSION, "Needs HOOMD-blue")
    def testOptimisedJoinsKeepTheirGeometry(self):
        """After rigid-body optimisations with dihedrals: every join within 0.05 A of its
        r0; angles at triazine carbons within 10 degrees of 116.6 (the exocyclic angle,
        N-C-N being 126.8), at benzene carbons of 120, along alkynes of 180"""
        lengths = xyz_util.BondLength(os.path.join(PARAMS, "bond_params.csv"), typed=True)
        ideal = {("T", "cp"): 116.6, ("A", "cp"): 120.0, ("P", "cp"): 120.0, ("B", "cg"): 180.0, ("B", "ch"): 180.0}
        for name in RECIPES:
            cell = scaledDown(name, optimise=True)
            worst = collections.defaultdict(float)
            for k, joins in joinKinds(cell).items():
                r0 = lengths.bondLength(k[0].split(":")[1], k[1].split(":")[1])
                for block, a, b, d in joins:
                    self.assertLess(abs(d - r0), 0.05, (name, k, d))
                    for atom, other in ((a, b), (b, a)):
                        key = (block._dataMap[atom][0].fragmentType, block.type(atom))
                        for value in linkers.anglesAt(block, atom, other, cell):
                            worst[key] = max(worst[key], abs(value - ideal[key]))
            for key, deviation in worst.items():
                self.assertLess(deviation, 10.0, (name, key, deviation))


if __name__ == "__main__":
    unittest.main()
