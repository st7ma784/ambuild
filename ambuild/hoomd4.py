"""MD engine for HOOMD-blue 4.0 and later (tested with 7.2).

Ports hoomd2.Hoomd2 to the hoomd.Simulation API with the same force field:
Lennard-Jones pairs, harmonic bonds and angles, periodic dihedrals (HOOMD 2's
harmonic dihedral with phi0 = 0) and Lennard-Jones walls, with rigid bodies through
md.constrain.Rigid. As in hoomd2, charges are copied to the particles but no
electrostatic force is applied.
"""
import logging

import hoomd
import hoomd.md as md
import numpy as np

from ambuild import xyz_core
from ambuild.ab_mdengine import MdEngineBase

logger = logging.getLogger(__name__)

NLIST_BUFFER = 0.4
LOG_PERIOD = 100
FIRE_CHECK_PERIOD = 100  # steps between convergence checks during optimisation


class Hoomd4(MdEngineBase):
    def __init__(self, paramsDir, outputDir=None):
        super(Hoomd4, self).__init__(paramsDir, outputDir=outputDir)
        self.device = None
        self.sim = None
        self.rigid = None
        self.forces = []
        self.nRigidParticles = 0
        self.thermo = None

    # ------------------------------------------------------------------ setup

    def setupDevice(self, quiet=False):
        self.device = hoomd.device.auto_select(notice_level=0 if quiet else 2)
        return

    def createSnapshot(self, data, doCharges=True, doDihedral=True):
        """Return a hoomd.Snapshot of data; like HOOMD itself, only rank 0 holds its arrays"""
        self.nRigidParticles = self.setTypes(data, doDihedral=doDihedral)
        snapshot = hoomd.Snapshot(self.device.communicator)
        if snapshot.communicator.rank != 0:
            return snapshot
        box = np.array(data.cell, dtype=float)
        snapshot.configuration.box = [box[0], box[1], box[2], 0, 0, 0]
        p = self.particleData(data, box, doCharges)
        particles = snapshot.particles
        particles.N = len(p["typeid"])
        particles.types = self.particleTypes
        for name in ("typeid", "position", "image", "mass", "charge", "diameter", "body"):
            getattr(particles, name)[:] = p[name]
        if self.rigidBody:
            particles.orientation[:] = p["orientation"]
            particles.moment_inertia[:] = p["moment_inertia"]
        offset = self.nRigidParticles
        self._setGroups(snapshot.bonds, self.bond_types, data.bonds, data.bondLabels, offset)
        self._setGroups(snapshot.angles, self.angle_types, data.angles, data.angleLabels, offset)
        self._setGroups(
            snapshot.dihedrals, self.dihedral_types, data.propers, data.properLabels, offset
        )
        return snapshot

    @staticmethod
    def _setGroups(section, types, groups, labels, offset):
        section.types = list(types)
        if not types:
            return
        section.N = len(groups)
        section.group[:] = np.array(groups, dtype=int) + offset
        section.typeid[:] = [types.index(label) for label in labels]

    def particleData(self, data, box, doCharges):
        """Per-particle arrays in tag order: rigid centres first, then their constituents"""
        typeid, position, image, mass, charge, diameter, body = [], [], [], [], [], [], []
        orientation, moment = [], []
        if self.rigidBody:
            for i, rp in enumerate(data.rigidParticles):
                pos, img = xyz_core.wrapCoord3(rp.position, dim=box, center=True)
                typeid.append(self.particleTypes.index(rp.type))
                position.append(pos)
                image.append(img)
                mass.append(rp.mass)
                charge.append(0.0)
                diameter.append(1.0)
                body.append(i)
                orientation.append(rp.orientation)
                moment.append(rp.principalMoments)
            for i, rp in enumerate(data.rigidParticles):
                for j in range(rp.natoms):
                    # b_positions are relative to the centre of mass; give HOOMD absolute,
                    # wrapped positions (Rigid then keeps them consistent with the body)
                    pos, img = xyz_core.wrapCoord3(
                        np.asarray(rp.position) + np.asarray(rp.b_positions[j]), dim=box, center=True
                    )
                    typeid.append(self.particleTypes.index(rp.b_atomTypes[j]))
                    position.append(pos)
                    image.append(img)
                    mass.append(rp.b_masses[j])
                    charge.append(rp.b_charges[j] if doCharges else 0.0)
                    diameter.append(rp.b_diameters[j])
                    body.append(i)
                    orientation.append((1.0, 0.0, 0.0, 0.0))
                    moment.append((0.0, 0.0, 0.0))
        else:
            for i in range(len(data.coords)):
                typeid.append(self.particleTypes.index(data.atomTypes[i]))
                position.append(data.coords[i])
                image.append(data.images[i])
                mass.append(data.masses[i])
                charge.append(data.charges[i] if doCharges else 0.0)
                diameter.append(data.diameters[i])
                body.append(-1)
        return {
            "typeid": typeid,
            "position": np.array(position, dtype=float),
            "image": np.array(image, dtype=int),
            "mass": mass,
            "charge": charge,
            "diameter": diameter,
            "body": body,
            "orientation": np.array(orientation, dtype=float),
            "moment_inertia": np.array(moment, dtype=float),
        }

    def setupSimulation(self, snapshot, data, walls=None, wallAtomType=None):
        self.sim = hoomd.Simulation(device=self.device, seed=1)
        self.sim.create_state_from_snapshot(snapshot)
        self.checkParameters()
        self.rigid = self.setupRigidBody(data)
        self.forces = [f for f in (self.bondForce(), self.angleForce(), self.dihedralForce()) if f]
        self.lj = self.pairForce()
        self.forces.append(self.lj)
        wallForce = self.wallForce(walls, wallAtomType)
        if wallForce is not None:
            self.forces.append(wallForce)
        self.activeFilter = self.setupFilters(data)
        self.thermo = md.compute.ThermodynamicQuantities(filter=hoomd.filter.All())
        self.sim.operations.computes.append(self.thermo)
        return

    def setupRigidBody(self, data):
        if not self.rigidBody:
            return None
        rigid = md.constrain.Rigid()
        for rtype, positions, types in data.rigidParticleMgr.referenceParticles:
            rigid.body[rtype] = {
                "constituent_types": list(types),
                "positions": [tuple(p) for p in positions],
                "orientations": [(1.0, 0.0, 0.0, 0.0)] * len(types),
            }
        return rigid

    def bondForce(self):
        if not self.bond_types:
            return None
        force = md.bond.Harmonic()
        for bond in self.bond_types:
            param = self.ffield.bondParameter(bond)
            force.params[bond] = dict(k=param["k"], r0=param["r0"])
        return force

    def angleForce(self):
        if not self.angle_types:
            return None
        force = md.angle.Harmonic()
        for angle in self.angle_types:
            param = self.ffield.angleParameter(angle)
            force.params[angle] = dict(k=param["k"], t0=param["t0"])
        return force

    def dihedralForce(self):
        if not self.dihedral_types:
            return None
        # HOOMD 2's harmonic dihedral, V = k/2 (1 + d cos(n phi)), is Periodic with phi0 = 0
        force = md.dihedral.Periodic()
        for dihedral in self.dihedral_types:
            param = self.ffield.dihedralParameter(dihedral)
            force.params[dihedral] = dict(k=param["k"], d=param["d"], n=param["n"], phi0=0.0)
        return force

    def pairForce(self):
        nl = md.nlist.Cell(
            buffer=NLIST_BUFFER, exclusions=("bond", "1-3", "1-4", "angle", "dihedral", "body")
        )
        lj = md.pair.LJ(nlist=nl, default_r_cut=self.rCut)
        for atype, btype in self._activePairs():
            param = self.ffield.pairParameter(atype, btype)
            lj.params[(atype, btype)] = dict(epsilon=param["epsilon"], sigma=param["sigma"])
        # Rigid-body centres take no part in pair interactions
        for ptype in self.exclusions:
            for other in self.particleTypes:
                lj.params[(ptype, other)] = dict(epsilon=0.0, sigma=0.0)
                lj.r_cut[(ptype, other)] = 0.0
        return lj

    def _activePairs(self):
        active = self.activeParticleTypes()
        return [(a, b) for i, a in enumerate(active) for b in active[i:]]

    def wallForce(self, walls, wallAtomType):
        """Two Lennard-Jones planes facing each other for each wall requested"""
        if walls is None or not any(walls):
            return None
        assert len(walls) == 3  # array of three booleans - one per wall
        assert wallAtomType is not None, "Need to set a wallAtomType!"
        box = self.sim.state.box
        planes = []
        for do, wtype in zip(walls, ["XOY", "XOZ", "YOZ"]):
            if not do:
                continue
            logger.info("Setting wall in HOOMD-blue for %s of atomType %s", wtype, wallAtomType)
            if wtype == "XOY":
                half, normal = (0, 0, box.Lz / 2), (0, 0, 1)
            elif wtype == "XOZ":
                half, normal = (0, box.Ly / 2, 0), (0, 1, 0)
            else:
                half, normal = (box.Lx / 2, 0, 0), (1, 0, 0)
            # Front plane facing in; back plane facing back towards it (HOOMD 2's inside=False)
            planes.append(hoomd.wall.Plane(origin=tuple(-h for h in half), normal=normal))
            planes.append(hoomd.wall.Plane(origin=half, normal=tuple(-n for n in normal)))
        force = md.external.wall.LJ(walls=planes)
        for ptype in self.activeParticleTypes():
            param = self.ffield.pairParameter(ptype, wallAtomType)
            force.params[ptype] = dict(
                epsilon=param["epsilon"], sigma=param["sigma"], r_cut=self.rCut, r_extrap=0.0
            )
        for ptype in self.exclusions:
            force.params[ptype] = dict(epsilon=0.0, sigma=0.0, r_cut=0.0, r_extrap=0.0)
        return force

    def setupFilters(self, data):
        """The particles that move: rigid-body centres or all atoms, less static fragments"""
        base = hoomd.filter.Rigid(("center",)) if self.rigidBody else hoomd.filter.All()
        static = [i for i, s in enumerate(data.static) if s]
        if static:
            # As hoomd2: remove the static fragments' atoms from the moving group
            return hoomd.filter.SetDifference(base, hoomd.filter.Tags(static))
        return base

    # ------------------------------------------------------------ calculations

    def _prepare(self, data, rigidBody, doDihedral, doImproper, doCharges, rCut, quiet, walls,
                 wallAtomType):
        if rCut is not None:
            self.rCut = rCut
        if doDihedral and doImproper:
            raise RuntimeError("Cannot have impropers and dihedrals at the same time")
        self.rigidBody = rigidBody
        self.setupDevice(quiet=quiet)
        snapshot = self.createSnapshot(data, doCharges=doCharges, doDihedral=doDihedral)
        self.setupSimulation(snapshot, data, walls=walls, wallAtomType=wallAtomType)
        return

    def _table(self, filename):
        """A hoomd.write.Table logging energies every LOG_PERIOD steps; returns (writer, file)"""
        logger_ = hoomd.logging.Logger(categories=["scalar"])
        logger_.add(self.thermo, quantities=["potential_energy", "kinetic_energy"])
        logger_.add(self.lj, quantities=["energy"])  # the pair LJ energy
        output = open(filename, "w")
        # Energies exist only once a step has run, so log at steps 99, 199, ... not at 0
        table = hoomd.write.Table(
            trigger=hoomd.trigger.Periodic(LOG_PERIOD, phase=LOG_PERIOD - 1),
            logger=logger_,
            output=output,
        )
        self.sim.operations.writers.append(table)
        return table, output

    def _gsd(self, filename, period):
        writer = hoomd.write.GSD(
            trigger=hoomd.trigger.Periodic(period), filename=filename, mode="wb"
        )
        self.sim.operations.writers.append(writer)
        return writer

    def optimiseGeometry(self, data, rigidBody=True, doDihedral=False, doImproper=False,
                         doCharges=True, rCut=None, quiet=None, walls=None, wallAtomType=None,
                         **kw):
        self._prepare(data, rigidBody, doDihedral, doImproper, doCharges, rCut, quiet, walls,
                      wallAtomType)
        table, output = self._table(self.outputPath("geomopt.tsv"))
        try:
            if kw.get("stepwise"):
                optimised = self._optimiseGeometryStepwise(**kw)
            else:
                optimised = self._optimiseGeometry(**kw)
            if kw.get("d") is not None:
                kw["d"]["potential_energy"] = self.thermo.potential_energy
        finally:
            self.sim.operations.writers.remove(table)
            output.close()
        # As with hoomd2, report success whether or not FIRE converged
        logger.info("Optimisation %s", "converged" if optimised else "did not converge")
        return True

    def _fire(self, dt, Nmin, alpha_start, ftol, Etol, finc, fdec):
        methods = [md.methods.ConstantVolume(filter=self.activeFilter)]
        return md.minimize.FIRE(
            dt=dt, force_tol=ftol, angmom_tol=ftol, energy_tol=Etol,
            integrate_rotational_dof=self.rigidBody, forces=self.forces, methods=methods,
            rigid=self.rigid, min_steps_adapt=Nmin, finc_dt=finc, fdec_dt=fdec,
            alpha_start=alpha_start,
        )

    def _runFire(self, fire, steps):
        """Run up to steps FIRE steps, stopping once converged; return whether it converged"""
        done = 0
        while done < steps and not fire.converged:
            n = min(FIRE_CHECK_PERIOD, steps - done)
            self.sim.run(n)
            done += n
        return fire.converged

    def _optimiseGeometry(self, optCycles=1000000, dump=False, dumpPeriod=1, dt=0.005, Nmin=5,
                          alpha_start=0.1, ftol=1e-2, Etol=1e-5, finc=1.1, fdec=0.5,
                          max_tries=3, retries_on_error=3, **kw):
        assert max_tries > 0
        attempts = max(retries_on_error, 0) + 1
        writer = self._gsd(self.outputPath("opt.gsd"), dumpPeriod) if dump else None
        for attempt in range(attempts):
            # Each attempt that fails lowers the timestep by 10
            fire = self._fire(dt, Nmin, alpha_start, ftol, Etol, finc, fdec)
            self.sim.operations.integrator = fire
            try:
                optimised = False
                for j in range(max_tries):
                    logger.info("Running %d optimisation cycles in macrocycle %d", optCycles, j)
                    if self._runFire(fire, optCycles):
                        logger.info("Optimisation converged on macrocycle %d", j)
                        optimised = True
                        break
                    logger.info("Optimisation failed to converge on macrocycle %d", j)
                break
            except RuntimeError as e:
                logger.info("Optimisation step %d failed!\n%s", attempt, e)
                if attempt + 1 >= attempts:
                    raise
                logger.info("Rerunning optimisation changing dt %g -> %g", dt, dt * 0.1)
                dt *= 0.1
                self.sim.operations.integrator = None
        if writer is not None:
            writer.flush()
            self.sim.operations.writers.remove(writer)
        return optimised

    def _optimiseGeometryStepwise(self, dump=False, dumpPeriod=1, Nmin=5, alpha_start=0.1,
                                  ftol=1e-2, Etol=1e-5, finc=1.1, fdec=0.5, **kw):
        dt, optCycles, multiplier = 1e-12, 10, 10
        writer = self._gsd(self.outputPath("opt.gsd"), dumpPeriod) if dump else None
        optimised = False
        for i in range(8):
            logger.info("Running step optimisation %d with %d cycles at dt %g", i, optCycles, dt)
            fire = self._fire(dt, Nmin, alpha_start, ftol, Etol, finc, fdec)
            self.sim.operations.integrator = fire
            if self._runFire(fire, optCycles):
                logger.info("Optimisation converged on step %d", i)
                optimised = True
                break
            dt *= multiplier
            optCycles *= multiplier
        if writer is not None:
            writer.flush()
            self.sim.operations.writers.remove(writer)
        return optimised

    def runMD(self, data, doDihedral=False, doImproper=False, doCharges=True, rigidBody=False,
              rCut=None, quiet=None, walls=None, wallAtomType=None, **kw):
        self._prepare(data, rigidBody, doDihedral, doImproper, doCharges, rCut, quiet, walls,
                      wallAtomType)
        table, output = self._table(self.outputPath("runmd.log"))
        try:
            self._runMD(**kw)
            if kw.get("d") is not None:
                kw["d"]["potential_energy"] = self.thermo.potential_energy
        finally:
            self.sim.operations.writers.remove(table)
            output.close()
        return True

    def _runMD(self, mdCycles=100000, integrator="nvt", T=1.0, tau=0.5, P=1, tauP=0.5,
               dt=0.0005, dump=False, dumpPeriod=100, **kw):
        thermostat = md.methods.thermostats.MTTK(kT=T, tau=tau)
        if integrator == "nvt":
            method = md.methods.ConstantVolume(filter=self.activeFilter, thermostat=thermostat)
        elif integrator == "npt":
            method = md.methods.ConstantPressure(
                filter=self.activeFilter, S=P, tauS=tauP, couple="xyz", thermostat=thermostat
            )
        else:
            raise RuntimeError("Unrecognised integrator: {0}".format(integrator))
        self.sim.operations.integrator = md.Integrator(
            dt=dt, integrate_rotational_dof=self.rigidBody, forces=self.forces,
            methods=[method], rigid=self.rigid,
        )
        writer = self._gsd(self.outputPath("runmd.gsd"), dumpPeriod) if dump else None
        self.sim.run(mdCycles)
        if writer is not None:
            writer.flush()
            self.sim.operations.writers.remove(writer)
        return

    # ----------------------------------------------------------------- results

    def snapshotResult(self):
        """Box and particle data for ab_hoomdlauncher.applyResult; None on non-root ranks.

        Every MPI rank must call this, as taking a snapshot is collective.
        """
        snapshot = self.sim.state.get_snapshot()
        if self.sim.device.communicator.rank != 0:
            return None
        return {
            "box": list(snapshot.configuration.box[:3]),
            "positions": np.array(snapshot.particles.position),
            "images": np.array(snapshot.particles.image),
            "offset": self.nRigidParticles if self.rigidBody else 0,
        }

    def numRanks(self):
        return self.sim.device.communicator.num_ranks
