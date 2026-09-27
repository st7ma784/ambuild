"""Where submissions run: here as processes (local) or on a Slurm cluster (slurm).

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
    raise SystemExit("AMBUILD_AGENT_BACKEND must be local or slurm, not {0!r}".format(config.backend))


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


class SlurmBackend:
    """Each submission is a build job and an upload job, submitted with submit_build.sh
    (deploy/slurm) from this login node. The recipe and its inputs are staged on the
    shared filesystem, under AMBUILD_RUNS_ROOT; the run directory is AMBUILD_RUNS_ROOT/<run id>."""
    name = "slurm"
    uploadsAtEnd = False  # the upload job does it

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

    def states(self, jobIds):
        """{job id: state} from squeue, else scontrol (recently ended jobs), else sacct"""
        queue = self._run(["squeue", "-h", "-o", "%i %T", "-u", self.user])
        if queue is None:
            raise RuntimeError("squeue failed")
        found = dict(line.split(None, 1) for line in queue.splitlines() if " " in line)
        states = {}
        for jobId in jobIds:
            state = found.get(jobId, "").strip()
            if not state:
                shown = self._run(["scontrol", "show", "job", "-o", jobId]) or ""
                match = re.search(r"JobState=(\S+)", shown)
                if match:
                    state = match.group(1)
                else:
                    acct = (self._run(["sacct", "-n", "-X", "-P", "-o", "State", "-j", jobId]) or "").split()
                    state = acct[0] if acct else GONE
            states[jobId] = state
        return states

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
        state, error = runOutcome(job, "The Slurm build job ended {0}".format(built))
        if state == "failed" and built in ("TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED", "DEADLINE"):
            error = "{0} (Slurm: {1})".format(error, built)
        return state, error, True

    def uploaded(self, job):
        return job.handle.get("upload_state") in ("COMPLETED", GONE)

    def cancel(self, job):
        self._run(["scancel", job.handle["build"]])  # the upload job still records the run

    def stopping(self, jobs):
        pass  # Slurm jobs carry on; a restarted agent takes them up again

    def waitsOnStop(self, jobs):
        return False

    def resume(self, sub):
        match = re.fullmatch(r"slurm:(\d+)/(\d+)", sub.get("external_id") or "")
        if not match:
            return None
        rundir, workdir = self._paths(sub["run_id"])
        return Job(submission=sub, rundir=rundir, workdir=workdir,
                   handle={"build": match.group(1), "upload": match.group(2)}, external_id=sub["external_id"],
                   state=sub["state"] if sub["state"] in ("submitted", "running") else "running")

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
