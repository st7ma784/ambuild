"""Where submissions run: here as processes (local), or on a Slurm cluster, submitted
from a login node with sbatch (slurm) or through slurmrestd with a JWT (slurmrest).

A backend starts a submission as a Job, polls it for (state, error, final), cancels it,
and takes it up again after the agent restarts (resume) when it can.
"""
import getpass
import logging
import os
import re
import shlex
import shutil
import socket
import subprocess
import time

from ambuild_agent.agent import Job, buildEnvironment, runOutcome, writeRecipe

logger = logging.getLogger("ambuild_agent")


def make(config):
    if config.backend == "local":
        return LocalBackend(config)
    if config.backend == "slurm":
        return SlurmBackend(config)
    if config.backend == "slurmrest":
        from ambuild_agent.slurmrest import SlurmRestBackend

        return SlurmRestBackend(config)
    raise SystemExit("AMBUILD_AGENT_BACKEND must be local, slurm or slurmrest, not {0!r}".format(config.backend))


def _tail(path, lines=8):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 4000))
            return " | ".join(f.read().decode("utf-8", "replace").strip().splitlines()[-lines:])
    except OSError:
        return "(no log)"


class LocalBackend:
    """Each submission is a process here: python -m ambuild.recipe run"""
    name = "local"
    uploadsAtEnd = True
    terminateGrace = 30  # seconds between SIGTERM and SIGKILL

    def __init__(self, config):
        self.config = config
        self.directory = os.path.abspath(config.directory)
        self.blobs = os.path.join(self.directory, "blobs")
        for sub in ("blobs", "runs", "work"):
            os.makedirs(os.path.join(self.directory, sub), exist_ok=True)
        self.stopped = False

    def start(self, sub):
        runId = sub["run_id"]
        workdir = os.path.join(self.directory, "work", runId)
        rundir = os.path.join(self.directory, "runs", runId)
        recipeFile = writeRecipe(sub, workdir)
        command = [self.config.python, "-m", "ambuild.recipe", "run", recipeFile, "--output", rundir,
                   "--blobs", self.blobs, "--run-id", runId]
        if sub.get("seed") is not None:
            command += ["--seed", str(sub["seed"])]
        log = open(os.path.join(workdir, "runner.log"), "ab")
        process = subprocess.Popen(command, cwd=workdir, stdout=log, stderr=subprocess.STDOUT,
                                   env=buildEnvironment(), start_new_session=True)
        logger.info("submission %s: started, pid %s", sub["submission_id"], process.pid)
        return Job(submission=sub, rundir=rundir, workdir=workdir, handle={"process": process, "log": log},
                   external_id="{0}:{1}".format(socket.gethostname(), process.pid), state="running")

    def poll(self, job):
        process = job.handle["process"]
        code = process.poll()
        if code is None:
            stopAt = job.handle.get("terminated_at")
            if stopAt is not None and time.monotonic() - stopAt > self.terminateGrace:
                process.kill()
            return "running", None, False
        job.handle["log"].close()
        if self.stopped and not job.cancelled:
            return "failed", "The agent stopped", True
        state, error = runOutcome(job, "The runner exited with code {0}: {1}".format(
            code, _tail(os.path.join(job.workdir, "runner.log"))))
        return state, error, True

    def cancel(self, job):
        process = job.handle["process"]
        if process.poll() is None and "terminated_at" not in job.handle:
            process.terminate()  # the runner records the run as failed ("Cancelled")
            job.handle["terminated_at"] = time.monotonic()

    def stopping(self, jobs):
        self.stopped = True
        for job in jobs.values():
            self.cancel(job)

    def waitsOnStop(self, jobs):
        return bool(jobs)  # until the stopped runs are uploaded and reported

    def resume(self, sub):
        return None  # its process ended with the agent's; the heartbeat reports it lost

    def uploaded(self, job):
        return False

    def forget(self, job, removeRun=False):
        if job.handle:
            job.handle["log"].close()
        if removeRun:
            shutil.rmtree(job.rundir, ignore_errors=True)
            shutil.rmtree(job.workdir, ignore_errors=True)

    def summary(self):
        return {"cpus": os.cpu_count(), "load": os.getloadavg() if hasattr(os, "getloadavg") else None,
                "disk_free_gb": round(shutil.disk_usage(self.directory).free / 1e9, 1)}


# Slurm job states (squeue %T, scontrol JobState, sacct State)
PENDING = {"PENDING", "CONFIGURING", "REQUEUED", "REQUEUE_HOLD", "REQUEUE_FED", "RESIZING"}
RUNNING = {"RUNNING", "COMPLETING", "SUSPENDED", "STAGE_OUT", "SIGNALING"}
GONE = "GONE"  # no longer known to Slurm (and no accounting to ask)


def taskIds(text):
    """Array task ids from Slurm's notation, e.g. "0-3,7%50" """
    ids = []
    for part in text.split("%")[0].split(","):
        low, _, high = part.partition("-")
        if low.isdigit():
            ids.extend(range(int(low), int(high or low) + 1))
    return ids


class SlurmJobs:
    """What the Slurm backends share: each submission is a build job (or an array job's
    task) followed afterany by an upload job, with external id slurm:<build>/<upload>.
    A backend gives states(job ids) -> {job id: state}, and _paths(run id)."""
    uploadsAtEnd = False  # the upload job does it

    def beginPass(self):
        """Forget the job states of the last pass (they are looked up once per pass)"""
        self._queue = None
        self._ended = {}

    def poll(self, job):
        build, upload = job.handle["build"], job.handle["upload"]
        try:
            states = self.states([build, upload])
        except RuntimeError as exc:
            logger.warning("submission %s: %s", job.id, exc)
            return job.state or "submitted", None, False
        built, uploading = states[build], states[upload]
        if built in PENDING:
            return "submitted", None, False
        if built in RUNNING:
            return "running", None, False
        if uploading in PENDING or uploading in RUNNING:  # built; waiting for the upload
            return job.state or "running", None, False
        job.handle.update(build_state=built, upload_state=uploading)
        state, error = self.outcome(job, "The Slurm build job ended {0}".format(built))
        if state == "failed" and built in ("TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED", "DEADLINE"):
            error = "{0} (Slurm: {1})".format(error, built)
        return state, error, True

    def outcome(self, job, fallbackError):
        return runOutcome(job, fallbackError)

    def uploaded(self, job):
        return job.handle.get("upload_state") in ("COMPLETED", GONE)

    def stopping(self, jobs):
        pass  # Slurm jobs carry on; a restarted agent takes them up again

    def waitsOnStop(self, jobs):
        return False

    def resume(self, sub):
        match = re.fullmatch(r"slurm:(\d+(?:_\d+)?)/(\d+)", sub.get("external_id") or "")
        if not match:
            return None
        rundir, workdir = self._paths(sub["run_id"])
        return Job(submission=sub, rundir=rundir, workdir=workdir,
                   handle={"build": match.group(1), "upload": match.group(2)}, external_id=sub["external_id"],
                   state=sub["state"] if sub["state"] in ("submitted", "running") else "running")


class SlurmBackend(SlurmJobs):
    """Each submission is a build job and an upload job, submitted with submit_build.sh
    (deploy/slurm) from this login node. The recipe and its inputs are staged on the
    shared filesystem, under AMBUILD_RUNS_ROOT; the run directory is AMBUILD_RUNS_ROOT/<run id>."""
    name = "slurm"

    def __init__(self, config):
        if not config.runs_root or not config.slurm_dir:
            raise SystemExit("The slurm backend needs AMBUILD_RUNS_ROOT (shared by all nodes) and AMBUILD_SLURM_DIR "
                             "(deploy/slurm)")
        self.config = config
        self.root = os.path.abspath(config.runs_root)
        self.blobs = os.path.join(self.root, ".ambuild-blobs")
        self.work = os.path.join(self.root, ".ambuild-work")
        self.submitScript = os.path.join(config.slurm_dir, "submit_build.sh")
        self.user = getpass.getuser()
        self.beginPass()

    def _paths(self, runId):
        return os.path.join(self.root, runId), os.path.join(self.work, runId)

    def sbatchOptions(self, sub):
        r = sub.get("resources") or {}
        options = ["--job-name=ambuild-{0}".format(sub["submission_id"])]
        if r.get("cpus"):
            options.append("--cpus-per-task={0}".format(r["cpus"]))
        if r.get("gpus"):
            options.append("--gpus={0}".format(r["gpus"]))
        if r.get("memory_mb"):
            options.append("--mem={0}M".format(r["memory_mb"]))
        if r.get("time"):
            options.append("--time={0}".format(r["time"]))
        if self.config.partition:
            options.append("--partition={0}".format(self.config.partition))
        return options + shlex.split(self.config.sbatch_options)

    def start(self, sub):
        runId = sub["run_id"]
        rundir, workdir = self._paths(runId)
        recipeFile = writeRecipe(sub, workdir)
        env = buildEnvironment({"AMBUILD_RUNS_ROOT": self.root, "AMBUILD_RUN_ID": runId, "AMBUILD_BLOBS": self.blobs})
        env.pop("AMBUILD_SEED", None)
        if sub.get("seed") is not None:
            env["AMBUILD_SEED"] = str(sub["seed"])
        result = subprocess.run([self.submitScript, "--recipe", recipeFile] + self.sbatchOptions(sub), cwd=workdir,
                                env=env, capture_output=True, text=True, timeout=120)
        match = re.search(r"build job (\d+), upload job (\d+)", result.stdout)
        if result.returncode != 0 or not match:
            raise RuntimeError("submit_build.sh failed: " + (result.stderr or result.stdout).strip()[-1000:])
        build, upload = match.groups()
        logger.info("submission %s: build job %s, upload job %s", sub["submission_id"], build, upload)
        return Job(submission=sub, rundir=rundir, workdir=workdir, handle={"build": build, "upload": upload},
                   external_id="slurm:{0}/{1}".format(build, upload), state="submitted")

    def _run(self, command):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("%s: %s", command[0], exc)
            return None
        return result.stdout if result.returncode == 0 else None

    def startBatch(self, subs):
        """Submit several submissions (a sweep's runs) as one array job; one alone is
        submitted as a plain build"""
        if len(subs) == 1:
            return [self.start(subs[0])]
        os.makedirs(self.work, exist_ok=True)
        lines = []
        for sub in subs:
            recipeFile = writeRecipe(sub, self._paths(sub["run_id"])[1])
            lines.append("{0} {1}{2}".format(sub["run_id"], recipeFile,
                                             "" if sub.get("seed") is None else " {0}".format(sub["seed"])))
        tasks = os.path.join(self.work, "array-{0}.tasks".format(subs[0]["run_id"]))
        with open(tasks, "w") as f:
            f.write("\n".join(lines) + "\n")
        env = buildEnvironment({"AMBUILD_RUNS_ROOT": self.root, "AMBUILD_BLOBS": self.blobs})
        options = self.sbatchOptions(subs[0])
        options[0] = "--job-name=ambuild-sweep-{0}".format(subs[0].get("sweep_id") or subs[0]["submission_id"])
        result = subprocess.run([os.path.join(self.config.slurm_dir, "submit_array.sh"), tasks] + options,
                                cwd=self.work, env=env, capture_output=True, text=True, timeout=120)
        match = re.search(r"array job (\d+) \((\d+) tasks\), upload job (\d+)", result.stdout)
        if result.returncode != 0 or not match or int(match.group(2)) != len(subs):
            raise RuntimeError("submit_array.sh failed: " + (result.stderr or result.stdout).strip()[-1000:])
        array, _, upload = match.groups()
        logger.info("submissions %s-%s: array job %s, upload job %s", subs[0]["submission_id"],
                    subs[-1]["submission_id"], array, upload)
        jobs = []
        for i, sub in enumerate(subs):
            rundir, workdir = self._paths(sub["run_id"])
            task = "{0}_{1}".format(array, i)
            jobs.append(Job(submission=sub, rundir=rundir, workdir=workdir, handle={"build": task, "upload": upload},
                            external_id="slurm:{0}/{1}".format(task, upload), state="submitted"))
        return jobs

    _taskIds = staticmethod(taskIds)

    def _queued(self):
        """{job id: state} of this user's jobs in squeue (array tasks as ARRAY_TASK)"""
        if getattr(self, "_queue", None) is None:
            out = self._run(["squeue", "-h", "-o", "%i %T", "-u", self.user])
            if out is None:
                raise RuntimeError("squeue failed")
            found = {}
            for line in out.splitlines():
                if " " not in line:
                    continue
                jobId, state = line.split(None, 1)
                pending = re.fullmatch(r"(\d+)_\[(.+)\]", jobId)
                if pending:
                    for task in self._taskIds(pending.group(2)):
                        found["{0}_{1}".format(pending.group(1), task)] = state.strip()
                else:
                    found[jobId] = state.strip()
            self._queue = found
        return self._queue

    def _endedStates(self, baseId):
        """{job id: state} of a job (or every task of an array job) that squeue no longer
        shows: from scontrol (recently ended jobs), else sacct (accounting)"""
        cache = getattr(self, "_ended", None)
        if cache is None:
            cache = self._ended = {}
        if baseId not in cache:
            states = {}
            for line in (self._run(["scontrol", "show", "job", "-o", baseId]) or "").splitlines():
                fields = dict(re.findall(r"(\w+)=(\S+)", line))
                if "JobState" not in fields:
                    continue
                if "ArrayJobId" in fields and "ArrayTaskId" in fields:
                    for task in self._taskIds(fields["ArrayTaskId"]):
                        states["{0}_{1}".format(fields["ArrayJobId"], task)] = fields["JobState"]
                else:
                    states[fields.get("JobId", baseId)] = fields["JobState"]
            if not states:
                for line in (self._run(["sacct", "-n", "-X", "-P", "-o", "JobID,State", "-j", baseId]) or "").splitlines():
                    if "|" in line:
                        jobId, state = line.split("|", 1)
                        states[jobId] = (state.split() or [GONE])[0]
            cache[baseId] = states
        return cache[baseId]

    def states(self, jobIds):
        """{job id: state} from squeue, else scontrol, else sacct; job ids may be array
        tasks (ARRAY_TASK)"""
        queued = self._queued()
        states = {}
        for jobId in jobIds:
            state = queued.get(jobId)
            if not state:
                state = self._endedStates(jobId.split("_")[0]).get(jobId, GONE)
            states[jobId] = state
        return states

    def cancel(self, job):
        self._run(["scancel", job.handle["build"]])  # the upload job still records the run

    def forget(self, job, removeRun=False):
        shutil.rmtree(job.workdir, ignore_errors=True)  # run directories stay, as for scripted builds

    def summary(self):
        """Partitions and their nodes by state, from sinfo"""
        partitions = {}
        for line in (self._run(["sinfo", "-h", "-o", "%P|%a|%D|%T"]) or "").splitlines():
            parts = line.split("|")
            if len(parts) != 4:
                continue
            name, available, count, state = parts
            entry = partitions.setdefault(name.rstrip("*"), {"default": name.endswith("*"), "available": available,
                                                             "nodes": {}})
            state = state.rstrip("*~#!%$@^-")
            entry["nodes"][state] = entry["nodes"].get(state, 0) + int(count or 0)
        return {"partitions": partitions}
