"""A block larger than the cell can wrap round onto its own periodic image. closeAtoms
checks a moved block against the other blocks only, so before Cell._selfImageClashes such
overlaps went unnoticed: joinBlocks placed a 90 A cluster in a 60 A cell with atoms 0.5 A
from their own images, and the next optimisation blew up ("Particle ... is no longer in
the simulation box"; paf1_large, docs/carbon-families.md)."""
import os
import unittest

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


if __name__ == "__main__":
    unittest.main()
