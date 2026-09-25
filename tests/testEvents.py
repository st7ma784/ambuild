"""
Tests for the events a cell sends to its sinks
"""
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
        with open(expected, "w") as f:
            writer = csv.DictWriter(f, ab_analyse.FIELDNAMES)
            writer.writeheader()
            for e in steps:
                writer.writerow(e["data"])
        with open(expected, "rb") as f1, open(self.cell.logcsv, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())

    def testArtifactEvents(self):
        self.cell.seed(2)
        pkl = self.cell.dump()
        self.cell.writeXyz("cell.xyz")
        self.cell.writeCml("cell.cml")

        artifacts = [e["data"] for e in self.sink.ofType(ab_analyse.ARTIFACT)]
        self.assertEqual([a["kind"] for a in artifacts], ["pickle", "xyz", "cml"])
        self.assertEqual(artifacts[0]["path"], pkl)
        self.assertEqual(artifacts[1]["path"], os.path.join(self.tmpdir, "cell.xyz"))
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
