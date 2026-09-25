"""Record a build as a self-contained run directory.

A recorded run directory contains:
    run.json      - run id, status, timings and provenance (versions, host, command, cell)
    events.jsonl  - one JSON object per line for every event (see ab_analyse)
    inputs/       - copies of the script, parameter files and building blocks used
alongside the files the cell writes itself (csv, log, pickles, structure files).
"""
import datetime
import hashlib
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import uuid

import numpy as np

from ambuild import ab_analyse
from ambuild import ab_util
from ambuild.version import __version__

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
RUN_FILE = "run.json"
EVENTS_FILE = "events.jsonl"
INPUTS_DIR = "inputs"

# Event types added to those in ab_analyse
RUN_STARTED = "run_started"  # data: run_id
INPUT = "input"  # data: an entry of run.json "inputs"
RUN_FINISHED = "run_finished"  # data: status, error


def sha256File(path):
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _jsonDefault(obj):
    """Convert the numpy and other non-JSON types that appear in events"""
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    return str(obj)


def gitInfo():
    """Return the git commit and dirty flag of the ambuild source, or None if not a git checkout"""
    srcdir = os.path.dirname(os.path.abspath(__file__))
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=srcdir, stderr=subprocess.DEVNULL
        )
        status = subprocess.check_output(
            ["git", "status", "--porcelain", "--", srcdir],
            cwd=srcdir,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return {"commit": commit.decode().strip(), "dirty": bool(status.strip())}


# Slurm variables recorded in run.json, so a run can be matched to its job
SLURM_VARIABLES = [
    "SLURM_CLUSTER_NAME",
    "SLURM_JOB_ID",
    "SLURM_ARRAY_JOB_ID",
    "SLURM_ARRAY_TASK_ID",
    "SLURM_JOB_PARTITION",
    "SLURM_JOB_NUM_NODES",
    "SLURM_NTASKS",
    "SLURM_CPUS_PER_TASK",
]


def schedulerInfo():
    """Return the Slurm job this process is part of, or None when not run under Slurm"""
    if "SLURM_JOB_ID" not in os.environ:
        return None
    return {
        "type": "slurm",
        "variables": {v: os.environ[v] for v in SLURM_VARIABLES if v in os.environ},
    }


class JsonlSink:
    """Append every event to a JSON Lines file"""

    def __init__(self, path):
        self.path = path
        self._handle = open(path, "a")

    def handle(self, event):
        self._handle.write(json.dumps(event, default=_jsonDefault, sort_keys=True) + "\n")
        self._handle.flush()

    def close(self):
        self._handle.close()


def checkRunDirectory(outputDir):
    """Raise an error if outputDir cannot hold a new recorded run"""
    if outputDir is None:
        raise ValueError("Recording a run needs a Cell with an outputDir")
    if os.path.exists(os.path.join(outputDir, RUN_FILE)):
        raise RuntimeError("Directory already holds a recorded run: {0}".format(outputDir))


class RunRecorder:
    """Keep run.json, events.jsonl and inputs/ for a cell with an outputDir"""

    def __init__(self, cell, runId=None, parentRunId=None):
        checkRunDirectory(cell.outputDir)
        self.cell = cell
        self.directory = cell.outputDir
        self.runId = runId or str(uuid.uuid4())
        gitinfo = gitInfo()
        self.run = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.runId,
            "parent_run_id": parentRunId,
            "status": "running",
            "started": _now(),
            "finished": None,
            "error": None,
            "ambuild": {
                "version": __version__,
                "git_commit": gitinfo["commit"] if gitinfo else None,
                "git_dirty": gitinfo["dirty"] if gitinfo else None,
            },
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "hostname": socket.gethostname(),
                "numpy": np.__version__,
                "hoomd": ".".join(map(str, ab_util.HOOMDVERSION))
                if ab_util.HOOMDVERSION
                else None,
            },
            "command": list(sys.argv),
            "scheduler": schedulerInfo(),
            "cell": {
                "box_dim": [float(x) for x in cell.dim],
                "atom_margin": cell.atomMargin,
                "bond_margin": cell.bondMargin,
                "bond_angle_margin_degrees": round(float(np.degrees(cell.bondAngleMargin)), 10),
                "params_dir": os.path.abspath(cell.paramsDir),
            },
            "inputs": [],
        }
        self._write()
        self.sink = JsonlSink(os.path.join(self.directory, EVENTS_FILE))
        cell.analyse.addSink(self.sink)
        cell.analyse.emit(RUN_STARTED, {"run_id": self.runId})

        script = sys.argv[0] if sys.argv else ""
        if script.endswith(".py") and os.path.isfile(script):
            self.addInput(script, "script")
        for name in sorted(os.listdir(cell.paramsDir)):
            path = os.path.join(cell.paramsDir, name)
            if os.path.isfile(path):
                self.addInput(path, "params")
        return

    def addInput(self, path, kind, **meta):
        """Copy an input file to inputs/<kind>/ and record it in run.json"""
        source = os.path.abspath(path)
        sha256 = sha256File(source)
        for entry in self.run["inputs"]:
            if entry["kind"] == kind and entry["source"] == source and entry["sha256"] == sha256:
                # Already copied (e.g. one .car file used for two fragment types)
                entry = dict(entry)
                entry.update(meta)
                self.run["inputs"].append(entry)
                self._write()
                self.cell.analyse.emit(INPUT, entry)
                return entry
        destdir = os.path.join(self.directory, INPUTS_DIR, kind)
        os.makedirs(destdir, exist_ok=True)
        name = os.path.basename(path)
        dest = os.path.join(destdir, name)
        count = 1
        while os.path.exists(dest):  # Two inputs with the same name from different directories
            stem, suffix = os.path.splitext(name)
            dest = os.path.join(destdir, "{0}_{1}{2}".format(stem, count, suffix))
            count += 1
        shutil.copyfile(path, dest)
        entry = {
            "kind": kind,
            "source": source,
            "path": os.path.relpath(dest, self.directory).replace(os.sep, "/"),
            "size": os.path.getsize(dest),
            "sha256": sha256,
        }
        entry.update(meta)
        self.run["inputs"].append(entry)
        self._write()
        self.cell.analyse.emit(INPUT, entry)
        return entry

    def finish(self, error=None):
        """Mark the run finished, or failed if error is given"""
        self.run["status"] = "failed" if error else "finished"
        self.run["error"] = error
        self.run["finished"] = _now()
        self.cell.analyse.emit(
            RUN_FINISHED, {"status": self.run["status"], "error": error}
        )
        self._write()
        return

    def _write(self):
        path = os.path.join(self.directory, RUN_FILE)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.run, f, indent=2, default=_jsonDefault)
            f.write("\n")
        os.replace(tmp, path)
