"""Read a run directory written by Ambuild with Cell(recordRun=True)."""
import fnmatch
import hashlib
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

RUN_FILE = "run.json"
EVENTS_FILE = "events.jsonl"

# Poreblazer's nitrogen_network.grd is ~13 MB per run and not needed afterwards;
# .ambuild-uploaded is the marker written by the command-line tool
DEFAULT_EXCLUDES = ["*.grd", "*.tmp", ".ambuild-uploaded"]


def sha256File(path):
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def isRunDirectory(path):
    return os.path.isfile(os.path.join(path, RUN_FILE))


def findRunDirectories(root):
    """Return every run directory at or below root, including nested child runs"""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        if RUN_FILE in filenames:
            found.append(dirpath)
        dirnames.sort()
    return sorted(found)


class RunDirectory:
    def __init__(self, path, excludes=None):
        self.path = os.path.abspath(path)
        self.excludes = DEFAULT_EXCLUDES if excludes is None else excludes
        with open(os.path.join(self.path, RUN_FILE)) as f:
            self.run = json.load(f)
        self.runId = self.run["run_id"]

    @property
    def status(self):
        return self.run["status"]

    def secondsSinceUpdate(self):
        """Seconds since run.json or events.jsonl last changed"""
        mtimes = [
            os.path.getmtime(os.path.join(self.path, name))
            for name in (RUN_FILE, EVENTS_FILE)
            if os.path.isfile(os.path.join(self.path, name))
        ]
        return time.time() - max(mtimes)

    def events(self):
        """Return [(seq, event)] for each complete line of events.jsonl.

        A final line without a newline may still be being written, so it is skipped.
        """
        path = os.path.join(self.path, EVENTS_FILE)
        if not os.path.isfile(path):
            return []
        events = []
        with open(path) as f:
            for seq, line in enumerate(f):
                if not line.endswith("\n"):
                    logger.warning("Skipping incomplete last event in %s", path)
                    break
                events.append((seq, json.loads(line)))
        return events

    def files(self):
        """Return the paths, relative and / separated, of the files to upload.

        Child run directories below this one are left for their own upload.
        """
        paths = []
        for dirpath, dirnames, filenames in os.walk(self.path):
            if dirpath != self.path and isRunDirectory(dirpath):
                dirnames[:] = []
                continue
            dirnames.sort()
            for name in sorted(filenames):
                if any(fnmatch.fnmatch(name, pattern) for pattern in self.excludes):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, name), self.path)
                paths.append(rel.replace(os.sep, "/"))
        return sorted(paths)

    def localPath(self, relpath):
        return os.path.join(self.path, *relpath.split("/"))
