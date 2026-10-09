"""The agent: claims submissions from the web API and runs them with a backend.

    local      each submission runs here, as a process (in a container or pod, that makes
               the container the worker); the agent uploads the run when it ends
    slurm      each submission is submitted with deploy/slurm/submit_build.sh from a login
               node; its build and upload jobs run on the cluster
    slurmrest  the same jobs, submitted through slurmrestd (Slurm's REST API) with a JWT,
               from any machine that can reach it; the agent needs no Slurm commands and
               no access to the cluster's filesystems

The agent talks only to the web API, with its token. Runs are uploaded by ambuild-upload
(the Slurm upload jobs, or the agent for the local backend and for live progress), which
is what writes to PostgreSQL and object storage; builds get no credentials.
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

# Kept from builds' environments: they need no database or storage credentials
SECRET_VARIABLES = ("DATABASE_URL", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                    "AMBUILD_AGENT_TOKEN", "PGPASSWORD", "AMBUILD_SLURM_JWT")
API_ERRORS = (urllib.error.URLError, OSError)


def buildEnvironment(extra=None):
    """os.environ without secrets, plus extra"""
    env = {k: v for k, v in os.environ.items() if k not in SECRET_VARIABLES}
    env.update(extra or {})
    return env


def readEnvFile(path):
    """KEY=VALUE lines (as the Slurm upload jobs source them), without comments or quotes"""
    values = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.replace("export ", "").strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[key] = value
    return values


def _jsonObject(env, name):
    try:
        value = json.loads(env.get(name) or "{}")
    except ValueError as exc:
        raise SystemExit("{0} must be a JSON object: {1}".format(name, exc))
    if not isinstance(value, dict):
        raise SystemExit("{0} must be a JSON object".format(name))
    return value


@dataclass
class Config:
    api_url: str
    token: str
    backend: str = "local"
    directory: str = "agent"  # local: blobs/, runs/, work/
    slots: int = 1
    poll: float = 5.0
    heartbeat: float = 30.0
    upload: str = "ambuild-upload"  # empty: no uploads by the agent
    upload_every: float = 60.0  # upload running runs this often (0: never)
    upload_env: str = ""  # a file of upload settings (the Slurm upload jobs' AMBUILD_UPLOAD_ENV)
    keep_runs: bool = False  # local: keep run directories after a successful upload
    python: str = sys.executable
    # slurm and slurmrest backends
    runs_root: str = ""  # on the cluster's shared filesystem
    slurm_dir: str = ""  # deploy/slurm: the job scripts (slurmrest sends their text)
    partition: str = ""
    sbatch_options: str = ""  # slurm only: more sbatch options
    xtb: bool = False  # slurm only: check each build with xTB afterwards (submit_build.sh --xtb)
    array_max: int = 50  # slurmrest: most array tasks running at once (slurm: submit_array.sh's)
    # slurmrest backend
    slurmrestd_url: str = ""
    slurm_user: str = ""
    slurm_jwt: str = ""
    slurm_jwt_file: str = ""  # read for every request, so the token can be replaced while running
    slurmrestd_version: str = ""  # e.g. v0.0.41; default: the newest tested one slurmrestd offers
    slurm_ca: str = ""  # CA bundle for an https slurmrestd
    slurm_setup: str = ""  # a shell line each job runs first, e.g. activating Ambuild's environment
    slurm_env: dict = field(default_factory=dict)  # the jobs' environment, besides Ambuild's variables
    slurm_job: dict = field(default_factory=dict)  # more job description fields, e.g. {"account": "chem"}
    slurm_log_dir: str = ""  # on the cluster, for the jobs' output (default runs_root)
    slurm_upload_env: str = ""  # on the cluster: the upload settings (default ~/.config/ambuild/upload.env)

    @classmethod
    def fromEnvironment(cls):
        env = os.environ
        if not env.get("AMBUILD_API_URL") or not env.get("AMBUILD_AGENT_TOKEN"):
            raise SystemExit("Set AMBUILD_API_URL and AMBUILD_AGENT_TOKEN")
        backend = env.get("AMBUILD_AGENT_BACKEND", "local")
        onSlurm = backend in ("slurm", "slurmrest")
        uploadEnv = env.get("AMBUILD_UPLOAD_ENV", "")
        if backend == "slurm" and not uploadEnv:
            default = os.path.expanduser("~/.config/ambuild/upload.env")
            uploadEnv = default if os.path.isfile(default) else ""
        return cls(
            api_url=env["AMBUILD_API_URL"].rstrip("/"),
            token=env["AMBUILD_AGENT_TOKEN"],
            backend=backend,
            directory=env.get("AMBUILD_AGENT_DIR", "agent"),
            slots=int(env.get("AMBUILD_AGENT_SLOTS", "1000" if onSlurm else "1")),
            poll=float(env.get("AMBUILD_AGENT_POLL", "15" if onSlurm else "5")),
            heartbeat=float(env.get("AMBUILD_AGENT_HEARTBEAT", "30")),
            upload=env.get("AMBUILD_AGENT_UPLOAD", "ambuild-upload"),
            upload_every=float(env.get("AMBUILD_AGENT_UPLOAD_EVERY", "60")),
            upload_env=uploadEnv,
            keep_runs=env.get("AMBUILD_AGENT_KEEP_RUNS", "0") in ("1", "true", "yes"),
            python=env.get("AMBUILD_AGENT_PYTHON", sys.executable),
            runs_root=env.get("AMBUILD_RUNS_ROOT", ""),
            slurm_dir=env.get("AMBUILD_SLURM_DIR", ""),
            partition=env.get("AMBUILD_SLURM_PARTITION", ""),
            sbatch_options=env.get("AMBUILD_SLURM_OPTIONS", ""),
            xtb=env.get("AMBUILD_AGENT_XTB", "0") in ("1", "true", "yes"),
            array_max=int(env.get("AMBUILD_ARRAY_MAX", "50")),
            slurmrestd_url=env.get("AMBUILD_SLURMRESTD_URL", ""),
            slurm_user=env.get("AMBUILD_SLURM_USER", ""),
            slurm_jwt=env.get("AMBUILD_SLURM_JWT", ""),
            slurm_jwt_file=env.get("AMBUILD_SLURM_JWT_FILE", ""),
            slurmrestd_version=env.get("AMBUILD_SLURMRESTD_VERSION", ""),
            slurm_ca=env.get("AMBUILD_SLURMRESTD_CA", ""),
            slurm_setup=env.get("AMBUILD_SLURM_SETUP", ""),
            slurm_env=_jsonObject(env, "AMBUILD_SLURMREST_ENV"),
            slurm_job=_jsonObject(env, "AMBUILD_SLURMREST_JOB"),
            slurm_log_dir=env.get("AMBUILD_SLURM_LOG_DIR", ""),
            slurm_upload_env=env.get("AMBUILD_SLURM_UPLOAD_ENV", ""),
        )

    def uploadEnvironment(self):
        env = dict(os.environ)
        if self.upload_env:
            env.update(readEnvFile(self.upload_env))
        return env


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
        tmp = "{0}.part-{1}".format(dest, os.getpid())
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
    """A submission being run; handle is the backend's (a process, Slurm job ids)"""
    submission: dict
    rundir: str
    workdir: str
    handle: object = None
    external_id: str = None
    state: str = None  # as last reported: submitted or running
    cancelled: bool = False
    last_upload: float = field(default_factory=time.monotonic)

    @property
    def id(self):
        return self.submission["submission_id"]


def writeRecipe(sub, workdir):
    """The submission's recipe as workdir/recipe.json; returns its path"""
    os.makedirs(workdir, exist_ok=True)
    path = os.path.join(workdir, "recipe.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sub["recipe"], f, indent=2)
    return path


def runOutcome(job, fallbackError):
    """(state, error) of a job that has ended, from its run.json"""
    run = None
    try:
        with open(os.path.join(job.rundir, "run.json")) as f:
            run = json.load(f)
    except (OSError, ValueError):
        pass
    if job.cancelled:
        return "cancelled", None
    if run and run.get("status") == "finished":
        return "finished", None
    if run and run.get("error"):
        return "failed", run["error"]
    return "failed", fallbackError


class Agent:
    def __init__(self, config, api=None, backend=None):
        from ambuild_agent import backends

        self.config = config
        self.api = api or Api(config.api_url, config.token)
        self.backend = backend or backends.make(config)
        if hasattr(self.backend, "attach"):
            self.backend.attach(self.api)  # slurmrest: learns how runs ended from the web API
        self.jobs = {}
        self.stopping = False
        self.last_heartbeat = 0.0
        self.last_error = None

    # --- the loop

    def run(self):
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        logger.info("ambuild-agent %s: %s backend, %s, %d slot(s)", __version__, self.backend.name,
                    self.config.api_url, self.config.slots)
        self.resume()
        while not (self.stopping and not self.backend.waitsOnStop(self.jobs)):
            self.step()
            time.sleep(self.config.poll if not self.stopping else 1)
        logger.info("stopped")

    def _stop(self, signum, frame):
        if not self.stopping:
            logger.info("stopping")
            self.stopping = True
            self.backend.stopping(self.jobs)

    def resume(self):
        """Take up the submissions this agent had before it (re)started"""
        while True:
            try:
                subs = self.api.call("GET", "/api/agent/submissions")["submissions"]
                break
            except API_ERRORS + (ApiError,) as exc:
                logger.warning("web API: %s; retrying", exc)
                time.sleep(min(self.config.poll, 10))
        for sub in subs:
            job = self.backend.resume(sub) if sub["state"] != "claimed" else None
            if job is not None:
                job.cancelled = sub["state"] == "cancelling"
                self.jobs[job.id] = job
                logger.info("submission %s: resumed (%s)", job.id, job.external_id)
            elif sub["state"] == "claimed":  # never started: back to the queue
                self.report(sub["submission_id"], "queued")
            # else the heartbeat reports it lost

    def step(self):
        """One pass: check the runs, heartbeat when due, claim work while there are free slots"""
        if hasattr(self.backend, "beginPass"):
            self.backend.beginPass()
        for job in list(self.jobs.values()):
            try:
                self.check(job)
            except API_ERRORS + (ApiError,) as exc:
                logger.warning("submission %s: %s; will retry", job.id, exc)
        try:
            if time.monotonic() - self.last_heartbeat >= self.config.heartbeat:
                self.heartbeat()
            ready = self.backend.ready() if hasattr(self.backend, "ready") else True
            if not ready:  # e.g. slurmrestd unreachable: claim nothing until it is back
                self.last_error = self.backend.problem
            while ready and not self.stopping and len(self.jobs) < self.config.slots:
                if not self.claim():
                    break
        except API_ERRORS + (ApiError,) as exc:
            logger.warning("web API: %s", exc)

    def heartbeat(self):
        summary = self.backend.summary()
        summary.update(running=len(self.jobs), last_error=self.last_error)
        reply = self.api.call("POST", "/api/agent/heartbeat", {
            "host": socket.gethostname(), "version": __version__,
            "capabilities": {"backend": self.backend.name, "slots": self.config.slots},
            "summary": summary, "active": sorted(self.jobs)})
        self.last_heartbeat = time.monotonic()
        for submissionId in reply.get("cancel", []):
            job = self.jobs.get(submissionId)
            if job is not None and not job.cancelled:
                logger.info("submission %s: cancelling", submissionId)
                job.cancelled = True
                self.backend.cancel(job)

    def claim(self):
        """Claim and start work: one submission, or (for a backend that starts batches, i.e.
        Slurm) all of a sweep's queued runs as one array job. False when the queue is empty"""
        if hasattr(self.backend, "startBatch"):
            limit = max(1, self.config.slots - len(self.jobs))
            subs = self.api.call("POST", "/api/agent/claim-batch", {"limit": limit})["submissions"]
        else:
            sub = self.api.call("POST", "/api/agent/claim")["submission"]
            subs = [sub] if sub else []
        if not subs:
            return False
        logger.info("claimed %s", ", ".join("{0} ({1})".format(s["submission_id"], s["name"]) for s in subs))
        try:
            for sub in subs:
                self.fetchInputs(sub["recipe"])
            jobs = self.backend.startBatch(subs) if len(subs) > 1 else [self.backend.start(subs[0])]
        except Exception as exc:
            logger.exception("could not start %s", [s["submission_id"] for s in subs])
            self.last_error = "submissions {0}: {1}".format([s["submission_id"] for s in subs], exc)
            for sub in subs:
                self.report(sub["submission_id"], "failed", error="Could not start: {0}".format(exc))
            return True
        for job in jobs:
            self.jobs[job.id] = job
            try:
                self.report(job.id, job.state, external_id=job.external_id)
            except ApiError as exc:
                if exc.status != 409:
                    raise
                logger.info("submission %s: cancelled while starting", job.id)  # state already final
                job.cancelled = True
                self.backend.cancel(job)
                self.backend.forget(job)
                del self.jobs[job.id]
        return True

    def fetchInputs(self, recipe):
        os.makedirs(self.backend.blobs, exist_ok=True)
        for digest in ab_recipe.references(recipe):
            dest = os.path.join(self.backend.blobs, digest)
            if not os.path.isfile(dest):
                self.api.download("/api/blobs/" + digest, dest, digest)

    # --- a running submission

    def check(self, job):
        state, error, final = self.backend.poll(job)
        if not final:
            if state != job.state:
                if not job.cancelled:  # a cancelling submission only moves to its end
                    self.report(job.id, state, external_id=job.external_id)
                job.state = state
            if state == "running" and getattr(self.backend, "uploadsLive", True) and \
                    self.config.upload and self.config.upload_every and \
                    time.monotonic() - job.last_upload >= self.config.upload_every:
                self.upload(job, final=False)
            return
        uploaded = self.upload(job, final=True) if self.backend.uploadsAtEnd else self.backend.uploaded(job)
        if not uploaded and state == "finished":
            state, error = "failed", "The run finished but was not uploaded (see the agent's log)"
        self.report(job.id, state, error=error)  # raises (and is retried next pass) if the API is down
        del self.jobs[job.id]
        logger.info("submission %s: %s", job.id, state)
        self.backend.forget(job, removeRun=uploaded and not self.config.keep_runs)

    def upload(self, job, final):
        """Upload the run directory; True on success"""
        job.last_upload = time.monotonic()
        if not self.config.upload or not os.path.isfile(os.path.join(job.rundir, "run.json")):
            return False
        command = [self.config.upload] + (["--finalise"] if final else []) + [job.rundir]
        try:
            result = subprocess.run(command, capture_output=True, text=True, env=self.config.uploadEnvironment(),
                                    timeout=600)
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("submission %s: upload failed: %s", job.id, exc)
            return False
        if result.returncode != 0:
            self.last_error = "upload of submission {0} failed".format(job.id)
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
