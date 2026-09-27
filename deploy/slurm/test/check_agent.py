"""Check the web GUI's Slurm agent end to end, for run_test.sh (after the scripted builds):

1. an agent added through the API (as on the Agents page) comes online;
2. a recipe submitted for Slurm is submitted by it with submit_build.sh (with the
   recipe's resources as sbatch options) and its run is uploaded while it runs, so the
   run page is live;
3. the agent is killed mid-run and started again: it takes the job up and the run
   finishes, uploaded by its Slurm upload job, with its results;
4. cancelling a running submission scancels it, and the run is uploaded as cancelled;
5. a 3x3 grid sweep runs as one array job, and its page plots a result against both
   parameters;
6. a campaign steered by the controller meets a density goal in fewer runs than the
   equivalent grid, each round one array job, every trial linked to its runs.

Only the standard library (and deploy/demo/webapi.py). AMBUILD_API_URL: the web GUI.
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import uuid

sys.path.insert(0, "/opt/ambuild-demo")
import webapi  # noqa: E402

BLOCKS = os.environ.get("AMBUILD_BLOCKS_DIR", "/ambuild/tests/blocks")
FINAL = ("finished", "failed", "cancelled")


def check(condition, message):
    if not condition:
        for name, path in (("agent", "/tmp/agent.log"), ("campaign controller", "/tmp/campaigns.log")):
            if os.path.exists(path):
                print("--- {0} log".format(name))
                with open(path) as f:
                    print(f.read()[-6000:])
        sys.exit("FAIL: " + message)
    print("ok:", message)


def waitFor(condition, seconds, what):
    deadline = time.time() + seconds
    while True:
        value = condition()
        if value:
            return value
        if time.time() > deadline:
            check(False, "{0} within {1} s".format(what, seconds))
        time.sleep(1)


def submission(submissionId):
    return webapi.call("GET", "/api/submissions/{0}".format(submissionId))


def runOrNone(runId):
    try:
        return webapi.call("GET", "/api/runs/" + runId)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def startAgent(token):
    env = dict(os.environ, AMBUILD_API_URL=webapi.API, AMBUILD_AGENT_TOKEN=token, AMBUILD_AGENT_BACKEND="slurm",
               AMBUILD_RUNS_ROOT=os.environ["AMBUILD_RUNS_ROOT"], AMBUILD_SLURM_DIR="/opt/ambuild-slurm",
               AMBUILD_AGENT_POLL="2", AMBUILD_AGENT_HEARTBEAT="5", AMBUILD_AGENT_UPLOAD_EVERY="4")
    log = open("/tmp/agent.log", "a")
    return subprocess.Popen(["ambuild-agent"], env=env, stdout=log, stderr=subprocess.STDOUT)


def main():
    webapi.waitForWeb()
    name = "slurm-test-" + uuid.uuid4().hex[:6]
    created = webapi.call("POST", "/api/agents", {"name": name, "backend": "slurm"})
    agent = startAgent(created["token"])
    waitFor(lambda: any(a["name"] == name and a["live"] for a in webapi.call("GET", "/api/agents")["agents"]),
            60, "the agent comes online")
    check(True, "agent {0} added through the API is online".format(name))

    # 2-3. a build on Slurm, live, surviving an agent restart
    slow = {"op": "poreblazer", "threads": 1, "settings": {"cubelet_size": 0.15}}
    stages = [{"op": "seed", "count": 6},
              {"repeat": 3, "stages": [{"op": "grow", "count": 5}, {"op": "zip", "bond_margin": 1.0}, slow]}]
    body = webapi.demoRecipe(BLOCKS, name="slurm e2e " + name, seed=5, stages=stages)
    body["resources"] = {"cpus": 1, "time": "00:20:00"}
    sub = webapi.call("POST", "/api/submissions", {"recipe": body, "backend": "slurm", "owner": "slurm test"})
    row = waitFor(lambda: (lambda r: r if r["state"] in ("submitted", "running") + FINAL else None)(
        submission(sub["submission_id"])), 60, "the agent submits it")
    check((row["external_id"] or "").startswith("slurm:"), "submitted to Slurm as {0}".format(row["external_id"]))
    build = row["external_id"][6:].split("/")[0]
    waitFor(lambda: submission(sub["submission_id"])["state"] in ("running",) + FINAL, 120, "the build job starts")
    live = waitFor(lambda: (lambda r: r if r and r["summary"]["status"] == "running" else None)(runOrNone(sub["run_id"])),
                   120, "the running run is uploaded (live progress)")
    check(live["summary"]["status"] == "running", "the run is in the database while it runs")
    page = webapi.get("/runs/" + sub["run_id"]).decode()
    check('hx-trigger="every 15s"' in page, "its page refreshes itself while it runs")

    agent.send_signal(signal.SIGKILL)
    agent.wait()
    agent = startAgent(created["token"])
    check(True, "agent killed mid-run and started again")
    row = waitFor(lambda: (lambda r: r if r["state"] in FINAL else None)(submission(sub["submission_id"])), 600,
                  "the build finishes")
    check(row["state"] == "finished", "the submission finished ({0}: {1})".format(row["state"], row["error"]))
    run = webapi.call("GET", "/api/runs/" + sub["run_id"])
    check(run["summary"]["status"] == "finished" and run["summary"]["label"] == body["name"],
          "its run was uploaded as finished, labelled with the recipe's name")
    check(run["summary"]["slurm_job_id"] == build, "the run records its Slurm build job {0}".format(build))
    check(len(run["pore_results"]) == 3, "with its three Poreblazer results")
    frames = webapi.call("GET", "/api/runs/{0}/structures".format(sub["run_id"]))["frames"]
    check(len(frames) == 4, "and a structure per checkpoint ({0})".format(len(frames)))

    # 4. cancelling
    long = webapi.demoRecipe(BLOCKS, name="slurm cancel " + name, seed=6, stages=[
        {"op": "seed", "count": 6}, {"repeat": 100, "stages": [{"op": "grow", "count": 2}, slow]}])
    sub = webapi.call("POST", "/api/submissions", {"recipe": long, "backend": "slurm", "owner": "slurm test"})
    waitFor(lambda: submission(sub["submission_id"])["state"] in ("running",) + FINAL, 180, "the long build starts")
    time.sleep(5)
    state = webapi.call("POST", "/api/submissions/{0}/cancel".format(sub["submission_id"]))["state"]
    check(state == "cancelling", "a running Slurm submission is cancelling")
    row = waitFor(lambda: (lambda r: r if r["state"] in FINAL else None)(submission(sub["submission_id"])), 180,
                  "the cancelled build ends")
    check(row["state"] == "cancelled", "and then cancelled ({0})".format(row["state"]))
    run = waitFor(lambda: runOrNone(sub["run_id"]), 60, "the cancelled run is uploaded")
    check(run["summary"]["status"] in ("failed", "incomplete"),
          "its run was uploaded as {0}: {1}".format(run["summary"]["status"], run["summary"]["error"]))

    # 5. a 3x3 grid sweep, as one array job, and its page plotting against both parameters
    base = webapi.demoRecipe(BLOCKS, name="slurm sweep " + name, seed=3, stages=[
        {"op": "seed", "count": 4},
        {"repeat": 2, "stages": [{"op": "grow", "count": 2}, {"op": "zip", "bond_margin": 1.0}]}])
    grid = [{"name": "box", "path": "/cell/box", "all": True, "values": [20, 25, 30]},
            {"name": "grow", "path": "/stages/1/stages/0/count", "values": [1, 2, 3]}]
    sweep = webapi.call("POST", "/api/sweeps", {"recipe": base, "parameters": grid, "backend": "slurm",
                                                "owner": "slurm test"})
    check(sweep["runs"] == 9, "a 3x3 grid sweep of 9 runs is queued")

    def sweepRuns():
        return webapi.call("GET", "/api/sweeps/{0}".format(sweep["sweep_id"]))["runs"]

    runs = waitFor(lambda: (lambda rs: rs if all(r["state"] in FINAL for r in rs) else None)(sweepRuns()), 600,
                   "the sweep's runs finish")
    check(all(r["state"] == "finished" and r["uploaded"] for r in runs),
          "all 9 runs finished and were uploaded ({0})".format(sorted({r["state"] for r in runs})))
    tasks = [r["external_id"][6:].split("/")[0] for r in runs]
    arrays = {t.split("_")[0] for t in tasks}
    check(len(arrays) == 1 and sorted(int(t.split("_")[1]) for t in tasks) == list(range(9)),
          "as the 9 tasks of one array job ({0})".format(", ".join(sorted(arrays))))
    check(all(r["results"]["density"] is not None for r in runs), "each with its density")
    page = webapi.get("/sweeps/{0}?metric=density".format(sweep["sweep_id"])).decode()
    check('id="sweep-box-data"' in page and 'id="sweep-grow-data"' in page,
          "the sweep page plots density against box and against grow")

    # 6. a campaign steered by the controller (Optuna TPE) at a density band, over box size
    #    and grow count: it must meet the goal in fewer runs than the equivalent grid (6 box
    #    sizes x 6 grow counts x 2 seeds = 72 runs), each round one array job, and every
    #    trial linked to its runs
    controllerToken = webapi.call("POST", "/api/agents", {"name": "controller-" + name, "backend": "campaigns"})
    env = dict(os.environ, AMBUILD_API_URL=webapi.API, AMBUILD_CAMPAIGNS_TOKEN=controllerToken["token"],
               AMBUILD_CAMPAIGNS_POLL="3")
    controller = subprocess.Popen(["ambuild-campaigns"], env=env, stdout=open("/tmp/campaigns.log", "a"),
                                  stderr=subprocess.STDOUT)
    spec = {
        "parameters": [{"name": "box", "path": "/cell/box", "all": True, "type": "float", "low": 20, "high": 32},
                       {"name": "grow", "path": "/stages/1/stages/0/count", "type": "int", "low": 1, "high": 6}],
        "constraints": [{"metric": "density", "min": 0.068, "max": 0.072}],
        "objective": {"target": {"metric": "density", "value": 0.07}},
        "replicates": 2, "method": "tpe", "initial_points": 4, "batch_size": 3,
        "budget": {"runs": 48}, "stop": {"feasible_points": 1}, "sampler_seed": 1,
    }
    campaign = webapi.call("POST", "/api/campaigns", {"recipe": base, "spec": spec, "backend": "slurm",
                                                      "name": "slurm campaign " + name, "owner": "slurm test"})

    def campaignDetail():
        return webapi.call("GET", "/api/campaigns/{0}".format(campaign["campaign_id"]))

    detail = waitFor(lambda: (lambda d: d if d["campaign"]["state"] != "active" else None)(campaignDetail()), 1200,
                     "the campaign ends")
    trials = detail["trials"]
    check(detail["campaign"]["state"] == "finished" and detail["campaign"]["message"].startswith("goal met"),
          "the campaign met its goal: {0} ({1} trials in {2} rounds)".format(
              detail["campaign"]["message"], len(trials), max(t["round"] for t in trials)))
    check(detail["runs_used"] < 72, "in {0} runs, fewer than the grid's 72".format(detail["runs_used"]))
    best = next(t for t in trials if t["number"] == detail["best"])
    inBand = [r for r in best["runs"] if 0.068 <= (r["results"]["density"] or 0) <= 0.072]
    check(len(inBand) >= 1 and best["score"]["constraints"][0]["fraction"] >= 0.5,
          "its best point {0} is in the band for {1} of its 2 seeds (densities {2})".format(
              best["params"], len(inBand), ", ".join("{0:.4f}".format(r["results"]["density"]) for r in best["runs"])))
    check(max(t["round"] for t in trials) >= 2, "after Bayesian rounds, not just the first (random) one")
    check(all(r["uploaded"] and r["run_id"] for t in trials for r in t["runs"]) and
          all(len(t["runs"]) == 2 for t in trials), "every trial links to its 2 runs")
    for t in trials:
        webapi.get("/runs/" + t["runs"][0]["run_id"])  # the links work
    arrays = {}
    for sweepId in {t["sweep_id"] for t in trials}:
        rows = webapi.call("GET", "/api/sweeps/{0}".format(sweepId))["runs"]
        arrays[sweepId] = {r["external_id"][6:].split("_")[0] for r in rows}
    check(all(len(a) == 1 for a in arrays.values()),
          "each of its {0} rounds ran as one array job".format(len(arrays)))
    check("campaign-progress-data" in webapi.get("/campaigns/{0}".format(campaign["campaign_id"])).decode(),
          "and its page plots its progress")
    controller.terminate()
    controller.wait(timeout=30)

    webapi.call("POST", "/api/agents/{0}/revoke".format(created["agent_id"]))
    agent.terminate()
    agent.wait(timeout=30)
    print("all Slurm agent checks passed")
    print(json.dumps({"agent": name}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
