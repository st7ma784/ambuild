"""The example recipe benzene_network: benzene rings linked directly (biaryl bonds), with
GAFF's biaryl typing (the linking carbons are cp) and typed bond lengths.

Checked everywhere: grown rings are joined at GAFF's cp-cp length, 1.4854 A (a measured
biaryl link is about 1.48-1.49 A), not the generic C-C single bond (1.53 A). With
HOOMD-blue: after the recipe's rigid-body optimisations (with dihedrals) every link is within
0.05 A of it, makes 120 +- 10 degrees with its ring's bonds, and linked rings twist by a
physical amount (median 20-60 degrees; biphenyl's is about 44 in the gas phase).
"""
import json
import math
import os
import shutil
import tempfile
import unittest

import numpy as np

from context import BLOCKS_DIR, ab_util
from ambuild import recipe as ab_recipe

BIARYL_LINK = 1.4854  # A, GAFF 1.81 cp-cp r0
RING_BOND = 1.397  # A, the blocks' (rigid) ring bonds


def lastStructure(rundir):
    """(box, carbon positions) of the last checkpoint"""
    names = [n for n in os.listdir(rundir) if n.startswith("step_") and n.endswith(".xyz")]
    last = max(names, key=lambda n: int(n[5:-4]))
    with open(os.path.join(rundir, last)) as f:
        lines = f.read().splitlines()
    box = float(lines[1].split('"')[1].split()[0])
    atoms = [l.split() for l in lines[2:2 + int(lines[0])]]
    return box, np.array([[float(x) for x in a[1:4]] for a in atoms if a[0] == "C"])


def links(box, carbons):
    """(link lengths, ring-link angles, twists between linked rings' planes): the rings are
    the carbons joined by ring bonds (exactly 1.397 A in rigid blocks), the links C-C
    contacts under 2.2 A between different rings"""
    d = carbons[:, None, :] - carbons[None, :, :]
    d -= box * np.round(d / box)
    dist = np.linalg.norm(d, axis=2)
    ringBond = np.abs(dist - RING_BOND) < 0.01
    ring = -np.ones(len(carbons), dtype=int)
    for start in range(len(carbons)):
        if ring[start] < 0:
            stack, ring[start] = [start], start
            while stack:
                i = stack.pop()
                for j in np.nonzero(ringBond[i])[0]:
                    if ring[j] < 0:
                        ring[j] = start
                        stack.append(j)

    def normal(r):
        members = np.nonzero(ring == r)[0]
        pts = carbons[members[0]] - d[members[0], members]  # unwrapped about the first member
        return np.linalg.svd(pts - pts.mean(axis=0))[2][2]

    lengths, angles, twists = [], [], []
    for i in range(len(carbons)):
        for j in range(i + 1, len(carbons)):
            if ring[i] != ring[j] and dist[i, j] < 2.2:
                lengths.append(dist[i, j])
                for a, b in ((i, j), (j, i)):
                    for k in np.nonzero(ringBond[a])[0]:
                        u, v = -d[a, b], -d[a, k]  # a->b, a->k
                        angles.append(math.degrees(math.acos(np.dot(u, v) / (np.linalg.norm(u) * np.linalg.norm(v)))))
                twists.append(math.degrees(math.acos(min(1.0, abs(np.dot(normal(ring[i]), normal(ring[j])))))))
    return lengths, angles, twists


def withoutOptimisation(recipe):
    body = json.loads(json.dumps(recipe))
    body["stages"][1]["stages"] = [s for s in body["stages"][1]["stages"] if s["op"] != "optimise"]
    return body


class BenzeneNetwork(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.recipe = ab_recipe.example("benzene_network", blocksDir=BLOCKS_DIR)
        self.recipe["stages"] = self.recipe["stages"][:-1]  # without Poreblazer

    def build(self, body):
        rundir = os.path.join(self.tmp, "run")
        ab_recipe.run(body, rundir, baseDir="/")
        return lastStructure(rundir)

    def testIsAShippedExample(self):
        self.assertIn("benzene_network", ab_recipe.examples())
        self.assertEqual(ab_recipe.validate(self.recipe, allowPaths=True), [])

    def testRingsAreJoinedAtTheBiarylLength(self):
        lengths, angles, twists = links(*self.build(withoutOptimisation(self.recipe)))
        self.assertGreater(len(lengths), 20)
        placed = [x for x in lengths if abs(x - BIARYL_LINK) < 0.005]  # grown; zipped ones may be off
        self.assertGreater(len(placed), 0.8 * len(lengths), sorted(lengths)[len(lengths) // 2])

    @unittest.skipUnless(ab_util.HOOMDVERSION, "Needs HOOMD-blue")
    def testOptimisedLinksHaveTheRightGeometry(self):
        lengths, angles, twists = links(*self.build(self.recipe))
        self.assertGreater(len(lengths), 20)
        self.assertLess(max(abs(x - BIARYL_LINK) for x in lengths), 0.05, lengths)
        self.assertLess(max(abs(a - 120) for a in angles), 10, angles)
        # GAFF's biaryl torsion (the recipe optimises with dihedrals) against the ortho
        # hydrogens' clash: twisted, but far from perpendicular (biphenyl: about 44 degrees in
        # the gas phase, flatter when packed); without the torsion the rings go to about 83
        twists.sort()
        self.assertTrue(20 < twists[len(twists) // 2] < 60, twists)


if __name__ == "__main__":
    unittest.main()
