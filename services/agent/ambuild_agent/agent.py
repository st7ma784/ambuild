"""The agent: claims submissions from the web API and runs them.

The local backend runs each submission here, as a process: `python -m ambuild.recipe run`
in a run directory of its own, then `ambuild-upload`. In a container (the Compose demo, a
K3s Deployment) that makes the container the worker. It talks only to the web API, with
its token; the upload job is what writes to PostgreSQL and object storage, as the Slurm
upload jobs do.
"""
import hashlib
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from ambuild import recipe as ab_recipe

from ambuild_agent import __version__

logger = logging.getLogger("ambuild_agent")

# Kept from the runner's environment: it needs no database or storage credentials
SECRET_VARIABLES = ("DATABASE_URL", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                    "AMBUILD_AGENT_TOKEN", "PGPASSWORD")
TERMINATE_GRACE = 30  # seconds between SIGTERM and SIGKILL when cancelling


@dataclass
class Config:
    api_url: str
    token: str
    directory: str = "agent"
    slots: int = 1
    poll: float = 5.0
    heartbeat: float = 30.0
    upload: str = "ambuild-upload"  # empty: do not upload
    upload_every: float = 60.0  # upload running runs this often (0: only when they end)
    keep_runs: bool = False  # keep run directories after a successful upload
    python: str = sys.executable
    name: str = ""

    @classmethod
    def fromEnvironment(cls):
        env = os.environ
        if not env.get("AMBUILD_API_URL") or not env.get("AMBUILD_AGENT_TOKEN"):
            raise SystemExit("Set AMBUILD_API_URL and AMBUILD_AGENT_TOKEN")
        return cls(
            api_url=env["AMBUILD_API_URL"].rstrip("/"),
            token=env["AMBUILD_AGENT_TOKEN"],
            directory=env.get("AMBUILD_AGENT_DIR", "agent"),
            slots=int(env.get("AMBUILD_AGENT_SLOTS", "1")),
            poll=float(env.get("AMBUILD_AGENT_POLL", "5")),
            heartbeat=float(env.get("AMBUILD_AGENT_HEARTBEAT", "30")),
            upload=env.get("AMBUILD_AGENT_UPLOAD", "ambuild-upload"),
            upload_every=float(env.get("AMBUILD_AGENT_UPLOAD_EVERY", "60")),
            keep_runs=env.get("AMBUILD_AGENT_KEEP_RUNS", "0") in ("1", "true", "yes"),
            python=env.get("AMBUILD_AGENT_PYTHON", sys.executable),
        )


class ApiError(Exception):
    def __init__(self, status, detail):
        self.status = status
        super().__init__("HTTP {0}: {1}".format(status, detail))


class Api:
    def __init__(self, url, token, timeout=30):
        self.url = url
        self.token = token
        self.timeout = timeout

    def _open(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json",
            "User-Agent": "ambuild-agent/" + __version__})
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode()).get("detail", exc.reason)
            except ValueError:
                detail = exc.reason
            raise ApiError(exc.code, detail)

    def call(self, method, path, body=None):
        with self._open(method, path, body) as response:
            return json.loads(response.read().decode() or "null")

    def download(self, path, dest, sha256):
        """Save the response to dest, checking its sha256"""
        tmp = dest + ".part"
        digest = hashlib.sha256()
        with self._open("GET", path) as response, open(tmp, "wb") as f:
            for chunk in iter(lambda: response.read(1 << 20), b""):
                digest.update(chunk)
                f.write(chunk)
        if digest.hexdigest() != sha256:
            os.remove(tmp)
            raise RuntimeError("Downloaded {0} does not match its sha256".format(path))
        os.replace(tmp, dest)


@dataclass
class Job:
    submission: dict
    rundir: str
    workdir: str
    process: subprocess.Popen
    log: object
    started: float = field(default_factory=time.monotonic)
    last_upload: float = field(default_factory=time.monotonic)
    cancelled: bool = False
    terminated_at: float = None

    @property
    def id(self):
        return self.submission["submission_id"]


class Agent:
    def __init__(self, config, api=None):
        self.config = config
        self.api = api or Api(config.api_url, config.token)
        self.directory = os.path.abspath(config.directory)
        self.blobs = os.path.join(self.directory, "blobs")
        for sub in ("blobs", "runs", "work"):
            os.makedirs(os.path.join(self.directory, sub), exist_ok=True)
        self.jobs = {}
        self.stopping = False
        self.last_heartbeat = 0.0

    # --- the loop

    def run(self):
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        logger.info("ambuild-agent %s: %s, %d slot(s), in %s", __version__, self.config.api_url, self.config.slots,
                    self.directory)
        while not (self.stopping and not self.jobs):
            self.step()
            time.sleep(self.config.poll if not self.stopping else 1)
        logger.info("stopped")

    def _stop(self, signum, frame):
        if not self.stopping:
            logger.info("stopping: cancelling %d run(s)", len(self.jobs))
            self.stopping = True
            for job in self.jobs.values():
                self.terminate(job)

    def step(self):
        """One pass: check the runs, heartbeat when due, claim work while there are free slots"""
        for job in list(self.jobs.values()):
            self.check(job)
        try:
            if time.monotonic() - self.last_heartbeat >= self.config.heartbeat:
                self.heartbeat()
            while not self.stopping and len(self.jobs) < self.config.slots:
                if not self.claim():
                    break
        except (urllib.error.URLError, OSError, ApiError) as exc:
            logger.warning("web API: %s", exc)

    def heartbeat(self):
        load = os.getloadavg() if hasattr(os, "getloadavg") else None
        reply = self.api.call("POST", "/api/agent/heartbeat", {
            "host": socket.gethostname(), "version": __version__,
            "capabilities": {"backend": "local", "slots": self.config.slots},
            "summary": {"running": len(self.jobs), "cpus": os.cpu_count(), "load": load,
                        "disk_free_gb": round(shutil.disk_usage(self.directory).free / 1e9, 1)},
            "active": sorted(self.jobs)})
        self.last_heartbeat = time.monotonic()
        for submissionId in reply.get("cancel", []):
            job = self.jobs.get(submissionId)
            if job is not None and not job.cancelled:
                logger.info("submission %s: cancelling", submissionId)
                job.cancelled = True
                self.terminate(job)

    def claim(self):
        """Claim and start one submission; False when the queue is empty"""
        sub = self.api.call("POST", "/api/agent/claim")["submission"]
        if sub is None:
            return False
        logger.info("submission %s: claimed (%s, run %s)", sub["submission_id"], sub["name"], sub["run_id"])
        try:
            self.fetchInputs(sub["recipe"])
        except Exception as exc:
            logger.error("submission %s: %s", sub["submission_id"], exc)
            self.report(sub["submission_id"], "failed", error="Could not fetch the inputs: {0}".format(exc))
            return True
        try:
            self.report(sub["submission_id"], "running", external_id="{0}:pending".format(socket.gethostname()))
        except ApiError as exc:
            if exc.status == 409:  # cancelled meanwhile
                logger.info("submission %s: no longer wanted (%s)", sub["submission_id"], exc)
                return True
            raise
        job = self.start(sub)
        self.report(sub["submission_id"], "running", external_id="{0}:{1}".format(socket.gethostname(), job.process.pid))
        return True

    # --- one submission

    def fetchInputs(self, recipe):
        for digest in ab_recipe.references(recipe):
            dest = os.path.join(self.blobs, digest)
            if not os.path.isfile(dest):
                self.api.download("/api/blobs/" + digest, dest, digest)

    def start(self, sub):
        runId = sub["run_id"]
        workdir = os.path.join(self.directory, "work", runId)
        rundir = os.path.join(self.directory, "runs", runId)
        os.makedirs(workdir, exist_ok=True)
        recipeFile = os.path.join(workdir, "recipe.json")
        with open(recipeFile, "w", encoding="utf-8") as f:
            json.dump(sub["recipe"], f, indent=2)
        command = [self.config.python, "-m", "ambuild.recipe", "run", recipeFile, "--output", rundir,
                   "--blobs", self.blobs, "--run-id", runId]
        if sub.get("seed") is not None:
            command += ["--seed", str(sub["seed"])]
        env = {k: v for k, v in os.environ.items() if k not in SECRET_VARIABLES}
        log = open(os.path.join(workdir, "runner.log"), "ab")
        process = subprocess.Popen(command, cwd=workdir, stdout=log, stderr=subprocess.STDOUT, env=env,
                                   start_new_session=True)
        job = Job(submission=sub, rundir=rundir, workdir=workdir, process=process, log=log)
        self.jobs[sub["submission_id"]] = job
        logger.info("submission %s: started, pid %s", job.id, process.pid)
        return job

    def terminate(self, job):
        if job.process.poll() is None and job.terminated_at is None:
            job.process.terminate()  # the runner records the run as failed ("Cancelled")
            job.terminated_at = time.monotonic()

    def check(self, job):
        code = job.process.poll()
        now = time.monotonic()
        if code is None:
            if job.terminated_at is not None and now - job.terminated_at > TERMINATE_GRACE:
                job.process.kill()
            elif self.config.upload and self.config.upload_every and now - job.last_upload >= self.config.upload_every:
                self.upload(job, final=False)
            return
        job.log.close()
        uploaded = self.upload(job, final=True)
        state, error = self.outcome(job, code)
        if not uploaded and state == "finished":
            state, error = "failed", "The run finished but could not be uploaded (see the agent's log)"
        try:
            self.report(job.id, state, error=error)
        except (urllib.error.URLError, OSError, ApiError) as exc:
            logger.warning("submission %s: could not report %s: %s; will retry", job.id, state, exc)
            job.log = open(os.devnull, "ab")
            return  # keep the job, and try again on the next pass
        del self.jobs[job.id]
        logger.info("submission %s: %s", job.id, state)
        if uploaded and not self.config.keep_runs:
            shutil.rmtree(job.rundir, ignore_errors=True)
            shutil.rmtree(job.workdir, ignore_errors=True)

    def outcome(self, job, code):
        """(state, error) of a finished process"""
        run = None
        try:
            with open(os.path.join(job.rundir, "run.json")) as f:
                run = json.load(f)
        except (OSError, ValueError):
            pass
        if job.cancelled or self.stopping:
            return "cancelled" if job.cancelled else "failed", None if job.cancelled else "The agent stopped"
        if code == 0 and run and run.get("status") == "finished":
            return "finished", None
        if run and run.get("error"):
            return "failed", run["error"]
        return "failed", "The runner exited with code {0}: {1}".format(code, self.tail(job))

    def tail(self, job, lines=8):
        try:
            with open(os.path.join(job.workdir, "runner.log"), "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 4000))
                return " | ".join(f.read().decode("utf-8", "replace").strip().splitlines()[-lines:])
        except OSError:
            return "(no log)"

    def upload(self, job, final):
        """Upload the run directory; True on success"""
        job.last_upload = time.monotonic()
        if not self.config.upload:
            return False
        if not os.path.isfile(os.path.join(job.rundir, "run.json")):
            return False
        command = [self.config.upload] + (["--finalise"] if final else []) + [job.rundir]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            logger.warning("submission %s: upload failed: %s", job.id, result.stderr.strip()[-2000:])
            return False
        return True

    def report(self, submissionId, state, external_id=None, error=None):
        body = {"state": state}
        if external_id:
            body["external_id"] = external_id
        if error:
            body["error"] = error
        return self.api.call("PATCH", "/api/agent/submissions/{0}".format(submissionId), body)
