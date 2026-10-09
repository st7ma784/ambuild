"""The xtb recipe stage: Ambuild's xTB worker run as an external program
(docs/xtb-spec.md). A stand-in (fake_xtb.py) writes the worker's results format, so the
stage's tests need neither tblite nor the xtb binary; the last classes run the real worker
where those are installed (tests/docker/xtb.Dockerfile)."""
import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from context import BLOCKS_DIR, PARAMS_DIR, ab_util
from ambuild import recipe as ab_recipe
from ambuild import xtb
from ambuild import xtb_worker
from testIonMap import events

FAKE = "{0} {1}".format(sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_xtb.py"))
WORKER = [sys.executable, "-m", "ambuild.xtb_worker"]
HAVE_TBLITE = importlib.util.find_spec("tblite") is not None and importlib.util.find_spec("ase") is not None

# A stand-in for Poreblazer: its log's figures, with a smaller surface and neck in a
# directory named for the relaxed structure
FAKE_POREBLAZER = """#!{python}
import os, sys
sys.stdin.read()
relaxed = os.getcwd().endswith("relaxed")
with open("poreblazer.log", "w") as f:
    f.write("Poreblazer_v3.0.5\\n")
    f.write("Accessible surface area per mass in m^2/g: {{0}}\\n".format(1380.0 if relaxed else 1500.0))
    f.write("Pore limiting diameter in A: {{0}}\\n".format(7.25 if relaxed else 7.5))
    f.write("Maximum pore diameter in A: 9.0\\n")
    f.write("cubelet " + open("defaults.dat").read().splitlines()[3] + "\\n")
"""


def recipe(**stage):
    return {
        "recipe_version": 1, "name": "xtb test",
        "cell": {"box": [20, 20, 20]},
        "fragments": [{"type": "A", "car": os.path.join(BLOCKS_DIR, "benzene2.car"),
                       "csv": os.path.join(BLOCKS_DIR, "benzene2.csv"), "name": "benzene2"}],
        "params": {name: os.path.join(PARAMS_DIR, name) for name in (
            "angle_params.csv", "bond_params.csv", "dihedral_params.csv", "improper_params.csv", "pair_params.csv")},
        "bond_types": ["A:a-A:a"],
        "stages": [{"op": "seed", "count": 3}, dict({"op": "xtb"}, **stage)],
        "seed": 4,
    }


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.rundir = os.path.join(self.tmp, "run")


class XtbStage(TempDir):
    def testTheFiguresAreRecorded(self):
        ab_recipe.run(recipe(threads=2), self.rundir, baseDir="/", xtbWorker=FAKE)
        (event,) = events(self.rundir, xtb.EVENT)
        data = event["data"]
        self.assertEqual((data["returncode"], data["converged"], data["method"]), (0, True, "GFN1-xTB"))
        self.assertEqual((data["atoms"], data["charge"], data["mode"]), (36, 0, "single_point"))
        self.assertEqual((data["fmax_eV_A"], data["frms_eV_A"], data["gap_eV"]), (1.5, 0.5, 2.0))
        self.assertEqual(data["energy_per_atom_eV"], -10.0)
        self.assertEqual(data["worst_atoms"][0], [3, 1.5])
        self.assertEqual((data["results"], data["log"]), ("xtb_1/xtb.json", "xtb_1/xtb.log"))
        self.assertIsNone(data["relax"])
        self.assertIsNotNone(event["step"])
        for name in ("structure.xyz", "structure.topology.json", "xtb.json", "xtb.log"):
            self.assertTrue(os.path.isfile(os.path.join(self.rundir, "xtb_1", name)), name)
        kinds = {e["data"]["kind"] for e in events(self.rundir, "artifact")}
        self.assertTrue({"xtb", "xtb_structure", "xtb_topology"} <= kinds)
        with open(os.path.join(self.rundir, "xtb_1", "xtb.json")) as f:
            written = json.load(f)
        # a single point is given no relaxation settings; the threads reach the worker
        self.assertEqual(written["flags"], ["--out", "--method", "--mode", "--charge", "--topology"])
        self.assertEqual(written["threads"], "2")
        metrics = xtb.metrics(data)
        self.assertEqual((metrics["xtb_fmax"], metrics["xtb_energy_per_atom"], metrics["xtb_relax_rmsd"]),
                         (1.5, -10.0, None))

    def testARelaxationIsRecorded(self):
        ab_recipe.run(recipe(mode="relax", method="gfn2", max_steps=20, fmax=0.1), self.rundir, baseDir="/",
                      xtbWorker=FAKE)
        (event,) = events(self.rundir, xtb.EVENT)
        data = event["data"]
        self.assertEqual((data["method"], data["mode"], data["relaxed"]), ("GFN2-xTB", "relax", "xtb_1/relaxed.xyz"))
        self.assertEqual((data["relax"]["steps"], data["relax"]["fmax_threshold_eV_A"]), (20, 0.1))
        self.assertIn("xtb_relaxed", {e["data"]["kind"] for e in events(self.rundir, "artifact")})
        metrics = xtb.metrics(data)
        self.assertEqual((metrics["xtb_relax_rmsd"], metrics["xtb_relax_max_bond_change"]), (0.125, 0.0625))
        self.assertAlmostEqual(metrics["xtb_relax_energy_drop"], 0.25)
        self.assertEqual(metrics["xtb_relax_reached_fmax"], 0)

    def testAFailedWorkerStopsTheRecipe(self):
        with self.assertRaisesRegex(RuntimeError, "xTB worker failed .exit code 5."):
            ab_recipe.run(recipe(mode="relax", max_steps=99), self.rundir, baseDir="/", xtbWorker=FAKE)
        (event,) = events(self.rundir, xtb.EVENT)
        self.assertEqual(event["data"]["returncode"], 5)
        with open(os.path.join(self.rundir, "run.json")) as f:
            self.assertEqual(json.load(f)["status"], "failed")

    def testACalculationThatDoesNotConvergeIsRecordedAndTheRecipeCarriesOn(self):
        r = recipe(mode="relax", max_steps=97)
        r["stages"].append({"op": "seed", "count": 1})
        ab_recipe.run(r, self.rundir, baseDir="/", xtbWorker=FAKE)
        (event,) = events(self.rundir, xtb.EVENT)
        self.assertEqual((event["data"]["converged"], event["data"]["error"]), (False, "SCF not converged"))
        self.assertEqual(xtb.metrics(event["data"]), {m: None for m in xtb.METRICS})
        with open(os.path.join(self.rundir, "run.json")) as f:
            self.assertEqual(json.load(f)["status"], "finished")

    def testAnotherFormatVersionIsRefused(self):
        with self.assertRaisesRegex(RuntimeError, "not ambuild-xtb version 1"):
            ab_recipe.run(recipe(mode="relax", max_steps=98), self.rundir, baseDir="/", xtbWorker=FAKE)

    def testWithoutAWorkerTheRecipeDoesNotStart(self):
        with mock.patch.dict(os.environ, {"PATH": "", "XTB_WORKER": "", "XTB_EXE": ""}), \
                mock.patch.object(xtb.importlib.util, "find_spec", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "set XTB_WORKER.*install tblite"):
                ab_recipe.run(recipe(), self.rundir, baseDir="/")
            with self.assertRaisesRegex(RuntimeError, "set XTB_WORKER.*xtb binary"):
                ab_recipe.run(recipe(method="gfnff"), self.rundir, baseDir="/")
        self.assertFalse(os.path.exists(self.rundir))

    def testGfnffRelaxesToo(self):
        ab_recipe.run(recipe(method="gfnff", mode="relax"), self.rundir, baseDir="/", xtbWorker=FAKE)
        (event,) = events(self.rundir, xtb.EVENT)
        self.assertEqual((event["data"]["method"], event["data"]["relaxed"]), ("GFN-FF", "xtb_1/relaxed.xyz"))

    @unittest.skipIf(os.name == "nt", "the stand-in for Poreblazer is a script run by its first line")
    def testPoreblazerOnTheRelaxedCell(self):
        """Poreblazer runs on the built and the relaxed structure with the settings of the
        recipe's last poreblazer stage, and the change is recorded"""
        exe = os.path.join(self.tmp, "poreblazer.exe")
        with open(exe, "w") as f:
            f.write(FAKE_POREBLAZER.format(python=sys.executable))
        os.chmod(exe, 0o755)
        r = recipe(mode="relax", poreblazer=True, threads=1)
        r["stages"].insert(1, {"op": "poreblazer", "settings": {"cubelet_size": 0.4}})
        ab_recipe.run(r, self.rundir, baseDir="/", xtbWorker=FAKE, poreblazerExe=exe)
        (event,) = events(self.rundir, xtb.EVENT)
        pores = event["data"]["poreblazer"]
        self.assertEqual((pores["settings"]["cubelet_size"], pores["settings"]["visualisation"]), (0.4, "none"))
        self.assertEqual((pores["built"]["surface_area_m2_g"], pores["relaxed"]["surface_area_m2_g"]), (1500.0, 1380.0))
        self.assertEqual((pores["built"]["returncode"], pores["relaxed"]["directory"]), (0, "xtb_2/poreblazer_relaxed"))
        self.assertEqual((pores["d_surface_area_m2_g"], pores["d_pore_limiting_diameter_A"]), (-120.0, -0.25))
        metrics = xtb.metrics(event["data"])
        self.assertEqual((metrics["xtb_d_surface_area"], metrics["xtb_d_pld"]), (-120.0, -0.25))
        # both structures are given to Poreblazer whole: the same atoms, inside the cell
        for name in ("built", "relaxed"):
            with open(os.path.join(self.rundir, "xtb_2", "poreblazer_" + name, "ambuild.xyz")) as f:
                lines = f.read().splitlines()
            self.assertEqual((lines[0], len(lines)), ("36", 38))
            self.assertTrue(all(0.0 <= float(x) < 20.0 for line in lines[2:] for x in line.split()[1:]))
            with open(os.path.join(self.rundir, "xtb_2", "poreblazer_" + name, "poreblazer.log")) as f:
                self.assertIn("cubelet 0.4", f.read())
        # they are the check's, not Poreblazer results of the run
        self.assertEqual(len(events(self.rundir, "pore_result")), 1)

    def testThePoreblazerComparisonNeedsARelaxationAndPoreblazer(self):
        with self.assertRaisesRegex(ab_recipe.RecipeError, "needs\\s+mode relax"):
            ab_recipe.run(recipe(poreblazer=True), self.rundir, baseDir="/", xtbWorker=FAKE, poreblazerExe="pb")
        with mock.patch.dict(os.environ, {"POREBLAZER_EXE": ""}):
            with self.assertRaisesRegex(RuntimeError, "xtb stage runs Poreblazer: set POREBLAZER_EXE"):
                ab_recipe.run(recipe(mode="relax", poreblazer=True), self.rundir, baseDir="/", xtbWorker=FAKE)
        self.assertFalse(os.path.exists(self.rundir))

    def testACellAboveMaxAtomsIsRefusedBeforeTheWorkerStarts(self):
        with self.assertRaisesRegex(RuntimeError, "36 atoms, more than the 35"):
            ab_recipe.run(recipe(max_atoms=35), self.rundir, baseDir="/", xtbWorker=FAKE)
        self.assertEqual(events(self.rundir, xtb.EVENT), [])
        self.assertFalse(os.path.exists(os.path.join(self.rundir, "xtb_1")))

    def testACellAboveTheMemoryLimitIsRefusedBeforeTheWorkerStarts(self):
        with self.assertRaisesRegex(RuntimeError, "about 155 MB for this cell of 36 atoms, more than the 100 MB"):
            ab_recipe.run(recipe(memory_limit_mb=100), self.rundir, baseDir="/", xtbWorker=FAKE)
        self.assertFalse(os.path.exists(os.path.join(self.rundir, "xtb_1")))

    def testAChargedCellNeedsItsChargeGiven(self):
        with mock.patch.object(xtb, "netCharge", return_value=1):
            with self.assertRaisesRegex(RuntimeError, r"net charge of \+1 e"):
                ab_recipe.run(recipe(), self.rundir, baseDir="/", xtbWorker=FAKE)
        self.assertFalse(os.path.exists(os.path.join(self.rundir, "xtb_1")))
        rundir = os.path.join(self.tmp, "charged")
        ab_recipe.run(recipe(charge=1), rundir, baseDir="/", xtbWorker=FAKE)
        (event,) = events(rundir, xtb.EVENT)
        self.assertEqual(event["data"]["charge"], 1)

    def testTheStageIsValidated(self):
        self.assertEqual(ab_recipe.validate(recipe(method="gfn2", mode="relax", max_steps=10, fmax=0.1, threads=4,
                                                   max_atoms=500, charge=-1, poreblazer=True), allowPaths=True), [])
        for bad in ({"method": "pm6"}, {"mode": "md"}, {"max_steps": 0}, {"fmax": -1}, {"charge": 0.5},
                    {"poreblazer": "yes"}):
            self.assertTrue(ab_recipe.validate(recipe(**bad), allowPaths=True), bad)
        self.assertIn("xtb", ab_recipe.describe()["operations"])


class Settings(unittest.TestCase):
    def testNetCharge(self):
        self.assertEqual(xtb.netCharge([0.25, -0.25, 0.004]), 0)
        self.assertEqual(xtb.netCharge([1.0, 0.12, -0.12]), 1)
        with self.assertRaisesRegex(ValueError, "not a whole number"):
            xtb.netCharge([0.5, 0.1])

    def testTheWorkerCommand(self):
        with mock.patch.dict(os.environ, {"XTB_WORKER": "/opt/x/bin/python -m ambuild.xtb_worker"}):
            self.assertEqual(xtb.workerCommand("gfn1"), ["/opt/x/bin/python", "-m", "ambuild.xtb_worker"])
            self.assertEqual(xtb.workerCommand("gfn1", "other"), ["other"])
        with mock.patch.dict(os.environ, {"XTB_WORKER": "", "XTB_EXE": "/opt/xtb/bin/xtb", "PATH": ""}), \
                mock.patch.object(xtb.importlib.util, "find_spec", return_value=None):
            self.assertIsNone(xtb.workerCommand("gfn2"))
            self.assertEqual(xtb.workerCommand("gfnff"), WORKER)

    def testTheMemoryEstimateCoversWhatWasMeasured(self):
        """docs/xtb-spec.md, Scaling: the largest of the three methods at each size"""
        self.assertGreaterEqual(xtb.memoryEstimateMb(472), 579)
        self.assertGreaterEqual(xtb.memoryEstimateMb(944), 2062)
        self.assertLess(xtb.memoryEstimateMb(944), 1.1 * 2062)
        self.assertGreater(xtb.memoryEstimateMb(1888), 3376)  # both methods were killed there

    def testMetricNames(self):
        self.assertEqual(xtb.METRICS[:2], ["xtb_fmax", "xtb_frms"])
        self.assertEqual(xtb.METRICS[-2:], ["xtb_d_surface_area", "xtb_d_pld"])
        self.assertEqual(xtb.poreChanges({"built": {"surface_area_m2_g": 10.0}, "relaxed": {"surface_area_m2_g": 4.0}}),
                         {"d_surface_area": -6.0, "d_pld": None})
        self.assertEqual(xtb.metricLabel("xtb_gap"), "HOMO–LUMO gap (eV, xTB)")
        self.assertEqual(xtb.metrics(None), {m: None for m in xtb.METRICS})


# --- structures for the worker: benzene in a cubic cell, in the export's format

HEADER = ('Lattice="{0:.6f} 0.0 0.0 0.0 {0:.6f} 0.0 0.0 0.0 {0:.6f}" '
          'Properties=species:S:1:pos:R:3:type:S:1:charge:R:1:fragment:S:1:block:I:1 pbc="T T T" step=0 '
          'run_id="test" export_version=1')
CC, CH = 1.395, 1.085


def benzene(centre, pushOut=0.0):
    """(symbols, positions, bonds): carbons 0, 2, .. 10, each followed by its hydrogen;
    pushOut moves carbon 0 and its hydrogen outwards"""
    symbols, positions, bonds = [], [], []
    for k in range(6):
        angle = math.pi / 3 * k
        for symbol, radius in (("C", CC), ("H", CC + CH)):
            radius += pushOut if k == 0 else 0.0
            symbols.append(symbol)
            positions.append([centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle), centre[2]])
        bonds.append([2 * k, 2 * k + 1, [0, 0, 0]])
        bonds.append(sorted([2 * k, (2 * k + 2) % 12]) + [[0, 0, 0]])
    return symbols, positions, sorted(bonds)


def writeFiles(directory, symbols, positions, bonds, side):
    structure = os.path.join(directory, "structure.xyz")
    with open(structure, "w", newline="\n") as f:
        f.write("{0}\n{1}\n".format(len(symbols), HEADER.format(side)))
        for s, (x, y, z) in zip(symbols, positions):
            f.write("{0} {1:.6f} {2:.6f} {3:.6f} {4} 0.0000 A 0\n".format(s, x, y, z, "ca" if s == "C" else "ha"))
    topology = os.path.join(directory, "structure.topology.json")
    with open(topology, "w") as f:
        json.dump({"format": "ambuild-topology", "version": 1, "bonds": bonds}, f)
    return structure, topology


class StructureFiles(TempDir):
    def testTheStructureIsReadAndARelaxedOneKeepsItsFormat(self):
        symbols, positions, bonds = benzene((6.0, 6.0, 6.0))
        path, _ = writeFiles(self.tmp, symbols, positions, bonds, 12.0)
        structure = xtb.readStructure(path)
        self.assertEqual((structure["lattice"], structure["symbols"]), ([12.0, 12.0, 12.0], symbols))
        self.assertEqual(structure["charges"], [0.0] * 12)
        self.assertAlmostEqual(structure["positions"][1][0], 6.0 + CC + CH, places=6)
        moved = [[x + 7.0, y, z] for x, y, z in structure["positions"]]  # through a face: not wrapped back
        relaxed = xtb.readStructure(xtb.writeRelaxed(os.path.join(self.tmp, "relaxed.xyz"), structure, moved,
                                                     "GFN1-xTB"))
        self.assertEqual((relaxed["symbols"], relaxed["lattice"], relaxed["columns"]),
                         (symbols, structure["lattice"], structure["columns"]))
        self.assertEqual([row[4:] for row in relaxed["rows"]], [row[4:] for row in structure["rows"]])
        self.assertAlmostEqual(relaxed["positions"][1][0], 13.0 + CC + CH, places=6)
        self.assertTrue(relaxed["header"].startswith(structure["header"]))
        self.assertIn('relaxed_by="GFN1-xTB"', relaxed["header"])

    def testOnlyOrthorhombicCellsAreRead(self):
        path = os.path.join(self.tmp, "tilted.xyz")
        with open(path, "w") as f:
            f.write('1\nLattice="5 1 0 0 5 0 0 0 5" Properties=species:S:1:pos:R:3\nC 0 0 0\n')
        with self.assertRaisesRegex(ValueError, "orthorhombic"):
            xtb.readStructure(path)


class WorkerFigures(unittest.TestCase):
    def testForces(self):
        figures = xtb_worker.forceFigures([[0, 0, 0], [3, 4, 0], [0, 0, 1]])
        self.assertEqual(figures["fmax_eV_A"], 5.0)
        self.assertAlmostEqual(figures["frms_eV_A"], math.sqrt(26 / 3.0))
        self.assertEqual(figures["worst_atoms"], [[1, 5.0], [2, 1.0], [0, 0.0]])

    def testABondThroughAFaceHasItsTrueLength(self):
        """The topology's image gives the bond's short vector from wrapped positions"""
        structure = {"lattice": [10.0, 10.0, 10.0], "positions": [[9.5, 5.0, 5.0], [0.5, 5.0, 5.0], [2.0, 5.0, 5.0]]}
        bonds = [[0, 1, [1, 0, 0]], [1, 2, [0, 0, 0]]]
        self.assertAlmostEqual(xtb_worker.bondLength(structure["positions"], structure["lattice"], bonds[0]), 1.0)
        # atom 1 moves 0.25 Å towards atom 0, through nothing: one bond shortens, the other lengthens
        figures = xtb_worker.relaxFigures(structure, [[9.5, 5.0, 5.0], [0.25, 5.0, 5.0], [2.0, 5.0, 5.0]], bonds)
        self.assertAlmostEqual(figures["max_bond_change_A"], 0.25)
        self.assertEqual(figures["worst_bonds"], [[0, 1, 1.0, 0.75], [1, 2, 1.5, 1.75]])
        self.assertAlmostEqual(figures["max_displacement_A"], 0.25)
        self.assertAlmostEqual(figures["rmsd_A"], 0.25 / math.sqrt(3))
        self.assertIsNone(xtb_worker.relaxFigures(structure, structure["positions"], None)["max_bond_change_A"])

    def testTheEngradFileIsRead(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, "structure.engrad")
        with open(path, "w") as f:
            f.write("#\n# Number of atoms\n#\n   2\n#\n# The current total energy in Eh\n#\n  -1.5\n#\n"
                    "# The current gradient in Eh/bohr\n#\n 0.1\n 0.2\n 0.3\n -0.1\n -0.2\n -0.3\n#\n"
                    "# The atomic numbers and current coordinates in Bohr\n#\n 1 0.0 0.0 0.0\n 1 1.4 0.0 0.0\n")
        self.assertEqual(xtb_worker.readEngrad(path), (-1.5, [[0.1, 0.2, 0.3], [-0.1, -0.2, -0.3]]))

    def testACoordFileIsReadAndUnwrapped(self):
        """xtb writes its optimised geometry in bohr and may wrap atoms into the cell"""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, "xtbopt.coord")
        with open(path, "w") as f:
            f.write("$coord\n 1.0 2.0 3.0 c\n 18.0 0.0 0.0 h\n$periodic 3\n$lattice bohr\n 20 0 0\n 0 20 0\n 0 0 20\n$end\n")
        positions, lattice = xtb_worker.readCoord(path)
        self.assertEqual(len(positions), 2)
        self.assertAlmostEqual(lattice[1][1], 20.0 * xtb_worker.BOHR_A)
        self.assertAlmostEqual(positions[0][2], 3.0 * xtb_worker.BOHR_A)
        with open(path, "w") as f:
            f.write("$coord angs\n 9.9 5.0 5.0 c\n$end\n")
        self.assertEqual(xtb_worker.readCoord(path), ([[9.9, 5.0, 5.0]], None))
        # an atom that left through the low face and was wrapped to the high one
        moved = xtb_worker.unwrapped([[9.9, 5.0, 5.0]], [[0.1, 5.0, 5.0]], [10.0, 10.0, 10.0])
        self.assertAlmostEqual(moved[0][0], -0.1)
        self.assertEqual([xtb_worker.gfnffLevel(f) for f in (0.01, 0.05, 0.5)], ["tight", "normal", "crude"])

    def testElements(self):
        self.assertEqual(xtb_worker.atomicNumbers(["C", "H", "si", "LI"]), [6, 1, 14, 3])
        with self.assertRaisesRegex(ValueError, "No element"):
            xtb_worker.atomicNumbers(["Xx"])


@unittest.skipUnless(HAVE_TBLITE, "needs tblite and ASE (tests/docker/xtb.Dockerfile)")
class RealWorker(TempDir):
    def run_(self, pushOut, side=12.0, centre=(6.0, 6.0, 6.0), **kw):
        files = writeFiles(self.tmp, *benzene(centre, pushOut), side=side)
        out = os.path.join(self.tmp, "xtb.json")
        code = xtb.runWorker(WORKER, files[0], out, os.path.join(self.tmp, "xtb.log"), topology=files[1], threads=2,
                             **kw)
        self.assertEqual(code, 0, open(os.path.join(self.tmp, "xtb.log")).read()[-2000:])
        return xtb.readResults(out)

    def testAStretchedRingIsFoundAndRelaxed(self):
        data = self.run_(0.2, method="gfn1", mode="relax", maxSteps=80, fmax=0.05,
                         relaxed=os.path.join(self.tmp, "relaxed.xyz"))
        self.assertEqual((data["program"], data["method"], data["converged"]), ("tblite", "GFN1-xTB", True))
        self.assertEqual(data["worst_atoms"][0][0], 0)  # the carbon that was pushed out
        self.assertGreater(data["fmax_eV_A"], 1.0)
        self.assertGreater(data["gap_eV"], 2.0)
        relax = data["relax"]
        self.assertTrue(relax["reached_fmax"], relax)
        self.assertLess(relax["fmax_eV_A"], 0.05)
        self.assertLess(relax["energy_eV"], data["energy_eV"])
        self.assertIn(0, relax["worst_bonds"][0][:2])
        self.assertGreater(relax["max_bond_change_A"], 0.05)
        # the ring is regular again: every C-C bond the same length
        relaxed = xtb.readStructure(os.path.join(self.tmp, "relaxed.xyz"))
        cc = [xtb_worker.bondLength(relaxed["positions"], relaxed["lattice"], [2 * k, (2 * k + 2) % 12, [0, 0, 0]])
              for k in range(6)]
        self.assertLess(max(cc) - min(cc), 0.01)
        self.assertEqual(relaxed["symbols"], xtb.readStructure(os.path.join(self.tmp, "structure.xyz"))["symbols"])

    def testTheCellIsPeriodic(self):
        """A ring across the cell's corner gives the energy and forces of one in its middle"""
        middle = self.run_(0.1, method="gfn2")
        corner = self.run_(0.1, centre=(0.3, 11.8, 0.1), method="gfn2")
        self.assertAlmostEqual(middle["energy_eV"], corner["energy_eV"], places=4)
        self.assertAlmostEqual(middle["fmax_eV_A"], corner["fmax_eV_A"], places=3)
        self.assertEqual(middle["method"], "GFN2-xTB")
        self.assertIsNone(middle["relax"])


@unittest.skipUnless(xtb.xtbExecutable(), "needs the xtb binary (XTB_EXE, or xtb on the PATH)")
class RealGfnff(TempDir):
    def testASinglePoint(self):
        files = writeFiles(self.tmp, *benzene((6.0, 6.0, 6.0), 0.2), side=12.0)
        out = os.path.join(self.tmp, "xtb.json")
        code = xtb.runWorker(WORKER, files[0], out, os.path.join(self.tmp, "xtb.log"), "gfnff", threads=2)
        self.assertEqual(code, 0, open(os.path.join(self.tmp, "xtb.log")).read()[-2000:])
        data = xtb.readResults(out)
        self.assertEqual((data["program"], data["method"], data["converged"]), ("xtb", "GFN-FF", True))
        self.assertEqual(data["worst_atoms"][0][0], 0)
        self.assertGreater(data["fmax_eV_A"], 0.5)
        self.assertIsNone(data["gap_eV"])

    def testARelaxation(self):
        """xtb's own optimiser brings the stretched ring back, and the figures are measured
        on the geometry it wrote"""
        files = writeFiles(self.tmp, *benzene((0.4, 6.0, 6.0), 0.2), side=12.0)  # across a face
        out, relaxedFile = os.path.join(self.tmp, "xtb.json"), os.path.join(self.tmp, "relaxed.xyz")
        code = xtb.runWorker(WORKER, files[0], out, os.path.join(self.tmp, "xtb.log"), "gfnff", mode="relax",
                             maxSteps=200, fmax=0.05, topology=files[1], relaxed=relaxedFile, threads=2)
        self.assertEqual(code, 0, open(os.path.join(self.tmp, "xtb.log")).read()[-2000:])
        data = xtb.readResults(out)
        relax = data["relax"]
        self.assertEqual((data["method"], data["converged"], relax["structure"]), ("GFN-FF", True, "relaxed.xyz"))
        self.assertGreater(relax["steps"], 1)
        self.assertLess(relax["energy_eV"], data["energy_eV"])
        self.assertLess(relax["fmax_eV_A"], 0.2 * data["fmax_eV_A"])
        self.assertIn(0, relax["worst_bonds"][0][:2])
        self.assertLess(relax["max_displacement_A"], 1.0)  # no atom is reported a cell away
        relaxed = xtb.readStructure(relaxedFile)
        bonds = xtb_worker.readBonds(files[1])
        cc = [xtb_worker.bondLength(relaxed["positions"], relaxed["lattice"], b) for b in bonds
              if relaxed["symbols"][b[0]] == relaxed["symbols"][b[1]] == "C"]
        self.assertEqual(len(cc), 6)
        self.assertLess(max(cc) - min(cc), 0.02)
        self.assertEqual(relaxed["lattice"], [12.0, 12.0, 12.0])  # xtb would relax the cell too, left to itself


@unittest.skipUnless(HAVE_TBLITE and ab_util.HOOMDVERSION, "needs tblite and HOOMD-blue (tests/docker/xtb.Dockerfile)")
class RealBuild(TempDir):
    def testOptimisingABuildLowersItsLargestForce(self):
        """The check does what it is for: li_ion_carbon built without its optimisation
        stages is more strained, by GFN1-xTB, than the recipe as shipped"""
        from testLiIonCarbon import withoutOptimisation

        shipped = ab_recipe.example("li_ion_carbon", blocksDir=BLOCKS_DIR)
        shipped = dict(shipped, stages=shipped["stages"][:-1] + [{"op": "xtb", "method": "gfn1"}])
        fmax = {}
        for name, body in (("optimised", shipped), ("as placed", withoutOptimisation(shipped))):
            rundir = os.path.join(self.tmp, name.replace(" ", "_"))
            ab_recipe.run(body, rundir, baseDir="/")
            (event,) = events(rundir, xtb.EVENT)
            self.assertTrue(event["data"]["converged"], event["data"])
            fmax[name] = event["data"]["fmax_eV_A"]
        self.assertLess(fmax["optimised"], fmax["as placed"], fmax)


if __name__ == "__main__":
    unittest.main()
