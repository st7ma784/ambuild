"""
Tests for the events a cell sends to its sinks
"""
import collections
import csv
import hashlib
import os
import shutil
import tempfile
import unittest

from context import ab_analyse
from context import ab_cell
from context import BLOCKS_DIR, PARAMS_DIR


class RecordingSink:
    def __init__(self):
        self.events = []
        self.closed = False

    def handle(self, event):
        self.events.append(event)

    def close(self):
        self.closed = True

    def ofType(self, etype):
        return [e for e in self.events if e["type"] == etype]


class Test(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sink = RecordingSink()
        self.cell = ab_cell.Cell([20, 20, 20], paramsDir=PARAMS_DIR, outputDir=self.tmpdir)
        self.cell.libraryAddFragment(filename=os.path.join(BLOCKS_DIR, "ch4.car"), fragmentType="A")
        self.cell.addBondType("A:a-A:a")
        self.cell.addEventSink(self.sink)

    def tearDown(self):
        self.cell.close()
        shutil.rmtree(self.tmpdir)

    def testCsvMatchesStepEvents(self):
        """The csv file is exactly what csv.DictWriter writes for the step events"""
        self.cell.seed(3)
        self.cell.growBlocks(2)
        self.cell.close()

        steps = self.sink.ofType(ab_analyse.STEP)
        self.assertEqual({e["data"]["type"] for e in steps}, {"seed", "grow"})
        for e in steps:
            self.assertEqual(e["step"], e["data"]["step"])

        expected = os.path.join(self.tmpdir, "expected.csv")
        with open(expected, "w", newline="") as f:
            writer = csv.DictWriter(f, ab_analyse.FIELDNAMES)
            writer.writeheader()
            for e in steps:
                # fragment_types is a dict in the event; the csv keeps its old format
                ft = collections.defaultdict(list, e["data"]["fragment_types"])
                writer.writerow(dict(e["data"], fragment_types=str(ft)))
        with open(expected, "rb") as f1, open(self.cell.logcsv, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())

    def testFragmentTypesIsADict(self):
        """Step events carry fragment_types as a dict (JSON-friendly); the csv keeps the repr"""
        self.cell.seed(3)
        self.cell.close()
        step = self.sink.ofType(ab_analyse.STEP)[-1]
        self.assertEqual(step["data"]["fragment_types"], {"A": 3})
        self.assertIs(type(step["data"]["fragment_types"]), dict)
        with open(self.cell.logcsv, newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[-1]["fragment_types"], "defaultdict(<class 'list'>, {'A': 3})")

    def testJoinAndZipAreSteps(self):
        """joinBlocks and zipBlocks each record a step, including a zip that makes no bonds"""
        self.cell.seed(4)
        self.cell.joinBlocks(1, maxTries=50)
        self.cell.zipBlocks(bondMargin=0.01, bondAngleMargin=0.01)
        self.cell.close()
        types = [e["data"]["type"] for e in self.sink.ofType(ab_analyse.STEP)]
        self.assertEqual(set(types[:-2]), {"seed"})  # one step per block seeded
        self.assertEqual(types[-2:], ["join", "zip"])
        with open(self.cell.logcsv, newline="") as f:
            self.assertEqual([r["type"] for r in csv.DictReader(f)], types)

    def testArtifactEvents(self):
        self.cell.seed(2)
        pkl = self.cell.dump()
        self.cell.writeXyz("cell.xyz")
        self.cell.writeCml("cell.cml")

        artifacts = [e["data"] for e in self.sink.ofType(ab_analyse.ARTIFACT)]
        self.assertEqual([a["kind"] for a in artifacts], ["pickle", "structure", "xyz", "cml"])
        self.assertEqual(artifacts[0]["path"], pkl)
        self.assertEqual(artifacts[1]["path"], os.path.join(self.tmpdir, "step_1.xyz"))  # dump's structure
        self.assertEqual(artifacts[2]["path"], os.path.join(self.tmpdir, "cell.xyz"))
        for a in artifacts:
            with open(a["path"], "rb") as f:
                content = f.read()
            self.assertEqual(a["size"], len(content))
            self.assertEqual(a["sha256"], hashlib.sha256(content).hexdigest())

    @unittest.skipUnless(os.path.isfile("/bin/cat"), "Needs /bin/cat as a dummy executable")
    def testPoreResultEvent(self):
        self.cell.seed(2)
        results = self.cell.poreblazer("/bin/cat")

        pore = self.sink.ofType(ab_analyse.PORE_RESULT)
        self.assertEqual(len(pore), 1)
        self.assertIs(pore[0]["data"], results)
        self.assertEqual(pore[0]["data"]["returncode"], 0)

    def testCloseClosesSinks(self):
        self.cell.close()
        self.assertTrue(self.sink.closed)


if __name__ == "__main__":
    unittest.main()
