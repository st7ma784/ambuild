"""Carbon linkers that join themselves and each other (docs/carbon-linkers.md): benzene
rings (C6), acetylenes typed for polyyne links (C2: cg#ch), trigonal sp2 carbon nodes,
allene (a C3 cumulene), cyclopropenyl nodes (a C3 ring) and propargyl linkers, with GAFF
1.81 parameters (ambuild/recipes/params/gaff_carbon).

Everywhere: the blocks' geometry and end groups; every join the recipes allow has a
bond length, and the joins GAFF would get wrong are not allowed; a network forms with
every kind of join, each placed at its r0. With HOOMD-blue: after the recipes'
rigid-body optimisations, every join keeps its length and the angles at its atoms.
"""
import collections
import glob
import math
import os
import shutil
import tempfile
import unittest

import numpy as np

from context import BLOCKS_DIR, ab_cell, ab_util
from ambuild import recipe as ab_recipe
from ambuild import xyz_util

PARAMS = os.path.join(os.path.dirname(os.path.abspath(ab_recipe.__file__)), "recipes", "params", "gaff_carbon")
# joins GAFF would give the wrong length: an alkyne's cg end to another's ch end (the
# triple bond's), and cyclopropenyl-cyclopropenyl or allene-cyclopropenyl (double bonds')
FORBIDDEN = {frozenset(("cg", "ch")), frozenset(("cu",)), frozenset(("ce", "cu"))}


def bondLengths():
    return xyz_util.BondLength(os.path.join(PARAMS, "bond_params.csv"), typed=True)


def angle(u, v):
    return math.degrees(math.acos(max(-1.0, min(1.0, np.dot(u, v) / (np.linalg.norm(u) * np.linalg.norm(v))))))


def junctions(cell):
    """[(block, atom a, atom b)] for bonds between different fragments"""
    out = []
    for block in cell.blocks.values():
        for a, b in block.bonds():
            if block._dataMap[a][0] is not block._dataMap[b][0]:
                out.append((block, a, b))
    return out


def kind(block, a, b):
    return tuple(sorted(("{0}:{1}".format(block._dataMap[i][0].fragmentType, block.type(i)) for i in (a, b))))


def anglesAt(block, a, b):
    """The angles at atom a between its junction bond to b and its other bonds"""
    here = np.asarray(block.coord(a))
    out = []
    for n in block._bondedToAtom[a]:
        if n != b:
            out.append(angle(np.asarray(block.coord(b)) - here, np.asarray(block.coord(n)) - here))
    return out


def fragmentsOf(body):
    """{fragment type: (atom types, {atom: set of bonded atoms}, [(end group type, atom, cap)])}"""
    out = {}
    for frag in body["fragments"]:
        cell = ab_cell.Cell([30, 30, 30], paramsDir=PARAMS)
        cell.libraryAddFragment(filename=frag["car"], fragmentType=frag["type"])
        f = cell._fragmentLibrary[frag["type"]]
        n = len(list(f.iterCoord()))
        types = [f.type(i) for i in range(n)]
        neighbours = {i: set() for i in range(n)}
        for a, b in f.bonds():
            neighbours[a].add(b)
            neighbours[b].add(a)
        ends = [(e.type().split(":")[1], e.fragmentEndGroupIdx, e.fragmentCapIdx) for e in f.endGroups()]
        out[frag["type"]] = (types, neighbours, ends)
    return out


def possibleTerms(body):
    """(angle types, dihedral types) that joins allowed by the recipe's bond types can
    create. Each end group's position holds its cap (unbonded) or any partner it may join."""
    frags = fragmentsOf(body)
    partners = collections.defaultdict(set)  # (fragment, end group type) -> partner linking atom types
    for bt in body["bond_types"]:
        (f1, e1), (f2, e2) = (end.split(":") for end in bt.split("-"))
        for (fa, ea), (fb, eb) in (((f1, e1), (f2, e2)), ((f2, e2), (f1, e1))):
            types, _, ends = frags[fb]
            partners[(fa, ea)] |= {types[atom] for t, atom, cap in ends if t == eb}
    angles, dihedrals = set(), set()
    for ftype, (types, neighbours, ends) in frags.items():
        for atom in {a for _, a, _ in ends}:
            caps = {cap for _, a, cap in ends if a == atom}
            slots = [{types[n]} for n in neighbours[atom] if n not in caps]  # fixed neighbours
            mine = [e for e in ends if e[1] == atom]
            slots += [{types[cap]} | partners[(ftype, t)] for t, _, cap in mine]
            for i in range(len(slots)):
                for j in range(i + 1, len(slots)):
                    angles |= {(x, types[atom], y) for x in slots[i] for y in slots[j]}
            # dihedrals through this atom and a partner: X-atom-partner-Y
            for k, (t, _, cap) in enumerate(mine):
                others = slots[:len(slots) - len(mine)] + [s for m, s in enumerate(slots[len(slots) - len(mine):]) if m != k]
                for p in partners[(ftype, t)]:
                    for x in set().union(*others) if others else set():
                        for (g, (gtypes, gneigh, gends)) in frags.items():
                            for gt, gatom, gcap in gends:
                                if gtypes[gatom] != p or types[atom] not in partners[(g, gt)]:
                                    continue
                                gcaps = {c for _, a, c in gends if a == gatom}
                                ys = {gtypes[n] for n in gneigh[gatom] if n not in gcaps}
                                ys |= set().union(*[{gtypes[c]} | partners[(g, et)] for et, a, c in gends
                                                    if a == gatom and c != gcap]) if len(gcaps) > 1 else set()
                                dihedrals |= {(x, types[atom], p, y) for y in ys}
                    # and a neighbour's neighbour: Z-N-atom-partner
                    for n in neighbours[atom]:
                        if n in caps:
                            continue
                        for z in neighbours[n] - {atom}:
                            dihedrals |= {(types[z], types[n], types[atom], p) for p in partners[(ftype, t)]}
    return angles, dihedrals


def build(name, optimise):
    """The last checkpoint's cell of an example recipe, without Poreblazer, and
    without its optimisations unless optimise"""
    body = ab_recipe.example(name, blocksDir=BLOCKS_DIR)
    body["stages"] = [s for s in body["stages"] if s.get("op") != "poreblazer"]
    if not optimise:
        for s in body["stages"]:
            if "repeat" in s:
                s["stages"] = [x for x in s["stages"] if x["op"] != "optimise"]
    tmp = tempfile.mkdtemp()
    try:
        run = os.path.join(tmp, "run")
        ab_recipe.run(body, run, baseDir="/")
        last = max(glob.glob(os.path.join(run, "step_*.pkl.gz")), key=lambda p: int(os.path.basename(p)[5:].split(".")[0]))
        return ab_util.cellFromPickle(last, paramsDir=PARAMS)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class Blocks(unittest.TestCase):
    def fragment(self, name):
        cell = ab_cell.Cell([30, 30, 30], paramsDir=PARAMS)
        cell.libraryAddFragment(filename=os.path.join(BLOCKS_DIR, name + ".car"), fragmentType="A")
        return cell._fragmentLibrary["A"]

    def coords(self, frag):
        return [np.asarray(c) for c in frag.iterCoord()]

    def testCarbonNode(self):
        frag = self.fragment("carbon_node")
        c = self.coords(frag)
        self.assertEqual(len(frag.endGroups()), 3)
        self.assertEqual(frag.type(0), "ca")
        for i in (1, 2, 3):
            self.assertAlmostEqual(np.linalg.norm(c[i] - c[0]), 1.084, places=3)
        self.assertAlmostEqual(angle(c[1] - c[0], c[2] - c[0]), 120.0, places=3)
        self.assertAlmostEqual(np.linalg.norm(np.cross(c[2] - c[0], c[3] - c[0]) @ (c[1] - c[0])), 0.0, places=6)  # planar

    def testAcetyleneForPolyynes(self):
        frag = self.fragment("acetylene_cg")
        self.assertEqual([frag.type(i) for i in range(2)], ["cg", "ch"])
        self.assertEqual(sorted(e.type() for e in frag.endGroups()), ["A:g", "A:h"])
        c = self.coords(frag)
        self.assertAlmostEqual(np.linalg.norm(c[1] - c[0]), 1.203, places=3)

    def testAllene(self):
        frag = self.fragment("allene")
        c = self.coords(frag)
        self.assertEqual([frag.type(i) for i in range(3)], ["ce", "c1", "ce"])
        self.assertAlmostEqual(np.linalg.norm(c[1] - c[0]), 1.308, places=3)
        self.assertAlmostEqual(angle(c[0] - c[1], c[2] - c[1]), 180.0, places=3)
        self.assertAlmostEqual(angle(c[1] - c[0], c[3] - c[0]), 121.2, places=2)
        # the two ends' planes are perpendicular
        n1 = np.cross(c[3] - c[0], c[4] - c[0])
        n2 = np.cross(c[5] - c[2], c[6] - c[2])
        self.assertAlmostEqual(angle(n1, n2), 90.0, places=3)
        self.assertEqual(len(frag.endGroups()), 2)

    def testCyclopropenyl(self):
        frag = self.fragment("cyclopropenyl")
        c = self.coords(frag)
        self.assertEqual([frag.type(i) for i in range(3)], ["cu", "cu", "cu"])
        for i, j in ((0, 1), (1, 2), (2, 0)):
            self.assertAlmostEqual(np.linalg.norm(c[j] - c[i]), 1.363, places=3)
        self.assertAlmostEqual(angle(c[1] - c[0], c[3] - c[0]), 150.0, places=3)  # exocyclic, radial
        self.assertEqual(len(frag.endGroups()), 3)

    def testPropargyl(self):
        frag = self.fragment("propargyl")
        c = self.coords(frag)
        self.assertEqual([frag.type(i) for i in range(3)], ["c3", "cg", "ch"])
        self.assertAlmostEqual(angle(c[1] - c[0], c[4] - c[0]), 109.47, places=1)  # tetrahedral CH2
        self.assertEqual(sorted(e.type() for e in frag.endGroups()), ["A:c", "A:h"])


class Recipes(unittest.TestCase):
    def testTheyAreShippedAndValid(self):
        for name in ("carbon_nodes_network", "carbon_all_linkers"):
            self.assertIn(name, ab_recipe.examples())
            self.assertEqual(ab_recipe.validate(ab_recipe.example(name, blocksDir=BLOCKS_DIR), allowPaths=True), [])

    def testEveryAllowedJoinHasABondLengthAndNoneIsWrong(self):
        lengths = bondLengths()
        for name in ("carbon_nodes_network", "carbon_all_linkers"):
            body = ab_recipe.example(name, blocksDir=BLOCKS_DIR)
            linkTypes = {}
            for frag in body["fragments"]:
                with open(frag["csv"]) as f:
                    rows = [line.strip().split(",") for line in f if line.strip()][1:]
                with open(frag["car"]) as f:
                    types = [line.split()[6] for line in f.read().splitlines()[4:] if len(line.split()) > 7]
                for row in rows:
                    linkTypes["{0}:{1}".format(frag["type"], row[0])] = types[int(row[1])]
            for bt in body["bond_types"]:
                t1, t2 = (linkTypes[end] for end in bt.split("-"))
                self.assertNotIn(frozenset((t1, t2)), FORBIDDEN, (name, bt))
                self.assertTrue(1.3 < lengths.bondLength(t1, t2) < 1.55, (name, bt, lengths.bondLength(t1, t2)))

    def testEveryAngleAndDihedralAJoinCanMakeHasParameters(self):
        """Every angle and dihedral type the recipes' joins can create is in the parameter
        files (else HOOMD-blue stops: "The following parameters could not be found")"""
        with open(os.path.join(PARAMS, "angle_params.csv")) as f:
            angles = {line.split(",")[0] for line in f.read().splitlines()[1:]}
        with open(os.path.join(PARAMS, "dihedral_params.csv")) as f:
            dihedrals = {line.split(",")[0] for line in f.read().splitlines()[1:]}
        missing = set()
        for name in ("carbon_nodes_network", "carbon_all_linkers"):
            needAngles, needDihedrals = possibleTerms(ab_recipe.example(name, blocksDir=BLOCKS_DIR))
            missing |= {"angle " + "-".join(a) for a in needAngles
                        if "-".join(a) not in angles and "-".join(a[::-1]) not in angles}
            missing |= {"dihedral " + "-".join(d) for d in needDihedrals
                        if "-".join(d) not in dihedrals and "-".join(d[::-1]) not in dihedrals}
        self.assertEqual(sorted(missing), [])

    def testThePolyyneLinkIsASingleBond(self):
        lengths = bondLengths()
        self.assertAlmostEqual(lengths.bondLength("cg", "cg"), 1.3693)
        self.assertAlmostEqual(lengths.bondLength("cg", "ch"), 1.2056)  # the triple bond: never a join


class Networks(unittest.TestCase):
    def assertJoins(self, cell, expectedKinds):
        lengths = bondLengths()
        found = collections.defaultdict(list)
        for block, a, b in junctions(cell):
            found[kind(block, a, b)].append(np.linalg.norm(np.asarray(block.coord(a)) - np.asarray(block.coord(b))))
        for k in found:
            self.assertNotIn(frozenset(t.split(":")[1] for t in k), FORBIDDEN, k)
        self.assertGreaterEqual(set(found), expectedKinds)
        return found, lengths

    def testTheNodesNetworkMakesEveryJoin(self):
        cell = build("carbon_nodes_network", optimise=False)
        kinds = {("A:cp", "A:cp"), ("A:cp", "B:cg"), ("A:cp", "B:ch"), ("A:cp", "C:ca"), ("B:cg", "B:cg"),
                 ("B:cg", "C:ca"), ("B:ch", "B:ch"), ("B:ch", "C:ca"), ("C:ca", "C:ca")}
        found, lengths = self.assertJoins(cell, kinds)
        for k, values in found.items():  # grown blocks are placed at the join's r0
            r0 = lengths.bondLength(k[0].split(":")[1], k[1].split(":")[1])
            self.assertGreater(sum(abs(v - r0) < 0.005 for v in values), 0.8 * len(values), (k, r0, values))

    def testAllSixLinkersJoinWithoutTheWrongBonds(self):
        cell = build("carbon_all_linkers", optimise=False)
        types = collections.Counter(f.fragmentType for b in cell.blocks.values() for f in b.fragments)
        self.assertEqual(set(types), set("ABCDEF"))
        self.assertJoins(cell, {("A:cp", "D:ce"), ("A:cp", "E:cu"), ("C:ca", "F:c3"), ("B:ch", "F:ch")})

    @unittest.skipUnless(ab_util.HOOMDVERSION, "Needs HOOMD-blue")
    def testOptimisedJoinsKeepTheirGeometry(self):
        """After the rigid-body optimisations with dihedrals: every join within 0.05 A of
        its r0, and the angles at its atoms within 10 degrees of their ideal: 120 at ring
        and node carbons, 180 along alkynes, 148 at the cyclopropenyl ring (exocyclic),
        about 120 at allene ends and 109.5 at propargyl CH2"""
        ideal = {"cp": 120.0, "ca": 120.0, "cg": 180.0, "ch": 180.0, "ce": 120.0, "cu": 148.0, "c3": 109.5}
        for name in ("carbon_nodes_network", "carbon_all_linkers"):
            cell = build(name, optimise=True)
            lengths = bondLengths()
            worst = collections.defaultdict(float)
            for block, a, b in junctions(cell):
                t1, t2 = block.type(a), block.type(b)
                d = np.linalg.norm(np.asarray(block.coord(a)) - np.asarray(block.coord(b)))
                self.assertLess(abs(d - lengths.bondLength(t1, t2)), 0.05, (name, kind(block, a, b), d))
                for atom, other, t in ((a, b, t1), (b, a, t2)):
                    for value in anglesAt(block, atom, other):
                        if t == "cu" and value < 90:  # the other ring bond's side: 360 - 60 - 148
                            continue
                        worst[t] = max(worst[t], abs(value - ideal[t]))
            for t, deviation in worst.items():
                self.assertLess(deviation, 10.0, (name, t, deviation))


if __name__ == "__main__":
    unittest.main()
