"""
Builds are reproducible from a seed: the same recipe and seed give the same structure
in different processes, whatever the string-hash salt (PYTHONHASHSEED) or memory layout.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

from context import ab_util
from context import BLOCKS_DIR, PARAMS_DIR

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# A build with two fragment types and three bond types, so that choices are made among
# several blocks and several end-group types; junk objects shift the memory layout
RECIPE = r"""
import hashlib, json, os, random, sys
junk = [object() for _ in range(int(sys.argv[3]))]
from ambuild import ab_cell
blocks, params, outdir = sys.argv[4], sys.argv[5], sys.argv[1]
if sys.argv[2] == "none":  # no seed: Python's own start-up state
    kwargs = {}
elif sys.argv[2] == "replay":  # replay the recorded run in the directory named by the environment
    kwargs = {"randomState": os.environ["REPLAY_RUN"]}
else:
    kwargs = {"seed": int(sys.argv[2])}
cell = ab_cell.Cell([25, 25, 25], paramsDir=params, outputDir=outdir, recordRun=True, **kwargs)
cell.libraryAddFragment(os.path.join(blocks, "ch4.car"), fragmentType="A")
# with MD, B is also methane: the test parameters have no methane-benzene terms
cell.libraryAddFragment(os.path.join(blocks, "ch4.car" if sys.argv[6] == "md" else "benzene2.car"),
                        fragmentType="B")
cell.addBondType("A:a-B:a")
cell.addBondType("A:a-A:a")
cell.addBondType("B:a-B:a")
cell.seed(6)
cell.growBlocks(12, cellEndGroups=None, libraryEndGroups=None, maxTries=50)
cell.joinBlocks(2, maxTries=50)
cell.zipBlocks(bondMargin=1.0, bondAngleMargin=30)
# Split the largest block by deleting a fragment from its middle, then carry on
largest = max(cell.blocks.values(), key=lambda b: (b.numAtoms(), -b.id))
if len(largest.fragments) > 2:
    cell.deleteFragment(largest.fragments[len(largest.fragments) // 2], block=largest)
cell.growBlocks(6, cellEndGroups=None, libraryEndGroups=None, maxTries=50)
cell.zipBlocks(bondMargin=1.0, bondAngleMargin=30)
if sys.argv[6] == "md":
    cell.optimiseGeometry(rigidBody=True, optCycles=200, dt=0.005)
cell.writeXyz("final.xyz")
with open(os.path.join(outdir, "final.xyz"), "rb") as f:
    xyz = hashlib.sha256(f.read()).hexdigest()
print(json.dumps({"xyz": xyz, "atoms": cell.numAtoms(), "blocks": cell.numBlocks(),
                  "ids": sorted(cell.blocks.keys())[:5]}))
cell.close()
"""

# Build part way, checkpoint (dump), then carry on; or resume from that checkpoint and
# carry on the same way
RESUME = r"""
import hashlib, json, os, sys
junk = [object() for _ in range(int(sys.argv[3]))]
from ambuild import ab_cell, ab_util
mode, outdir, blocks, params = sys.argv[1], sys.argv[2], sys.argv[4], sys.argv[5]

def carryOn(cell):
    cell.growBlocks(8, cellEndGroups=None, libraryEndGroups=None, maxTries=50)
    largest = max(cell.blocks.values(), key=lambda b: (b.numAtoms(), -b.id))
    if len(largest.fragments) > 2:
        cell.deleteFragment(largest.fragments[len(largest.fragments) // 2], block=largest)
    cell.growBlocks(4, cellEndGroups=None, libraryEndGroups=None, maxTries=50)
    cell.zipBlocks(bondMargin=1.0, bondAngleMargin=30)

if mode == "checkpoint":
    cell = ab_cell.Cell([25, 25, 25], paramsDir=params, outputDir=outdir, seed=9)
    cell.libraryAddFragment(os.path.join(blocks, "ch4.car"), fragmentType="A")
    cell.libraryAddFragment(os.path.join(blocks, "benzene2.car"), fragmentType="B")
    cell.addBondType("A:a-B:a")
    cell.addBondType("A:a-A:a")
    cell.addBondType("B:a-B:a")
    cell.seed(6)
    cell.growBlocks(8, cellEndGroups=None, libraryEndGroups=None, maxTries=50)
    pickle = cell.dump()
else:
    pickle = sys.argv[6]
    cell = ab_util.cellFromPickle(pickle, paramsDir=params, outputDir=outdir,
                                  restoreRandomState=(mode == "resume"))
carryOn(cell)
cell.writeXyz("final.xyz")
with open(os.path.join(outdir, "final.xyz"), "rb") as f:
    print(json.dumps({"xyz": hashlib.sha256(f.read()).hexdigest(), "pickle": pickle,
                      "atoms": cell.numAtoms()}))
cell.close()
"""


def resume(mode, outdir, hashSeed, junk, pickle=""):
    """Run RESUME in a fresh interpreter: mode checkpoint, resume or resume-without-state"""
    env = dict(os.environ, PYTHONHASHSEED=str(hashSeed))
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", RESUME, mode, outdir, str(junk), BLOCKS_DIR, PARAMS_DIR, pickle],
        capture_output=True, text=True, env=env, cwd=os.path.dirname(outdir),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-3000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def build(seed, hashSeed, junk, md=False, replay=None, keep=None):
    """Run the recipe in a fresh interpreter; return its summary. seed None builds without a
    seed; replay, a run directory, replays that run instead; keep, if given, is where to
    leave the run directory"""
    with tempfile.TemporaryDirectory() as tmp:
        outdir = keep or os.path.join(tmp, "run")
        env = dict(os.environ, PYTHONHASHSEED=str(hashSeed))
        if replay:
            env["REPLAY_RUN"] = replay
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, "-c", RECIPE, outdir, "replay" if replay else ("none" if seed is None else str(seed)),
             str(junk), BLOCKS_DIR, PARAMS_DIR,
             "md" if md else "nomd"],
            capture_output=True, text=True, env=env, cwd=tmp,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[-3000:])
        return json.loads(proc.stdout.strip().splitlines()[-1])


class Test(unittest.TestCase):
    def testSameSeedSameStructure(self):
        """The same seed gives the same structure in processes with different hash salts and memory"""
        first = build(seed=7, hashSeed=1, junk=10)
        second = build(seed=7, hashSeed=2, junk=100000)
        self.assertGreater(first["blocks"], 0)
        self.assertEqual(first, second)

    @unittest.skipUnless(ab_util.HOOMDVERSION, "Needs HOOMD-blue")
    def testSameSeedSameStructureWithOptimisation(self):
        """Including a HOOMD-blue geometry optimisation (CPU, one rank)"""
        first = build(seed=11, hashSeed=3, junk=10, md=True)
        second = build(seed=11, hashSeed=4, junk=50000, md=True)
        self.assertEqual(first, second)

    def testUnseededRunCanBeReplayed(self):
        """A run without a seed records the generator's state; randomState replays it"""
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, "first")
            original = build(seed=None, hashSeed=5, junk=10, keep=first)
            with open(os.path.join(first, "run.json")) as f:
                run = json.load(f)
            self.assertEqual(run["random"], {"seed": None, "state": "inputs/random/random_state.json"})
            self.assertIn("random", [i["kind"] for i in run["inputs"]])
            replayed = build(seed=None, hashSeed=6, junk=20000, replay=first)
            self.assertEqual(original["xyz"], replayed["xyz"])

    def testResumeFromPickleContinuesTheSameBuild(self):
        """A build resumed from a checkpoint in another process ends as the uninterrupted build"""
        with tempfile.TemporaryDirectory() as tmp:
            uninterrupted = resume("checkpoint", os.path.join(tmp, "first"), hashSeed=1, junk=10)
            resumed = resume("resume", os.path.join(tmp, "second"), hashSeed=2, junk=50000,
                             pickle=uninterrupted["pickle"])
            self.assertEqual(resumed["xyz"], uninterrupted["xyz"])
            # Without the saved state the generator carries on from elsewhere
            other = resume("resume-without-state", os.path.join(tmp, "third"), hashSeed=2, junk=10,
                           pickle=uninterrupted["pickle"])
            self.assertNotEqual(other["xyz"], uninterrupted["xyz"])

    def testDifferentSeedsDiffer(self):
        """Different seeds give different structures (the seed is actually used)"""
        self.assertNotEqual(build(seed=7, hashSeed=1, junk=10)["xyz"], build(seed=8, hashSeed=1, junk=10)["xyz"])


if __name__ == "__main__":
    unittest.main()
