"""sp3-sp2 networks (docs/sp3-sp2-networks.md): the tetraphenylmethane node, the two recipes,
and, where liminal is installed, the π-conduction analysis of their builds.

As in testCarbonFamilies, the recipes' 60 A builds are too large for the suite, so the
builds are scaled down (a 30 A cell, two passes, no Poreblazer or conduction stage)."""
import collections
import copy
import itertools
import os
import shutil
import subprocess
import tempfile
import unittest

import numpy as np

from context import BLOCKS_DIR, ab_util
from ambuild import campaign as ab_campaign
from ambuild import conduction as ab_conduction
from ambuild import gallery as ab_gallery
from ambuild import recipe as ab_recipe
from ambuild import xyz_util
import testCarbonFamilies as families
import testCarbonLinkers as linkers
from testIonMap import REAL

RECIPES = ("tpm_phenylene_large", "tpm_sp2_network_large")
SP2 = {"P", "R", "N"}  # phenylene, 1,3,5-benzene, trigonal carbon


class Node(unittest.TestCase):
    def setUp(self):
        self.f, self.c = families.fragment("tetraphenylmethane", families.PAF)

    def testComposition(self):
        types = [self.f.type(i) for i in range(len(self.c))]
        self.assertEqual(len(self.c), 45)  # C25H20
        self.assertEqual(collections.Counter(types), {"c3": 1, "cp": 8, "ca": 16, "ha": 20})
        self.assertEqual(types[0], "c3")

    def testFourPhenylArmsAtTheTetrahedralAngle(self):
        ipso = [1 + 6 * k for k in range(4)]
        for i in ipso:
            self.assertAlmostEqual(np.linalg.norm(self.c[i] - self.c[0]), 1.5156, places=4)
        for i, j in itertools.combinations(ipso, 2):
            self.assertAlmostEqual(linkers.angle(self.c[i] - self.c[0], self.c[j] - self.c[0]), 109.47, places=1)
        for k in range(4):  # each ring regular, 1.397 A
            ring = self.c[1 + 6 * k:7 + 6 * k]
            for n in range(6):
                self.assertAlmostEqual(np.linalg.norm(ring[n] - ring[(n + 1) % 6]), 1.397, places=4)

    def testLinkedAtTheParaCarbonsAndArmsDoNotCrowd(self):
        ends = sorted(e.fragmentEndGroupIdx for e in self.f.endGroups())
        self.assertEqual(ends, [4, 10, 16, 22])  # para to each ipso carbon
        for p in ends:
            self.assertEqual(self.f.type(p), "cp")
        hydrogens = self.c[25:]
        closest = min(np.linalg.norm(hydrogens[i] - hydrogens[j])
                      for i in range(20) for j in range(20) if i // 5 != j // 5)
        self.assertGreater(closest, 2.4)  # the propeller twist keeps ortho H apart (vdW contact 2.4 A)


class Recipes(unittest.TestCase):
    def testTopologies(self):
        expected = {
            "tpm_phenylene_large": ["T:t-P:a"],
            "tpm_sp2_network_large": ["T:t-T:t", "T:t-P:a", "T:t-R:a", "T:t-N:a", "P:a-P:a", "P:a-R:a", "P:a-N:a",
                                      "R:a-R:a", "R:a-N:a", "N:a-N:a"],
        }
        for name in RECIPES:
            body = ab_recipe.example(name, blocksDir=BLOCKS_DIR)
            self.assertEqual(ab_recipe.validate(body, allowPaths=True), [], name)
            self.assertEqual(body["bond_types"], expected[name])
            self.assertEqual(body["cell"]["box"], [60, 60, 60])
            self.assertEqual([s.get("op") for s in body["stages"]][-2:], ["poreblazer", "conduction"])
            seeds = [s for s in body["stages"] if s.get("op") == "seed"]
            self.assertEqual(seeds[0]["fragment_type"], "T")

    def testEveryAngleAndDihedralAJoinCanMakeHasParameters(self):
        missing = set()
        for name in RECIPES:
            body = ab_recipe.example(name, blocksDir=BLOCKS_DIR)
            params = families.paramsDir(body)
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

    def testTheExampleCampaignFitsTheNetworkRecipe(self):
        spec = ab_campaign.examples()["semiconducting_sp3_sp2"]
        body = ab_recipe.example("tpm_sp2_network_large", blocksDir=BLOCKS_DIR)
        ref = "sha256:" + "0" * 64  # as the web GUI holds it: files by reference, not path
        for frag in body["fragments"]:
            frag["car"] = frag["csv"] = ref
        body["params"] = {name: ref for name in body["params"]}
        self.assertEqual(ab_campaign.validate(spec, body), [])
        self.assertEqual(ab_campaign.objectiveMetric(spec), "el_conductance")
        self.assertIn({"metric": "el_radical_domains", "max": 0}, spec["constraints"])

    def testInTheGallery(self):
        family = next(f for f in ab_gallery.catalogue() if f["id"] == "sp3-sp2")
        self.assertEqual(family["recipes"], list(RECIPES))
        self.assertIn("π conduction (liminal)", ab_gallery.summarise("tpm_sp2_network_large")["measures"])


class Builds(unittest.TestCase):
    def testOnlyTheIntendedJoinsAndNoSp3Sp3(self):
        for name in RECIPES:
            cell, params = families.scaledDown(name, optimise=False)
            lengths = xyz_util.BondLength(os.path.join(params, "bond_params.csv"), typed=True)
            found = collections.defaultdict(list)
            for block, a, b in linkers.junctions(cell):
                found[linkers.kind(block, a, b)].append(linkers.length(cell, block, a, b))
            self.assertTrue(found, name)
            for k in found:
                self.assertNotIn(":c3", k[0] + k[1], (name, k))  # every join is between sp2 carbons
                frags = {k[0].split(":")[0], k[1].split(":")[0]}
                if name == "tpm_phenylene_large":
                    self.assertEqual(frags, {"T", "P"}, k)  # strictly node arm to phenylene
                else:
                    self.assertLessEqual(frags, SP2 | {"T"}, k)
            for k, ds in found.items():
                r0 = lengths.bondLength(k[0].split(":")[1], k[1].split(":")[1])
                self.assertGreater(sum(abs(d - r0) < 0.005 for d in ds), 0.8 * len(ds), (name, k))
            # every sp3 carbon keeps four sp2 neighbours: its arms are part of its block
            for block in cell.blocks.values():
                for atom in range(block.numAtoms()):
                    if block.type(atom) == "c3":
                        self.assertEqual(sorted(block.type(n) for n in block.atomBonded1(atom)), ["cp"] * 4)

    @unittest.skipUnless(ab_util.HOOMDVERSION, "Needs HOOMD-blue")
    def testOptimisedJoinsKeepTheirGeometry(self):
        """Joins within 0.05 A of r0 after optimisation, 0.06 at the trigonal carbons; angles
        within 10 degrees of 120"""
        ideal = {"cp": 120.0, "ca": 120.0}
        for name in RECIPES:
            cell, params = families.scaledDown(name, optimise=True)
            lengths = xyz_util.BondLength(os.path.join(params, "bond_params.csv"), typed=True)
            for block, a, b in linkers.junctions(cell):
                d = linkers.length(cell, block, a, b)
                # a trigonal carbon's three aryl neighbours crowd their ortho hydrogens (as in
                # trityl and tetraphenylethylene), and rigid blocks cannot twist to relieve it:
                # its joins stretch 0.04-0.055 A (docs/sp3-sp2-networks.md)
                tolerance = 0.06 if "N:" in "".join(linkers.kind(block, a, b)) else 0.05
                self.assertLess(abs(d - lengths.bondLength(block.type(a), block.type(b))), tolerance, (name, d))
                for atom, other in ((a, b), (b, a)):
                    for value in linkers.anglesAt(block, atom, other, cell):
                        self.assertLess(abs(value - ideal[block.type(atom)]), 10.0, (name, block.type(atom), value))


@unittest.skipUnless(REAL, "Needs liminal (LIMINAL_EXE, or liminal installed)")
class RealConduction(unittest.TestCase):
    """liminal's conduct on scaled-down builds: tetraphenylmethane's sp3 carbon joins its four
    arms only by tunnelling (six sp3 bridges per node); graphyne has none"""

    def conduct(self, name):
        cell, _ = families.scaledDown(name, optimise=False)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        structure = cell.writeStructure(os.path.join(tmp, "s.xyz"))
        cell.writeTopology(os.path.join(tmp, "s.topology.json"), structure)
        out = os.path.join(tmp, "conduct.json")
        command = ab_conduction.executable(REAL)
        subprocess.run(command + ["conduct", structure, "--out", out], check=True, capture_output=True)
        return cell, ab_conduction.readResults(out)

    def testTheSp3NodesAreTheOnlyBreaksInConjugation(self):
        cell, data = self.conduct("tpm_phenylene_large")
        nodes = sum(1 for b in cell.blocks.values() for f in b.fragments if f.fragmentType == "T")
        self.assertEqual(data["sp3_bridges"], 6 * nodes)  # the four arms, pairwise
        self.assertEqual(data["sp3_bridges_by_length"], {"1": 6 * nodes})
        carbons = sum(1 for b in cell.blocks.values() for i in range(b.numAtoms()) if b.symbol(i) == "C")
        self.assertEqual(data["sites"], carbons - nodes)  # every carbon but the sp3 centres
        self.assertGreater(data["domains"], 1)
        if data["conductance"] > 0:
            self.assertGreater(data["tunnelling_share"], 0.99)  # nothing conjugated crosses a node

    def testAnAllSp2SpNetworkHasNoBridges(self):
        _, data = self.conduct("graphyne_large")
        self.assertEqual((data["sp3_bridges"], data["domains"]), (0, 1))
        self.assertGreater(data["sp_sites"], 0)


if __name__ == "__main__":
    unittest.main()
