"""The slurmrest backend: the Slurm backend's jobs, submitted through slurmrestd.

The agent can run on any machine that reaches slurmrestd (a container in the server room,
say): it needs no Slurm commands, munge key or login node, and never touches the
cluster's filesystems. Instead:

- each job's script is one of deploy/slurm's job scripts, with its recipe and input files
  written into it (base64, checked by sha256), which it stages under AMBUILD_RUNS_ROOT on
  the cluster before building;
- the build job uploads the run every AMBUILD_AGENT_UPLOAD_EVERY seconds while it runs
  (AMBUILD_LIVE_UPLOAD_EVERY), so its page is live, and the upload job afterwards, as for
  the slurm backend; both read the upload settings on the cluster;
- how a run ended comes from the web API (the uploaded run's status and error).

slurmrestd authenticates each request by a JWT for the cluster user whose jobs these are
(X-SLURM-USER-NAME, X-SLURM-USER-TOKEN): from AMBUILD_SLURM_JWT_FILE, read again for every
request so it can be replaced while the agent runs, or AMBUILD_SLURM_JWT. Tested against
Slurm 24.11's API versions v0.0.40 to v0.0.42.
"""
import base64
import hashlib
import json
import logging
import math
import os
import posixpath
import re
import shlex
import ssl
import urllib.error
import urllib.request

from ambuild import recipe as ab_recipe

from ambuild_agent import __version__
from ambuild_agent.agent import Job
from ambuild_agent.backends import GONE, SlurmJobs, taskIds

logger = logging.getLogger("ambuild_agent")

# Newest first: the version used unless AMBUILD_SLURMRESTD_VERSION says otherwise
TESTED_VERSIONS = ("v0.0.42", "v0.0.41", "v0.0.40")
# Slurm's default limit on a batch script (SchedulerParameters=max_script_size) is 4 MB
MAX_SCRIPT_BYTES = 4 * 1024 * 1024 - 64 * 1024
DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"
PREAMBLE = """\
# --- added by ambuild-agent (slurmrest). Jobs submitted through slurmrestd start with
# only the environment the agent sends: find the user's home.
export USER="${USER:-$(id -un)}"
export HOME="${HOME:-$(getent passwd "$USER" | cut -d: -f6)}"
"""
STAGE_FUNCTION = """\
ambuild_stage() {  # FILE SHA256, the file's base64 on stdin: written unless it exists already
    [ -s "$1" ] && return 0
    mkdir -p "$(dirname "$1")"
    local part="$1.part.${SLURM_JOB_ID:-$$}"
    base64 -d > "$part"
    if ! echo "$2  $part" | sha256sum -c --status; then
        echo "ambuild-agent: $1 does not match its sha256" >&2
        rm -f "$part"
        return 1
    fi
    mv -f "$part" "$1"
}
"""


class SlurmRestError(RuntimeError):
    pass


def minutes(text):
    """A Slurm time limit (MM, MM:SS, HH:MM:SS, D-HH, D-HH:MM, D-HH:MM:SS) in whole minutes"""
    text = str(text).strip()
    days, _, rest = text.rpartition("-") if "-" in text else ("", "", text)
    parts = [int(p) for p in rest.split(":")]
    if days:
        hours, mins, secs = (parts + [0, 0])[:3]
        total = int(days) * 1440 + hours * 60 + mins + secs / 60
    elif len(parts) == 3:
        total = parts[0] * 60 + parts[1] + parts[2] / 60
    elif len(parts) == 2:
        total = parts[0] + parts[1] / 60
    else:
        total = parts[0]
    return max(1, math.ceil(total))


def sbatchDirectives(script):
    """{option: value} of a job script's #SBATCH --option=value lines (slurmrestd ignores
    them: they have to be sent as job fields)"""
    found = {}
    for line in script.splitlines():
        match = re.match(r"#SBATCH\s+--([\w-]+)(?:[= ](\S+))?", line)
        if match:
            found[match.group(1)] = match.group(2)
    return found


def stagedScript(template, files, setup=""):
    """A job script: template's header (shebang, comments, #SBATCH lines), then the
    preamble, the setup line, and each of files ({path: bytes}) staged, then the rest of
    template"""
    lines = template.splitlines()
    split = 0
    while split < len(lines) and (lines[split].startswith("#") or not lines[split].strip()):
        split += 1
    parts = ["\n".join(lines[:split]), PREAMBLE]
    if setup:
        parts.append(setup)
    if files:
        parts.append(STAGE_FUNCTION)
        for path, content in files.items():
            parts.append("ambuild_stage {0} {1} <<'AMBUILD_STAGED' || exit 1\n{2}AMBUILD_STAGED".format(
                shlex.quote(path), hashlib.sha256(content).hexdigest(), base64.encodebytes(content).decode()))
    parts.append("# --- the job script\n" + "\n".join(lines[split:]))
    return "\n".join(parts) + "\n"


class SlurmRest:
    """A slurmrestd client"""

    def __init__(self, config, timeout=60):
        self.url = config.slurmrestd_url.rstrip("/")
        self.user = config.slurm_user
        self.jwt = config.slurm_jwt
        self.jwtFile = config.slurm_jwt_file
        self.timeout = timeout
        self.context = ssl.create_default_context(cafile=config.slurm_ca or None) if self.url.startswith("https") \
            else None

    def token(self):
        if self.jwtFile:
            try:
                with open(self.jwtFile) as f:
                    text = f.read().strip()
            except OSError as exc:
                raise SlurmRestError("Cannot read AMBUILD_SLURM_JWT_FILE: {0}".format(exc))
            # as scontrol token prints it, or the bare token
            return text.split("=", 1)[1] if text.startswith("SLURM_JWT=") else text
        return self.jwt

    def call(self, method, path, body=None, missingOk=False):
        headers = {"X-SLURM-USER-TOKEN": self.token(), "Accept": "application/json",
                   "User-Agent": "ambuild-agent/" + __version__}
        if self.user:
            headers["X-SLURM-USER-NAME"] = self.user
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=self.context) as response:
                reply = json.loads(response.read().decode() or "null") or {}
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            if exc.code in (401, 403):
                raise SlurmRestError("slurmrestd rejected the token for user {0!r} (HTTP {1}; expired? Make a new one "
                                     "with scontrol token)".format(self.user or "?", exc.code))
            if exc.code == 404 and missingOk:
                return None
            try:
                reply = json.loads(text)
            except ValueError:
                raise SlurmRestError("slurmrestd: HTTP {0} for {1} {2}: {3}".format(exc.code, method, path, text[:300]))
            raise SlurmRestError("slurmrestd: HTTP {0} for {1} {2}: {3}".format(exc.code, method, path,
                                                                                  self.errors(reply) or text[:300]))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SlurmRestError("slurmrestd at {0} unreachable: {1}".format(self.url, exc))
        return reply

    @staticmethod
    def errors(reply):
        return "; ".join("{0}{1}".format(e.get("error") or e.get("description") or "error",
                                         " ({0})".format(e["description"]) if e.get("description") and e.get("error")
                                         else "") for e in (reply or {}).get("errors") or [])


class SlurmRestBackend(SlurmJobs):
    """Each submission is a build job and an upload job, as for the slurm backend, submitted
    through slurmrestd. Run directories are AMBUILD_RUNS_ROOT/<run id> on the cluster;
    staged recipes are under AMBUILD_RUNS_ROOT/.ambuild-work, input files under
    AMBUILD_RUNS_ROOT/.ambuild-blobs."""
    name = "slurmrest"
    uploadsLive = False  # the build job does it (the agent cannot see the run directory)

    def __init__(self, config):
        missing = [name for name, value in (("AMBUILD_SLURMRESTD_URL", config.slurmrestd_url),
                                            ("AMBUILD_RUNS_ROOT", config.runs_root),
                                            ("AMBUILD_SLURM_DIR", config.slurm_dir)) if not value]
        if not (config.slurm_jwt or config.slurm_jwt_file):
            missing.append("AMBUILD_SLURM_JWT_FILE (or AMBUILD_SLURM_JWT)")
        if missing:
            raise SystemExit("The slurmrest backend needs " + ", ".join(missing))
        if config.sbatch_options:
            logger.warning("AMBUILD_SLURM_OPTIONS is for the slurm backend; slurmrest takes job fields as JSON in "
                           "AMBUILD_SLURMREST_JOB, e.g. {\"account\": \"chem\"}")
        self.config = config
        self.rest = SlurmRest(config)
        self.root = config.runs_root.rstrip("/")  # a path on the cluster, not here
        self.clusterBlobs = posixpath.join(self.root, ".ambuild-blobs")
        self.work = posixpath.join(self.root, ".ambuild-work")
        self.logDir = config.slurm_log_dir or self.root
        self.blobs = os.path.join(os.path.abspath(config.directory), "blobs")  # a cache here, of the web's files
        self.templates = {}
        for name in ("ambuild_build.sbatch", "ambuild_build_array.sbatch", "ambuild_upload.sbatch"):
            with open(os.path.join(config.slurm_dir, name)) as f:
                self.templates[name] = f.read()
        self.version = config.slurmrestd_version or None
        self.api = None
        self.problem = None
        self.beginPass()

    def attach(self, api):
        self.api = api

    def beginPass(self):
        super().beginPass()
        self._runs = None
        self._ready = None

    # --- slurmrestd

    def _path(self, rest):
        return "/slurm/{0}/{1}".format(self.version, rest)

    def ready(self):
        """True when slurmrestd answers and its controller is up (once per pass); else
        self.problem says why"""
        if self._ready is None:
            try:
                if self.version is None:
                    offered = self.rest.call("GET", "/openapi/v3").get("paths") or {}
                    versions = {m.group(1) for p in offered for m in [re.match(r"/slurm/(v[\d.]+)/", p)] if m}
                    usable = [v for v in TESTED_VERSIONS if v in versions]
                    if not usable:
                        raise SlurmRestError("slurmrestd offers API versions {0}, none of the tested {1}; set "
                                             "AMBUILD_SLURMRESTD_VERSION to try one".format(
                                                 sorted(versions), ", ".join(TESTED_VERSIONS)))
                    self.version = usable[0]
                    logger.info("slurmrestd %s: API %s", self.rest.url, self.version)
                pings = self.rest.call("GET", self._path("ping")).get("pings") or []
                if not any(str(p.get("pinged", "")).upper() in ("UP", "RESPONDING") for p in pings):
                    raise SlurmRestError("slurmrestd answers, but no Slurm controller is up: {0}".format(pings))
                self._ready, self.problem = True, None
            except SlurmRestError as exc:
                if self.problem != str(exc):
                    logger.warning("%s", exc)
                self._ready, self.problem = False, str(exc)
        return self._ready

    def _submitJob(self, job):
        reply = self.rest.call("POST", self._path("job/submit"), {"job": job})
        jobId = reply.get("job_id") or (reply.get("result") or {}).get("job_id")
        if not jobId:
            raise SlurmRestError("slurmrestd did not submit {0}: {1}".format(job.get("name"),
                                                                             self.rest.errors(reply) or reply))
        for warning in reply.get("warnings") or []:
            logger.info("slurmrestd: %s", warning.get("description") or warning)
        return str(jobId)

    # --- jobs

    def _paths(self, runId):
        return posixpath.join(self.root, runId), posixpath.join(self.work, runId)

    def _environment(self, extra):
        env = {"PATH": DEFAULT_PATH}
        env.update({k: str(v) for k, v in self.config.slurm_env.items()})
        env.update({"AMBUILD_RUNS_ROOT": self.root, "AMBUILD_BLOBS": self.clusterBlobs})
        if self.config.upload and self.config.upload_every:
            env["AMBUILD_LIVE_UPLOAD_EVERY"] = str(int(self.config.upload_every))
        if self.config.slurm_upload_env:
            env["AMBUILD_UPLOAD_ENV"] = self.config.slurm_upload_env
        env.update(extra)
        return ["{0}={1}".format(k, v) for k, v in sorted(env.items())]

    def _jobFields(self, template, name, output, env, resources=None):
        directives = sbatchDirectives(self.templates[template])
        job = {"name": name, "current_working_directory": self.logDir,
               "standard_output": posixpath.join(self.logDir, output), "environment": env}
        if directives.get("ntasks"):
            job["tasks"] = int(directives["ntasks"])
        if directives.get("time"):
            job["time_limit"] = {"set": True, "number": minutes(directives["time"])}
        r = resources or {}
        if r.get("cpus"):
            job["cpus_per_task"] = int(r["cpus"])
        if r.get("gpus"):
            job["tres_per_job"] = "gres/gpu:{0}".format(int(r["gpus"]))
        if r.get("memory_mb"):
            job["memory_per_node"] = {"set": True, "number": int(r["memory_mb"])}
        if r.get("time"):
            job["time_limit"] = {"set": True, "number": minutes(r["time"])}
        if self.config.partition:
            job["partition"] = self.config.partition
        job.update(self.config.slurm_job)
        return job

    def _inputs(self, subs):
        """{cluster path: bytes} of the submissions' input files (from the local cache)"""
        files = {}
        for sub in subs:
            for digest in ab_recipe.references(sub["recipe"]):
                with open(os.path.join(self.blobs, digest), "rb") as f:
                    files[posixpath.join(self.clusterBlobs, digest)] = f.read()
        return files

    def _script(self, template, files):
        script = stagedScript(self.templates[template], files, self.config.slurm_setup)
        if len(script.encode()) > MAX_SCRIPT_BYTES:
            raise SlurmRestError("the {0} script with its staged inputs is {1:.1f} MB, over Slurm's usual 4 MB limit "
                                 "(fewer runs per sweep batch: AMBUILD_AGENT_SLOTS)".format(
                                     template, len(script.encode()) / 1e6))
        return script

    def start(self, sub):
        return self.startBatch([sub])[0]

    def startBatch(self, subs):
        """Submit one submission as a build job, or several (a sweep's runs) as one array
        job; then their upload job"""
        files = self._inputs(subs)
        recipes = {}
        for sub in subs:
            recipes[sub["run_id"]] = posixpath.join(self._paths(sub["run_id"])[1], "recipe.json")
            files[recipes[sub["run_id"]]] = json.dumps(sub["recipe"], indent=2).encode()
        first = subs[0]
        if len(subs) == 1:
            rundir = self._paths(first["run_id"])[0]
            extra = {"AMBUILD_RUN_ID": first["run_id"], "AMBUILD_RUN_DIR": rundir,
                     "AMBUILD_RECIPE": recipes[first["run_id"]]}
            if first.get("seed") is not None:
                extra["AMBUILD_SEED"] = str(first["seed"])
            template = "ambuild_build.sbatch"
            job = self._jobFields(template, "ambuild-{0}".format(first["submission_id"]), "%x-%j.out",
                                  self._environment(extra), first.get("resources"))
            uploadExtra = {"AMBUILD_RUN_DIR": rundir}
            uploadFiles = {}
        else:
            tasks = posixpath.join(self.work, "array-{0}.tasks".format(first["run_id"]))
            runList = posixpath.join(self.work, "array-{0}.rundirs".format(first["run_id"]))
            files[tasks] = "".join("{0} {1}{2}\n".format(
                s["run_id"], recipes[s["run_id"]], "" if s.get("seed") is None else " {0}".format(s["seed"]))
                for s in subs).encode()
            template = "ambuild_build_array.sbatch"
            job = self._jobFields(template, "ambuild-sweep-{0}".format(first.get("sweep_id") or first["submission_id"]),
                                  "%x-%A_%a.out", self._environment({"AMBUILD_TASKS": tasks}), first.get("resources"))
            job["array"] = "0-{0}%{1}".format(len(subs) - 1, self.config.array_max)
            uploadExtra = {"AMBUILD_RUN_LIST": runList}
            uploadFiles = {runList: "".join(self._paths(s["run_id"])[0] + "\n" for s in subs).encode()}
        job["script"] = self._script(template, files)
        build = self._submitJob(job)
        try:
            upload = self._jobFields("ambuild_upload.sbatch", "ambuild-upload", "%x-%j.out",
                                     self._environment(uploadExtra))
            upload["dependency"] = "afterany:{0}".format(build)
            upload["script"] = self._script("ambuild_upload.sbatch", uploadFiles)
            upload = self._submitJob(upload)
        except Exception:
            self.rest.call("DELETE", self._path("job/{0}".format(build)), missingOk=True)
            raise
        jobs = []
        for i, sub in enumerate(subs):
            rundir, workdir = self._paths(sub["run_id"])
            task = build if len(subs) == 1 else "{0}_{1}".format(build, i)
            jobs.append(Job(submission=sub, rundir=rundir, workdir=workdir, handle={"build": task, "upload": upload},
                            external_id="slurm:{0}/{1}".format(task, upload), state="submitted"))
        logger.info("submissions %s: %s job %s, upload job %s", ", ".join(str(s["submission_id"]) for s in subs),
                    "build" if len(subs) == 1 else "array", build, upload)
        return jobs

    def _jobStates(self, baseId):
        """{job id: state} of a job, or of every task of an array job (ARRAY_TASK); {} once
        slurmctld has forgotten it"""
        if baseId not in self._ended:
            try:
                reply = self.rest.call("GET", self._path("job/{0}".format(baseId)), missingOk=True)
            except SlurmRestError as exc:
                raise RuntimeError(str(exc))
            states = {}
            for record in (reply or {}).get("jobs") or []:
                state = record.get("job_state")
                state = (state[0] if isinstance(state, list) and state else state) or GONE
                arrayJob = (record.get("array_job_id") or {}).get("number") or 0
                task = record.get("array_task_id") or {}
                if arrayJob and task.get("set"):
                    states["{0}_{1}".format(arrayJob, task.get("number"))] = state
                elif arrayJob and record.get("array_task_string"):
                    for number in taskIds(record["array_task_string"]):
                        states["{0}_{1}".format(arrayJob, number)] = state
                else:
                    states[str(record.get("job_id"))] = state
            self._ended[baseId] = states
        return self._ended[baseId]

    def states(self, jobIds):
        return {jobId: self._jobStates(jobId.split("_")[0]).get(jobId, GONE) for jobId in jobIds}

    def outcome(self, job, fallbackError):
        """How the run ended, from its upload (the web API), since the run directory is on
        the cluster"""
        if self._runs is None:
            subs = self.api.call("GET", "/api/agent/submissions")["submissions"]
            self._runs = {s["submission_id"]: (s.get("run_status"), s.get("run_error")) for s in subs}
        status, error = self._runs.get(job.id, (None, None))
        job.handle["run_status"] = status
        if job.cancelled:
            return "cancelled", None
        if status == "finished":
            return "finished", None
        if status:
            return "failed", error or "The run was uploaded as {0}".format(status)
        return "failed", "{0}, and its run was not uploaded (see the upload job's output in {1})".format(
            fallbackError, self.logDir)

    def uploaded(self, job):
        return job.handle.get("run_status") is not None

    def cancel(self, job):
        try:
            self.rest.call("DELETE", self._path("job/{0}".format(job.handle["build"])), missingOk=True)
        except SlurmRestError as exc:
            logger.warning("submission %s: could not cancel: %s", job.id, exc)

    def forget(self, job, removeRun=False):
        pass  # nothing here; run directories stay on the cluster, as for scripted builds

    def summary(self):
        """Partitions and their nodes by state, from slurmrestd"""
        if not self.ready():
            return {"problem": self.problem}
        try:
            nodes = self.rest.call("GET", self._path("nodes")).get("nodes") or []
        except SlurmRestError as exc:
            return {"problem": str(exc)}
        partitions = {}
        for node in nodes:
            state = (node.get("state") or ["UNKNOWN"])[0].lower()  # as sinfo shows it
            for name in node.get("partitions") or []:
                entry = partitions.setdefault(name, {"default": False, "available": "up", "nodes": {}})
                entry["nodes"][state] = entry["nodes"].get(state, 0) + 1
        return {"partitions": partitions, "slurmrestd": self.rest.url, "api": self.version}
