"""Structure export, format version 1 (docs/export.md), milestone E1: the extended XYZ
(with types, charges and provenance) and the topology JSON every checkpoint writes."""
import json
import os
import shlex
import shutil
import tempfile
import unittest

import numpy as np

from context import BLOCKS_DIR, PARAMS_DIR, ab_cell, xyz_core

SCHEMA = os.path.join(os.path.dirname(os.path.abspath(ab_cell.__file__)), "schemas", "topology-v1.json")
try:
    import jsonschema
except ImportError:
    jsonschema = None


def readXyz(path):
    """(header {key: value}, [atom fields by Properties name])"""
    with open(path) as f:
        lines = f.read().splitlines()
    header = dict(token.partition("=")[::2] for token in shlex.split(lines[1]))
    spec = header["Properties"].split(":")
    names = []
    for n in range(0, len(spec), 3):
        names += [spec[n]] * int(spec[n + 2])
    atoms = []
    for line in lines[2:2 + int(lines[0])]:
        fields = {}
        for name, value in zip(names, line.split()):
            fields.setdefault(name, []).append(value)
        atoms.append(fields)
    return header, atoms


class Export(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        self.rundir = os.path.join(self.tmpdir, "run")
        # a small box, so that blocks cross the periodic boundary; methane grown on benzene
        # leaves some end groups free
        self.cell = ab_cell.Cell([12, 12, 12], paramsDir=PARAMS_DIR, outputDir=self.rundir, recordRun=True, seed=7)
        self.addCleanup(self.cell.close)
        self.cell.libraryAddFragment(filename=os.path.join(BLOCKS_DIR, "benzene2.car"), fragmentType="A")
        self.cell.libraryAddFragment(filename=os.path.join(BLOCKS_DIR, "ch4.car"), fragmentType="B")
        self.cell.addBondType("A:a-B:a")
        self.cell.seed(3)
        self.cell.growBlocks(4, cellEndGroups=None, libraryEndGroups=None, maxTries=100)
        self.cell.dump()
        self.xyz = os.path.join(self.rundir, "step_1.xyz")
        self.topologyFile = os.path.join(self.rundir, "step_1.topology.json")
        with open(self.topologyFile) as f:
            self.topology = json.load(f)
        self.header, self.atoms = readXyz(self.xyz)
        self.expected = [(block, i) for block in self.cell.blocks.values() for i in range(block.numAtoms())]

    def testTheStructureHasTypesChargesAndProvenance(self):
        """test 1: types and charges are the blocks'; positions are wrapped into [0, L); the
        header's provenance is the run's"""
        self.assertEqual(self.header["Properties"], "species:S:1:pos:R:3:type:S:1:charge:R:1:fragment:S:1:block:I:1")
        self.assertEqual(len(self.atoms), len(self.expected))
        for fields, (block, i) in zip(self.atoms, self.expected):
            self.assertEqual(fields["species"], [block.symbol(i)])
            self.assertEqual(fields["type"], [block.type(i)])
            self.assertAlmostEqual(float(fields["charge"][0]), block.charge(i), places=4)
            self.assertEqual(fields["fragment"], [block.fragmentType(i)])
            self.assertEqual(int(fields["block"][0]), block.id)
            position = [float(x) for x in fields["pos"]]
            self.assertTrue(all(0.0 <= x < 12.0 for x in position), position)
            wrapped, _ = xyz_core.wrapCoord3(block.coord(i), np.array([12.0, 12.0, 12.0]), center=False)
            np.testing.assert_allclose(position, wrapped, atol=2e-6)
        with open(os.path.join(self.rundir, "run.json")) as f:
            run = json.load(f)
        self.assertEqual(self.header["run_id"], run["run_id"])
        self.assertEqual(self.header["export_version"], "1")
        self.assertEqual(self.header["ambuild_version"], run["ambuild"]["version"])

    def testTheTopologyHasEveryBondBlockAndFreeEndGroup(self):
        """test 2: every bond, each with the image that makes it the real (short) bond; the
        blocks cover every atom once; the free end groups are the cell's, with their caps"""
        t = self.topology
        self.assertEqual((t["format"], t["version"], t["atoms"]), ("ambuild-topology", 1, len(self.expected)))
        self.assertEqual(t["structure"], "step_1.xyz")
        # blocks
        covered = [0] * t["atoms"]
        for b in t["blocks"]:
            for n in range(b["start"], b["end"]):
                covered[n] += 1
        self.assertEqual(set(covered), {1})
        self.assertEqual([b["id"] for b in t["blocks"]], [block.id for block in self.cell.blocks.values()])
        # bonds: as many as the blocks have, each the real bond vector
        self.assertEqual(len(t["bonds"]), sum(len(block.bonds()) for block in self.cell.blocks.values()))
        positions = [[float(x) for x in fields["pos"]] for fields in self.atoms]
        crossing = 0
        for i, j, image in t["bonds"]:
            self.assertLess(i, j)
            (bi, ii), (bj, ij) = self.expected[i], self.expected[j]
            self.assertIs(bi, bj)  # bonded atoms are in one block
            real = np.asarray(bi.coord(ij)) - np.asarray(bi.coord(ii))
            vector = np.asarray(positions[j]) + 12.0 * np.asarray(image) - np.asarray(positions[i])
            np.testing.assert_allclose(vector, real, atol=1e-5)
            self.assertLess(np.linalg.norm(vector), 1.8 + self.cell.bondMargin)
            crossing += any(image)
        self.assertGreater(crossing, 0, "no bond crosses the boundary: the test does not test images")
        # free end groups
        free = sum(block.numFreeEndGroups() for block in self.cell.blocks.values())
        self.assertEqual(len(t["free_end_groups"]), free)
        self.assertGreater(free, 0)
        bonded = {(i, j) for i, j, _ in t["bonds"]}
        for e in t["free_end_groups"]:
            self.assertIn((min(e["atom"], e["cap"]), max(e["atom"], e["cap"])), bonded)  # the cap is bonded to it
            self.assertRegex(e["type"], r"^[AB]:a$")
        # the parameter files, by name and sha256
        self.assertIn("bond_params.csv", t["params"])

    def testExportsAreDeterministic(self):
        """test 3: the same cell writes byte-identical files; the topology names its structure's sha256"""
        first = {}
        for path in (self.xyz, self.topologyFile):
            with open(path, "rb") as f:
                first[path] = f.read()
        xyz = self.cell.writeStructure("step_1.xyz")
        self.cell.writeTopology("step_1.topology.json", xyz)
        for path, content in first.items():
            with open(path, "rb") as f:
                self.assertEqual(f.read(), content, path)
        self.assertEqual(self.topology["structure_sha256"], ab_cell._sha256File(self.xyz))

    @unittest.skipUnless(jsonschema, "needs jsonschema")
    def testTheTopologyFollowsItsSchema(self):
        """test 8: the file validates against topology-v1.json; another version does not"""
        with open(SCHEMA) as f:
            schema = json.load(f)
        jsonschema.validate(self.topology, schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(dict(self.topology, version=2), schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(dict(self.topology, bonds=[[0, 1]]), schema)


if __name__ == "__main__":
    unittest.main()
