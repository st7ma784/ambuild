"""The interface between Cell and its molecular-dynamics engine, and engine selection.

An MD engine is created for each calculation with (paramsDir, outputDir=None) and
provides:

    rCut                      the pair cut-off; Cell.setRcut may change it
    optimiseGeometry(data, **kw) -> bool
    runMD(data, **kw) -> bool
    snapshotResult() -> dict  box, positions, images and offset, as used by
                              ab_hoomdlauncher.applyResult; None on non-root MPI ranks
    updateCell(cell)          copy the result back into the cell
    numRanks() -> int         the number of MPI ranks the calculation ran on

data is the CellData from Cell.cellData(); keyword arguments are those of
Cell.optimiseGeometry and Cell.runMD. The engine is hoomd4.Hoomd4, for HOOMD-blue
4.0 and later. HOOMD-blue 1 and 2 are no longer supported.
"""
import itertools
import os

from ambuild.ab_ffield import FfieldParameters


def engineClass(hoomdVersion):
    """Return the MD engine class for a HOOMD-blue version given as [major, minor, patch]"""
    if hoomdVersion is None:
        return None
    if hoomdVersion[0] >= 4:
        from ambuild.hoomd4 import Hoomd4

        return Hoomd4
    raise RuntimeError(
        "HOOMD-blue {0} is not supported: install HOOMD-blue 4.0 or later from conda-forge "
        "(see README)".format(".".join(map(str, hoomdVersion)))
    )


class MdEngineBase(object):
    """The parts of an engine that do not depend on the HOOMD-blue version"""

    def __init__(self, paramsDir, outputDir=None):
        self.ffield = FfieldParameters(paramsDir)
        self.outputDir = outputDir
        self.debug = False
        self.rCut = 5.0
        self.rigidBody = False
        self.exclusions = set()  # particle types ignored in pair interactions (rigid centres)
        self.particleTypes = []
        self.bond_types = []
        self.angle_types = []
        self.dihedral_types = []

    def outputPath(self, filename):
        if self.outputDir is None:
            return filename
        return os.path.join(self.outputDir, filename)

    def setTypes(self, data, doDihedral=True):
        """Set the particle, bond, angle and dihedral types for data.

        Returns the number of rigid-body centre particles (0 unless self.rigidBody).
        The lists are sorted so that every MPI rank orders the types alike.
        """
        self.exclusions = set()
        nRigidParticles = 0
        if self.rigidBody:
            # Combined size includes rigid centre and constituent particles
            nRigidParticles = len(data.rigidParticles)
            rigidCenters = set([r.type for r in data.rigidParticles])
            atomTypes = set()
            for r in data.rigidParticles:
                atomTypes.update(r.b_atomTypes)
            overlap = atomTypes.intersection(rigidCenters)
            if overlap:
                raise RuntimeError("Clashing atomTypes/rigidCenters: {0}".format(overlap))
            self.particleTypes = sorted(atomTypes.union(rigidCenters))
            self.exclusions = set(rigidCenters)
        else:
            self.particleTypes = sorted(set(data.atomTypes))
        self.bond_types = sorted(set(data.bondLabels)) if len(data.bonds) else []
        self.angle_types = sorted(set(data.angleLabels)) if len(data.angles) else []
        self.dihedral_types = (
            sorted(set(data.properLabels)) if len(data.propers) and doDihedral else []
        )
        return nRigidParticles

    def activeParticleTypes(self):
        """Particle types that take part in pair interactions"""
        return sorted(set(self.particleTypes).difference(self.exclusions))

    def checkParameters(self, skipDihedrals=False):
        assert self.ffield
        assert self.particleTypes
        missingBonds = [b for b in self.bond_types if not self.ffield.hasBond(b)]
        missingAngles = [a for a in self.angle_types if not self.ffield.hasAngle(a)]
        missingDihedrals = []
        if not skipDihedrals:
            missingDihedrals = [d for d in self.dihedral_types if not self.ffield.hasDihedral(d)]
        missingPairs = [
            (a, b)
            for a, b in itertools.combinations_with_replacement(self.activeParticleTypes(), 2)
            if not self.ffield.hasPair(a, b)
        ]
        if missingBonds or missingAngles or missingDihedrals or missingPairs:
            msg = "The following parameters could not be found:\n"
            if missingBonds:
                msg += "Bonds: {0}\n".format(missingBonds)
            if missingAngles:
                msg += "Angles: {0}\n".format(missingAngles)
            if missingDihedrals:
                msg += "Dihedrals: {0}\n".format(missingDihedrals)
            if missingPairs:
                msg += "Pairs: {0}\n".format(missingPairs)
            msg += "Please add these to the files in the directory: {0}\n".format(
                self.ffield.paramsDir
            )
            raise RuntimeError(msg)
        return

    def updateCell(self, cell):
        """Reset the cell's coordinates and box from the calculation's result"""
        from ambuild.ab_hoomdlauncher import applyResult

        applyResult(cell, self.snapshotResult())
        return
