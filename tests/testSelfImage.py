"""A block larger than the cell can wrap round onto its own periodic image. closeAtoms
checks a moved block against the other blocks only, so before Cell._selfImageClashes such
overlaps went unnoticed: joinBlocks placed a 90 A cluster in a 60 A cell with atoms 0.5 A
from their own images, and the next optimisation blew up ("Particle ... is no longer in
the simulation box"; paf1_large, docs/carbon-families.md)."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import numpy as np

from context import BLOCKS_DIR, PARAMS_DIR, ab_cell


def cellWithBenzene(box):
    cell = ab_cell.Cell([box, box, box], paramsDir=PARAMS_DIR)
    cell.libraryAddFragment(filename=os.path.join(BLOCKS_DIR, "benzene.car"), fragmentType="A")
    block = cell.getLibraryBlock(fragmentType="A")
    block.translateCentroid(np.array([box / 2.0] * 3))
    return cell, cell.addBlock(block)


class SelfImage(unittest.TestCase):
    def testABlockWiderThanTheCellClashesWithItself(self):
        cell, idx = cellWithBenzene(4.0)  # benzene with its hydrogens is about 5 A across
        self.assertGreater(cell._selfImageClashes(cell.blocks[idx]), 0)
        self.assertFalse(cell.checkMove(idx))

    def testTheSameBlockFitsALargerCell(self):
        cell, idx = cellWithBenzene(30.0)
        self.assertEqual(cell._selfImageClashes(cell.blocks[idx]), 0)
        self.assertTrue(cell.checkMove(idx))

    def testTheCountMatchesABruteForceCheck(self):
        """Every pair of the block's atoms, checked through the nearest image: only pairs that
        meet through an image (not directly) and are not bonded to each other count"""
        cell, idx = cellWithBenzene(4.0)
        block = cell.blocks[idx]
        clashes = cell._selfImageClashes(block)
        bonded = {(min(a, b), max(a, b)) for a, b in block.bonds()}
        coords = np.array([block.coord(i) for i in range(block.numAtoms())])
        dim = np.array(cell.dim, dtype=float)
        imagePairs = 0
        for i in range(len(coords)):
            for j in range(i + 1, len(coords)):
                raw = coords[j] - coords[i]
                shift = np.round(raw / dim)
                if shift.any() and np.linalg.norm(raw - shift * dim) <= block.radius(i) + block.radius(j) + cell.atomMargin:
                    imagePairs += (i, j) not in bonded
        self.assertEqual(clashes, imagePairs)


CARBON = os.path.join(os.path.dirname(os.path.abspath(ab_cell.__file__)), "recipes", "params", "gaff_carbon")
TRIPLE, JOIN = 1.203, 1.3693  # acetylene_cg's C#C, and GAFF's cg-cg / ch-ch single bond


def polyyneRing(units=4):
    """A polyyne of acetylene_cg units along x in a cell exactly long enough for its ends to
    bond across the boundary: a chain that wraps onto its own periodic image"""
    length = units * (TRIPLE + JOIN)
    cell = ab_cell.Cell([length, 14.0, 14.0], paramsDir=CARBON, typedBondLengths=True)
    cell.libraryAddFragment(filename=os.path.join(BLOCKS_DIR, "acetylene_cg.car"), fragmentType="A")
    cell.addBondType("A:g-A:g")
    cell.addBondType("A:h-A:h")
    block = cell.getLibraryBlock(fragmentType="A")
    block.translateCentroid(np.array([length / 2.0, 7.0, 7.0]))
    cell.addBlock(block)
    return cell, length


class WrappedBlocks(unittest.TestCase):
    """A block zipped to its own periodic image: the export must give that bond's real,
    nearest-image vector, and joinBlocks must not move the block"""

    def testAChainThatWrapsIsExportedAsOne(self):
        cell, length = polyyneRing()
        (block,) = cell.blocks.values()
        self.assertFalse(cell._wrapsItself(block))
        grown = cell.growBlocks(3, maxTries=20, random=False)
        self.assertEqual(grown, 3)
        cell.zipBlocks(bondMargin=0.3, bondAngleMargin=10)
        (block,) = cell.blocks.values()
        self.assertEqual(block.numFreeEndGroups(), 0)  # the ends bonded to each other through the boundary
        self.assertTrue(cell._wrapsItself(block))
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        structure = cell.writeStructure(os.path.join(tmp, "ring.xyz"))
        cell.writeTopology(os.path.join(tmp, "ring.topology.json"), structure)
        with open(os.path.join(tmp, "ring.topology.json")) as f:
            topo = json.load(f)
        with open(structure) as f:
            lines = f.read().splitlines()[2:]
        pos = np.array([[float(x) for x in line.split()[1:4]] for line in lines])
        species = [line.split()[0] for line in lines]
        dim = np.array([length, 14.0, 14.0])
        carbon = [(i, j, image) for i, j, image in topo["bonds"] if species[i] == species[j] == "C"]
        self.assertEqual(len(carbon), 8)  # four triple bonds and four joins, a closed loop
        for i, j, image in carbon:
            d = np.linalg.norm(pos[j] + np.array(image) * dim - pos[i])
            self.assertTrue(abs(d - TRIPLE) < 0.01 or abs(d - JOIN) < 0.01, (i, j, image, d))
        # around the loop the images add up to one cell along x: it wraps
        self.assertEqual(winding(topo["bonds"], topo["atoms"]), {0})

    def testJoinBlocksNeverMovesAWrappedBlock(self):
        cell, length = polyyneRing()
        cell.growBlocks(3, maxTries=20, random=False)
        cell.zipBlocks(bondMargin=0.3, bondAngleMargin=10)
        (ring,) = cell.blocks.values()
        loose = cell.getLibraryBlock(fragmentType="A")
        loose.translateCentroid(np.array([2.0, 2.0, length / 2.0]))
        cell.addBlock(loose)
        ringEnd, looseEnd = ring.fragments[0].endGroups()[0], loose.fragments[0].endGroups()[0]
        moved = []

        def attach(move, static, dihedral=None):
            moved.append(move.block())
            cell.addBlock(move.block())
            return True

        # offered the ring as the block to move, joinBlocks moves the other one instead
        with mock.patch.object(cell, "cellEndGroupPair", return_value=(ringEnd, looseEnd)), \
                mock.patch.object(cell, "attachBlock", side_effect=attach):
            self.assertEqual(cell._joinBlocks(1), 1)
        self.assertEqual(moved, [loose])
        # offered only wrapped blocks, it moves neither and gives up
        moved.clear()
        with mock.patch.object(cell, "cellEndGroupPair", return_value=(ringEnd, ringEnd)), \
                mock.patch.object(cell, "attachBlock", side_effect=attach):
            self.assertEqual(cell._joinBlocks(1, maxTries=3), 0)
        self.assertEqual(moved, [])


def winding(bonds, atoms):
    """The axes along which the bonded clusters reach their own periodic images"""
    parent, offset = list(range(atoms)), [np.zeros(3, dtype=int) for _ in range(atoms)]

    def find(x):
        chain = []
        while parent[x] != x:
            chain.append(x)
            x = parent[x]
        acc = np.zeros(3, dtype=int)
        for node in reversed(chain):
            acc = acc + offset[node]
            offset[node], parent[node] = acc.copy(), x
        return x

    axes = set()
    for i, j, image in bonds:
        ri, rj = find(i), find(j)
        rel = offset[i] + np.array(image) - offset[j]
        if ri == rj:
            axes |= {k for k in range(3) if rel[k]}
        else:
            parent[rj], offset[rj] = ri, rel
    return axes


if __name__ == "__main__":
    unittest.main()
