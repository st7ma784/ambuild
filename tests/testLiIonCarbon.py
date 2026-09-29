"""The example recipe li_ion_carbon: a carbon network of benzene rings joined by alkyne
(ethynylene) linkers, whose pores should let lithium ions through.

Checked everywhere: the network forms, and grown blocks are joined at GAFF's ring-alkyne
bond length (typed bond lengths). With HOOMD-blue: after the recipe's rigid-body
optimisations every junction has the right geometry (bond length, a straight alkyne, a
trigonal ring carbon). With Poreblazer too (the ambuild-poreblazer image, run by CI's image
job): its pores let a lithium ion through, i.e. the pore limiting diameter is above a bare
Li+ (twice its Shannon ionic radius, 0.76 A) and the pore network percolates.
"""
import collections
import glob
import json
import math
import os
import re
import shutil
import statistics
import tempfile
import unittest

from context import BLOCKS_DIR, ab_util
from ambuild import recipe as ab_recipe

LITHIUM_ION_DIAMETER = 2 * 0.76  # A, bare Li+ (Shannon radius, six-coordinate)
RING_ALKYNE_BOND = 1.44  # A, GAFF 1.81 ca-c1 r0 (measured Csp-Car about 1.43-1.44 A)
TRIPLE_BOND = 1.203  # A, acetylene's measured C#C (the acetylene block; rigid in optimisation)
HAVE_POREBLAZER = bool(os.environ.get("POREBLAZER_EXE") or shutil.which("poreblazer.exe") or shutil.which("poreblazer"))


def lastStructure(rundir):
    """(box, [(element, (x, y, z), fragment type)]) of the last checkpoint"""
    files = sorted(glob.glob(os.path.join(rundir, "step_*.xyz")), key=lambda p: int(os.path.basename(p)[5:-4]))
    with open(files[-1]) as f:
        lines = f.read().splitlines()
    box = float(lines[1].split('"')[1].split()[0])
    # columns by name, from Properties (docs/export.md)
    spec = re.search(r"Properties=(\S+)", lines[1]).group(1).split(":")
    names = []
    for n in range(0, len(spec), 3):
        names += [spec[n]] * int(spec[n + 2])
    fragment = names.index("fragment")
    return box, [(a[0], tuple(map(float, a[1:4])), a[fragment]) for a in (l.split() for l in lines[2:2 + int(lines[0])])]


def _vec(a, b, box):
    return [((b[i] - a[i] + box / 2) % box) - box / 2 for i in range(3)]


def _norm(v):
    return math.sqrt(sum(x * x for x in v))


def _angle(u, v):
    return math.degrees(math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(u, v)) / (_norm(u) * _norm(v))))))


def junctions(box, atoms):
    """Bond lengths and angles where a ring carbon (fragment A) bonds to an alkyne carbon
    (B): (ca-c1 lengths, ca-c1-c1 angles, c1-ca-ca angles)"""
    ring = [a[1] for a in atoms if a[0] == "C" and a[2] == "A"]
    alkyne = [a[1] for a in atoms if a[0] == "C" and a[2] == "B"]
    lengths, straight, trigonal = [], [], []
    for p in alkyne:
        for q in ring:
            d = _norm(_vec(p, q, box))
            if d < 2.2:
                lengths.append(d)
                partner = min((r for r in alkyne if r is not p), key=lambda r: _norm(_vec(p, r, box)))
                straight.append(_angle(_vec(p, q, box), _vec(p, partner, box)))
                for r in sorted(ring, key=lambda r: _norm(_vec(q, r, box)))[1:3]:
                    trigonal.append(_angle(_vec(q, p, box), _vec(q, r, box)))
    return lengths, straight, trigonal


def linkers(box, atoms):
    """For each alkyne linker (a pair of fragment-B carbons): (C#C length, what each end
    is bonded to besides its partner), e.g. (1.203, ("CA", "HB")) for a linker bonded to
    a ring at one end that still has its hydrogen at the other"""
    alkyne = [a for a in atoms if a[0] == "C" and a[2] == "B"]
    result, seen = [], set()
    for i, a in enumerate(alkyne):
        j = min((k for k in range(len(alkyne)) if k != i), key=lambda k: _norm(_vec(a[1], alkyne[k][1], box)))
        if (j, i) in seen:
            continue
        seen.add((i, j))
        ends = []
        for end, partner in ((a, alkyne[j]), (alkyne[j], a)):
            ends.append("+".join(sorted(b[0] + b[2] for b in atoms
                                        if b is not end and b is not partner and _norm(_vec(end[1], b[1], box)) < 1.8)))
        result.append((_norm(_vec(a[1], alkyne[j][1], box)), tuple(sorted(ends))))
    return result


def withoutOptimisation(recipe):
    body = json.loads(json.dumps(recipe))
    body["stages"][1]["stages"] = [s for s in body["stages"][1]["stages"] if s["op"] != "optimise"]
    return body


class LiIonCarbon(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        # its own GAFF parameters, shipped beside it (ambuild/recipes/params/gaff_benzene_alkyne)
        self.recipe = ab_recipe.example("li_ion_carbon", blocksDir=BLOCKS_DIR)

    def build(self, body):
        rundir = os.path.join(self.tmp, "run")
        ab_recipe.run(body, rundir, baseDir="/")
        with open(os.path.join(rundir, "run.json")) as f:
            self.assertEqual(json.load(f)["status"], "finished")
        return rundir

    def testIsAShippedExample(self):
        self.assertIn("li_ion_carbon", ab_recipe.examples())
        self.assertEqual(ab_recipe.validate(self.recipe, allowPaths=True), [])
        self.assertEqual({f["name"] for f in self.recipe["fragments"]}, {"benzene_135", "acetylene"})
        self.assertTrue(all(os.path.isfile(p) for p in self.recipe["params"].values()))

    def testBuildsAnAlkyneLinkedNetwork(self):
        body = withoutOptimisation(dict(self.recipe, stages=self.recipe["stages"][:-1]))
        box, atoms = lastStructure(self.build(body))
        byFragment = collections.Counter(a[2] for a in atoms)
        self.assertGreater(byFragment["A"] // 9, 20)  # rings: C6H3 once linked
        self.assertGreater(byFragment["B"] // 2, 20)  # linkers: C2 once linked at both ends
        lengths, straight, trigonal = junctions(box, atoms)
        # grown blocks are placed at GAFF's ca-c1 length (typed bond lengths), not the
        # generic C-C single bond (1.53 A); zipped bonds may be off until optimised
        placed = [d for d in lengths if abs(d - RING_ALKYNE_BOND) < 0.005]
        self.assertGreater(len(placed), 0.8 * len(lengths), statistics.median(lengths))

    def assertLinkersIntact(self, box, atoms):
        """Every linker keeps its triple bond, and each end bonds to one ring carbon or
        (a linker end left free) its own hydrogen: never to another linker"""
        found = linkers(box, atoms)
        self.assertGreater(len(found), 20)
        for length, ends in found:
            self.assertAlmostEqual(length, TRIPLE_BOND, delta=0.01)
            self.assertTrue(set(ends) <= {"CA", "HB"}, ends)
        # most linkers join two rings
        self.assertGreater(sum(ends == ("CA", "CA") for _, ends in found), len(found) / 2)

    def testBuildIsReproducible(self):
        """The same recipe and seed give the same structure: placing linear alkynes takes
        (anti)parallel alignments, where rounding once made the result vary between runs"""
        body = withoutOptimisation(dict(self.recipe, stages=self.recipe["stages"][:-1]))
        first = lastStructure(self.build(body))
        shutil.rmtree(os.path.join(self.tmp, "run"))
        self.assertEqual(lastStructure(self.build(body)), first)
        # before optimisation contacts can be close (ring H to alkyne C about 1.65 A), so
        # only the (rigid) triple bonds are checked here; the ends are, once optimised
        lengths = [length for length, _ in linkers(*first)]
        self.assertGreater(len(lengths), 20)
        self.assertLess(max(abs(d - TRIPLE_BOND) for d in lengths), 0.01, lengths)

    def assertTopologyHasTheBonds(self, rundir, box, atoms):
        """The exported topology (docs/export.md, test 9) holds every C#C and ring-alkyne
        bond, at the lengths measured here, and nothing longer than 1.8 A"""
        files = sorted(glob.glob(os.path.join(rundir, "step_*.topology.json")),
                       key=lambda p: int(os.path.basename(p)[5:].split(".")[0]))
        with open(files[-1]) as f:
            topology = json.load(f)
        self.assertEqual(topology["atoms"], len(atoms))
        byKind = collections.defaultdict(list)
        for i, j, image in topology["bonds"]:
            vector = [atoms[j][1][k] + image[k] * box - atoms[i][1][k] for k in range(3)]
            length = _norm(vector)
            self.assertLess(length, 1.8, (i, j, image))
            byKind[tuple(sorted((atoms[i][0] + atoms[i][2], atoms[j][0] + atoms[j][2])))].append(length)
        triple = byKind[("CB", "CB")]
        ringAlkyne = byKind[("CA", "CB")]
        self.assertEqual(len(triple), len(linkers(box, atoms)))
        self.assertLess(max(abs(d - TRIPLE_BOND) for d in triple), 0.01)
        self.assertEqual(len(ringAlkyne), len(junctions(box, atoms)[0]))
        self.assertLess(max(abs(d - RING_ALKYNE_BOND) for d in ringAlkyne), 0.05)

    @unittest.skipUnless(ab_util.HOOMDVERSION, "Needs HOOMD-blue")
    def testOptimisedJunctionsHaveTheRightGeometry(self):
        rundir = self.build(dict(self.recipe, stages=self.recipe["stages"][:-1]))
        box, atoms = lastStructure(rundir)
        self.assertLinkersIntact(box, atoms)
        self.assertTopologyHasTheBonds(rundir, box, atoms)
        lengths, straight, trigonal = junctions(box, atoms)
        self.assertGreater(len(lengths), 20)
        self.assertLess(max(abs(d - RING_ALKYNE_BOND) for d in lengths), 0.05, lengths)
        self.assertLess(max(abs(a - 180) for a in straight), 10, straight)
        self.assertLess(max(abs(a - 120) for a in trigonal), 10, trigonal)

    @unittest.skipUnless(HAVE_POREBLAZER and ab_util.HOOMDVERSION, "Needs Poreblazer and HOOMD-blue")
    def testPoresLetLithiumIonsThrough(self):
        rundir = self.build(self.recipe)
        with open(os.path.join(rundir, "events.jsonl")) as f:
            results = [json.loads(line)["data"] for line in f if '"pore_result"' in line]
        self.assertEqual(len(results), 1)
        pore = results[0]
        self.assertEqual(pore["returncode"], 0)
        self.assertGreater(pore["pore_limiting_diameter_A"], LITHIUM_ION_DIAMETER)
        self.assertGreaterEqual(pore["percolated_dimensions"], 1)


if __name__ == "__main__":
    unittest.main()
