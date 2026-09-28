"""Check the slurmrest agent end to end, for run_test.sh (after check_agent.py):

The agent runs as "nobody", which cannot read the runs directory (/shared/rest-runs, the
cluster user ambuild's, mode 700), and has no Slurm commands to use: everything goes
through slurmrestd with a JWT for ambuild (AMBUILD_SLURM_JWT_FILE).

1. it comes online, and its status card says it works through slurmrestd;
2. a recipe is submitted as a build job and an upload job; the jobs run as ambuild, stage
   the recipe and its inputs on the cluster, and upload the run while it builds (live)
   and at the end; the agent is killed mid-run and started again, and takes the job up;
3. cancelling a running submission cancels its job, and the run is uploaded as cancelled;
4. a 2x2 sweep runs as one array job;
5. with a rejected token it claims nothing, and its status card says why; with a new
   token in the file (no restart) it carries on.

Only the standard library (and deploy/demo/webapi.py). AMBUILD_API_URL: the web GUI.
"""
import json
import os
import signal
import subprocess
import sys
import time
import uuid

sys.path.insert(0, "/opt/ambuild-demo")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import webapi  # noqa: E402
from check_agent import FINAL, check, runOrNone, submission, waitFor  # noqa: E402

BLOCKS = os.environ.get("AMBUILD_BLOCKS_DIR", "/ambuild/tests/blocks")
ROOT = "/shared/rest-runs"
TOKEN_FILE = "/tmp/rest-agent/slurm.jwt"
LOG = "/tmp/rest-agent.log"
AS_NOBODY = ["setpriv", "--reuid=nobody", "--regid=nogroup", "--clear-groups"]


def writeToken(text=None):
    if text is None:
        text = subprocess.run(["scontrol", "token", "username=ambuild", "lifespan=7200"], capture_output=True,
                              text=True, check=True).stdout.strip()
    with open(TOKEN_FILE, "w") as f:
        f.write(text + "\n")
    os.chmod(TOKEN_FILE, 0o644)  # test only: readable by the agent's user


def startAgent(token):
    env = {"PATH": "/opt/venv/bin:/usr/bin:/bin", "HOME": "/tmp/rest-agent",
           "AMBUILD_API_URL": webapi.API, "AMBUILD_AGENT_TOKEN": token, "AMBUILD_AGENT_BACKEND": "slurmrest",
           "AMBUILD_AGENT_DIR": "/tmp/rest-agent", "AMBUILD_SLURMRESTD_URL": "http://localhost:6820",
           "AMBUILD_SLURM_USER": "ambuild", "AMBUILD_SLURM_JWT_FILE": TOKEN_FILE,
           "AMBUILD_RUNS_ROOT": ROOT, "AMBUILD_SLURM_DIR": "/opt/ambuild-slurm",
           # the jobs' whole environment: through slurmrestd they inherit nothing
           "AMBUILD_SLURMREST_ENV": json.dumps({"PATH": "/opt/venv/bin:/usr/bin:/bin",
                                                "POREBLAZER_EXE": "/opt/poreblazer/poreblazer.exe",
                                                "AMBUILD_PARAMS_DIR": os.environ["AMBUILD_PARAMS_DIR"],
                                                "AMBUILD_BLOCKS_DIR": os.environ["AMBUILD_BLOCKS_DIR"]}),
           "AMBUILD_AGENT_POLL": "2", "AMBUILD_AGENT_HEARTBEAT": "5", "AMBUILD_AGENT_UPLOAD_EVERY": "4"}
    return subprocess.Popen(AS_NOBODY + ["ambuild-agent"], env=env, stdout=open(LOG, "a"), stderr=subprocess.STDOUT)


def card(name):
    for c in webapi.call("GET", "/api/status")["checks"]:
        if c["name"] == "Agent " + name:
            return c
    return None


def main():
    webapi.waitForWeb()
    os.makedirs("/tmp/rest-agent", exist_ok=True)
    subprocess.run(["chown", "nobody:nogroup", "/tmp/rest-agent"], check=True)
    writeToken()
    check(subprocess.run(AS_NOBODY + ["test", "-r", ROOT]).returncode != 0,
          "the agent's user cannot read the runs directory {0}".format(ROOT))

    name = "slurmrest-test-" + uuid.uuid4().hex[:6]
    created = webapi.call("POST", "/api/agents", {"name": name, "backend": "slurm"})
    agent = startAgent(created["token"])
    status = waitFor(lambda: (lambda c: c if c and c["state"] == "ok" else None)(card(name)), 60,
                     "the agent comes online")
    check("slurmrestd http://localhost:6820" in status["facts"].get("via", ""),
          "online, through {0}".format(status["facts"].get("via")))

    # 2. a build: staged by its job, live, surviving an agent restart
    slow = {"op": "poreblazer", "threads": 1, "settings": {"cubelet_size": 0.15}}
    stages = [{"op": "seed", "count": 6},
              {"repeat": 3, "stages": [{"op": "grow", "count": 5}, {"op": "zip", "bond_margin": 1.0}, slow]}]
    body = webapi.demoRecipe(BLOCKS, name="slurmrest e2e " + name, seed=5, stages=stages)
    body["resources"] = {"cpus": 1, "time": "00:20:00"}
    sub = webapi.call("POST", "/api/submissions", {"recipe": body, "backend": "slurm", "owner": "slurmrest test"})
    row = waitFor(lambda: (lambda r: r if r["state"] in ("submitted", "running") + FINAL else None)(
        submission(sub["submission_id"])), 60, "the agent submits it")
    check((row["external_id"] or "").startswith("slurm:"), "submitted through slurmrestd as {0}".format(
        row["external_id"]))
    build = row["external_id"][6:].split("/")[0]
    def liveOrEnded():
        run = runOrNone(sub["run_id"])
        row = submission(sub["submission_id"])
        if (run and run["summary"]["status"] != "running") or row["state"] in FINAL:
            check(False, "the submission ended before its run was seen running (submission {0}: {1}; run: {2})".format(
                row["state"], row["error"], run and (run["summary"]["status"], run["summary"]["error"])))
        return run

    live = waitFor(liveOrEnded, 180, "the build job uploads the running run (live progress)")
    check(live["summary"]["status"] == "running", "the run is in the database while it runs (uploaded by its job)")
    rundir = os.path.join(ROOT, sub["run_id"])
    check(os.stat(rundir).st_uid == 1000, "the job runs as the token's user, ambuild")
    check(os.path.isfile(os.path.join(ROOT, ".ambuild-work", sub["run_id"], "recipe.json")),
          "and staged its recipe on the cluster")

    agent.send_signal(signal.SIGKILL)
    agent.wait()
    agent = startAgent(created["token"])
    check(True, "agent killed mid-run and started again")
    row = waitFor(lambda: (lambda r: r if r["state"] in FINAL else None)(submission(sub["submission_id"])), 600,
                  "the build finishes")
    check(row["state"] == "finished", "the submission finished ({0}: {1})".format(row["state"], row["error"]))
    run = webapi.call("GET", "/api/runs/" + sub["run_id"])
    check(run["summary"]["status"] == "finished" and run["summary"]["slurm_job_id"] == build,
          "its run was uploaded as finished, from Slurm job {0}".format(build))
    check(len(run["pore_results"]) == 3, "with its three Poreblazer results")

    # 3. cancelling
    long = webapi.demoRecipe(BLOCKS, name="slurmrest cancel " + name, seed=6, stages=[
        {"op": "seed", "count": 6}, {"repeat": 100, "stages": [{"op": "grow", "count": 2}, slow]}])
    sub = webapi.call("POST", "/api/submissions", {"recipe": long, "backend": "slurm", "owner": "slurmrest test"})
    waitFor(lambda: submission(sub["submission_id"])["state"] in ("running",) + FINAL, 180, "the long build starts")
    time.sleep(5)
    webapi.call("POST", "/api/submissions/{0}/cancel".format(sub["submission_id"]))
    row = waitFor(lambda: (lambda r: r if r["state"] in FINAL else None)(submission(sub["submission_id"])), 180,
                  "the cancelled build ends")
    check(row["state"] == "cancelled", "a running submission is cancelled through slurmrestd ({0})".format(
        row["state"]))
    run = waitFor(lambda: runOrNone(sub["run_id"]), 60, "the cancelled run is uploaded")
    check(run["summary"]["status"] in ("failed", "incomplete"), "its run was uploaded as {0}".format(
        run["summary"]["status"]))

    # 4. a sweep, as one array job
    base = webapi.demoRecipe(BLOCKS, name="slurmrest sweep " + name, seed=3, stages=[
        {"op": "seed", "count": 4}, {"repeat": 2, "stages": [{"op": "grow", "count": 2}]}])
    grid = [{"name": "box", "path": "/cell/box", "all": True, "values": [20, 25]},
            {"name": "grow", "path": "/stages/1/stages/0/count", "values": [1, 2]}]
    sweep = webapi.call("POST", "/api/sweeps", {"recipe": base, "parameters": grid, "backend": "slurm",
                                                "owner": "slurmrest test"})
    runs = waitFor(lambda: (lambda rs: rs if all(r["state"] in FINAL for r in rs) else None)(
        webapi.call("GET", "/api/sweeps/{0}".format(sweep["sweep_id"]))["runs"]), 600, "the sweep's runs finish")
    check(all(r["state"] == "finished" and r["uploaded"] for r in runs),
          "all 4 runs finished and were uploaded ({0})".format(sorted({(r["state"], r["error"]) for r in runs})))
    arrays = {r["external_id"][6:].split("_")[0] for r in runs}
    check(len(arrays) == 1, "as one array job ({0})".format(", ".join(sorted(arrays))))

    # 5. a rejected token, then a new one
    writeToken("not-a-token")
    status = waitFor(lambda: (lambda c: c if c and c["state"] == "warn" else None)(card(name)), 60,
                     "its status card warns")
    check("rejected the token for user 'ambuild'" in status["summary"],
          "with a rejected token: {0}".format(status["summary"]))
    small = webapi.demoRecipe(BLOCKS, name="slurmrest token " + name, seed=4, stages=[{"op": "seed", "count": 4}])
    sub = webapi.call("POST", "/api/submissions", {"recipe": small, "backend": "slurm", "owner": "slurmrest test"})
    time.sleep(12)
    check(submission(sub["submission_id"])["state"] == "queued", "and it claims nothing meanwhile")
    writeToken()
    row = waitFor(lambda: (lambda r: r if r["state"] in FINAL else None)(submission(sub["submission_id"])), 300,
                  "the run finishes once the token file has a new token")
    check(row["state"] == "finished", "a new token in the file is used without a restart")

    webapi.call("POST", "/api/agents/{0}/revoke".format(created["agent_id"]))
    agent.terminate()
    agent.wait(timeout=30)
    print("all slurmrest agent checks passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit as exc:
        if exc.code not in (0, None):
            if os.path.exists(LOG):
                with open(LOG) as f:
                    print("--- slurmrest agent log\n" + f.read()[-6000:])
            print("--- squeue\n" + subprocess.run(["squeue"], capture_output=True, text=True).stdout)
            outputs = sorted((p for p in os.listdir(ROOT) if p.endswith(".out")),
                             key=lambda p: os.path.getmtime(os.path.join(ROOT, p)))
            for name in outputs[-4:]:
                with open(os.path.join(ROOT, name), errors="replace") as f:
                    print("--- job output {0}\n{1}".format(name, f.read()[-3000:]))
            with open("/var/log/slurm/slurmrestd.log", errors="replace") as f:
                print("--- slurmrestd log\n" + f.read()[-2000:])
        raise
