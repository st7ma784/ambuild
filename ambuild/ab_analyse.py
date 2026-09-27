import collections
import csv
import time

# Event types sent to sinks. Each event is a dict:
#   {"type": <event type>, "step": <step number>, "timestamp": <time.time()>, "data": {...}}
STEP = "step"  # data: the row written to the csv file (fragment_types as a dict)
ARTIFACT = "artifact"  # data: path, kind, size, sha256
PORE_RESULT = "pore_result"  # data: the results of Cell.poreblazer()

FIELDNAMES = [
    "step",
    "type",
    "tot_time",
    "time",
    "num_frags",
    "num_particles",
    "num_blocks",
    "density",
    "num_free_endGroups",
    "potential_energy",
    "num_tries",
    "fragment_types",
    "file_count",
]


class CsvSink:
    """Write step events to a csv file, one row per step"""

    def __init__(self, logfile):
        self.logfile = logfile
        self._logHandle = open(self.logfile, "w", newline="")
        self._logWriter = csv.DictWriter(self._logHandle, FIELDNAMES)
        self._logWriter.writeheader()

    def handle(self, event):
        if event["type"] == STEP:
            row = event["data"]
            if isinstance(row.get("fragment_types"), dict):
                # the csv keeps the format it always had: the repr of Cell.fragmentTypes()
                row = dict(row, fragment_types=str(collections.defaultdict(list, row["fragment_types"])))
            self._logWriter.writerow(row)
            self._logHandle.flush()

    def close(self):
        self._logHandle.close()


class Analyse:
    """Record each step of a build and send events describing it to a list of sinks.

    A sink is any object with handle(event) and close() methods. The csv file is
    written by a CsvSink, which is always the first sink.
    """

    def __init__(self, cell, logfile="ambuild.csv"):

        self.fieldnames = FIELDNAMES
        self.cell = cell

        self.step = 0
        self._startTime = time.time()
        self._stepTime = time.time()

        # Need to create an initial entry as we query the previous one for any data we don't have
        d = {}
        for f in self.fieldnames:
            if f == "time":
                d[f] = self._startTime
            # elif f == 'file_count':
            elif f == "type":
                d[f] = "init"
            else:
                d[f] = 0

        self.last = d

        self.logfile = logfile
        self.sinks = [CsvSink(self.logfile)]

        return

    def addSink(self, sink):
        """Send all subsequent events to sink as well"""
        self.sinks.append(sink)
        return

    def emit(self, etype, data):
        """Send an event of type etype with payload data to every sink"""
        event = {"type": etype, "step": self.step, "timestamp": time.time(), "data": data}
        for sink in self.sinks:
            sink.handle(event)
        return

    def start(self):
        """Called whenever we start a step"""
        assert self._stepTime == None
        assert self.last
        self.step += 1
        self._stepTime = time.time()
        return

    def stop(self, stype, d={}):
        """Called at the end of a step with the data to write to the csv file"""

        new = {}

        for f in self.fieldnames:
            if f == "type":
                new[f] = stype
            elif f == "step":
                new[f] = self.step
            elif f == "time":
                new[f] = time.time() - self._stepTime
            elif f == "tot_time":
                new[f] = time.time() - self._startTime
                self._stepTime
            elif f == "num_blocks":
                new[f] = self.cell.numBlocks()
            elif f == "num_frags":
                new[f] = self.cell.numFragments()
            elif f == "num_particles":
                new[f] = self.cell.numAtoms()
            elif f == "num_free_endGroups":
                new[f] = self.cell.numFreeEndGroups()
            elif f == "density":
                new[f] = self.cell.density()
            elif f == "fragment_types":
                new[f] = dict(self.cell.fragmentTypes())
            elif f == "file_count":
                new[f] = self.cell._fileCount
            elif f in d:
                new[f] = d[f]
            else:
                new[f] = self.last[f]

        self.emit(STEP, new)

        self.last = new
        self._stepTime = None
        self.start()
        return

    def close(self):
        """Close all the sinks"""
        for sink in self.sinks:
            sink.close()
        return
