"""Write the HOOMD engine parity fixture: saved cells and their HOOMD-blue 2 energies.

Run once under HOOMD-blue 2 from the tests directory:

    python test_data/hoomd_parity/make_reference.py

testHoomdParity then checks that newer HOOMD-blue engines give the same energies.
"""
import json
import os
import random
import sys

here = os.path.dirname(os.path.abspath(__file__))
tests = os.path.dirname(os.path.dirname(here))
params = os.path.join(tests, "params")

from ambuild import ab_cell, ab_util

# name -> (setup, calculation keyword arguments)
CASES = {
    "all_atom": ("plain", dict(rigidBody=False)),
    "all_atom_dihedrals": ("plain", dict(rigidBody=False, doDihedral=True)),
    "rigid": ("plain", dict(rigidBody=True)),
    "all_atom_walls": ("walls", dict(rigidBody=False)),
}


def build(setup, workdir):
    """Bonded benzene blocks, or (as testWallMd) methane blocks between walls on all sides"""
    random.seed(3)
    if setup == "walls":
        cell = ab_cell.Cell([20, 20, 20], paramsDir=params, outputDir=workdir)
        cell.libraryAddFragment(os.path.join(tests, "blocks", "ch4.car"), fragmentType="A")
        cell.setWall(XOY=True, XOZ=True, YOZ=True)
        cell.seed(15)
        return cell
    cell = ab_cell.Cell([30, 30, 30], paramsDir=params, outputDir=workdir)
    cell.libraryAddFragment(os.path.join(tests, "blocks", "benzene.car"), fragmentType="A")
    cell.addBondType("A:a-A:a")
    cell.seed(6)
    cell.growBlocks(4)
    return cell


def staticEnergy(cell, kw):
    """Potential energy of the current configuration: one step at a negligible timestep"""
    cell.runMD(mdCycles=1, dt=1e-9, quiet=True, **kw)
    return cell.analyse.last["potential_energy"]


def main():
    reference = {"hoomd": ".".join(map(str, ab_util.HOOMDVERSION)), "cases": {}}
    for setup in ("plain", "walls"):
        workdir = os.path.join("/tmp", "hoomd_parity_" + setup)
        cell = build(setup, workdir)
        pkl = cell.writePickle(os.path.join(here, "cell_" + setup))
        cell.close()
        for name, (caseSetup, kw) in CASES.items():
            if caseSetup != setup:
                continue
            restored = ab_util.cellFromPickle(pkl, paramsDir=params, outputDir=workdir + "_" + name)
            reference["cases"][name] = {"pickle": os.path.basename(pkl), "kwargs": kw,
                                        "potential_energy": staticEnergy(restored, kw)}
            restored.close()
            print(name, reference["cases"][name]["potential_energy"])
    with open(os.path.join(here, "reference.json"), "w") as f:
        json.dump(reference, f, indent=1)


if __name__ == "__main__":
    sys.exit(main())
