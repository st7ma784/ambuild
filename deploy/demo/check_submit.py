"""End-to-end check of submission through the web GUI (docker compose --profile agent-test):

1. a recipe submitted through the API is claimed by the local agent, built, uploaded, and
   browsable as a run labelled with the recipe's name, with its Poreblazer results;
2. the same recipe and seed run here from the command line give the same structure, and
   so does submitting it through the agent skill's helper (.claude/skills/ambuild);
3. cancelling a running submission stops it, and its run is uploaded as failed.
"""
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import webapi  # noqa: E402

from ambuild import ab_util  # noqa: E402

FINAL = ("finished", "failed", "cancelled")


def check(condition, message):
    if not condition:
        sys.exit("FAIL: " + message)
    print("ok:", message)


def waitFor(submissionId, states, seconds):
    deadline = time.time() + seconds
    while True:
        row = webapi.call("GET", "/api/submissions/{0}".format(submissionId))
        if row["state"] in states:
            return row
        if time.time() > deadline:
            sys.exit("FAIL: submission {0} still {1} after {2} s".format(submissionId, row["state"], seconds))
        time.sleep(2)


def waitForAgent(seconds=120):
    deadline = time.time() + seconds
    while not any(a["live"] for a in webapi.call("GET", "/api/agents")["agents"]):
        if time.time() > deadline:
            sys.exit("FAIL: no live agent")
        time.sleep(2)


def withoutRunId(text):
    """A structure file's text without its header's run id and recipe hash: two runs of the
    same build write the same atoms, but those can differ (docs/export.md)"""
    return re.sub(r' (run_id|recipe_sha256)="[^"]*"', "", text)


def lastStructure(rundir):
    files = sorted(glob.glob(os.path.join(rundir, "step_*.xyz")), key=lambda p: int(os.path.basename(p)[5:-4]))
    with open(files[-1]) as f:
        return withoutRunId(f.read())


def main():
    webapi.waitForWeb()
    waitForAgent()
    blocks = ab_util.blocksDir()
    name = "e2e {0}".format(uuid.uuid4().hex[:8])
    body = webapi.demoRecipe(blocks, name=name, seed=11)

    # 1. through the web GUI
    sub = webapi.call("POST", "/api/submissions", {"recipe": body, "owner": "e2e check"})
    print("submitted", sub["submission_id"], "run", sub["run_id"])
    row = waitFor(sub["submission_id"], FINAL, 900)
    check(row["state"] == "finished", "submission finished ({0}: {1})".format(row["state"], row["error"]))
    check(row["uploaded"] and row["run_status"] == "finished", "its run was uploaded as finished")
    run = webapi.call("GET", "/api/runs/" + sub["run_id"])
    check(run["summary"]["label"] == name, "the run is labelled with the recipe's name")
    check(run["run"]["run_json"]["random"]["seed"] == 11, "the run recorded the seed")
    check(any(f["path"].startswith("inputs/recipe/") for f in run["files"]), "the run recorded its recipe")
    check(len(run["pore_results"]) == 1, "the run has its Poreblazer result")
    frames = webapi.call("GET", "/api/runs/{0}/structures".format(sub["run_id"]))["frames"]
    check(len(frames) == 4, "a structure for each checkpoint (seed, two passes, Poreblazer): {0}".format(len(frames)))
    web = withoutRunId(webapi.get(frames[-1]["url"]).decode())

    # 2. the same recipe from the command line
    work = tempfile.mkdtemp()
    blobs = os.path.join(work, "blobs")
    os.makedirs(blobs)
    for n in ("ch4.car", "ch4.csv", "benzene2.car", "benzene2.csv"):
        with open(os.path.join(blocks, n), "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        shutil.copy(os.path.join(blocks, n), os.path.join(blobs, digest))
    recipeFile = os.path.join(work, "recipe.json")
    with open(recipeFile, "w") as f:
        json.dump(body, f)
    out = subprocess.run([sys.executable, "-m", "ambuild.recipe", "run", recipeFile, "--output",
                          os.path.join(work, "cli"), "--blobs", blobs], capture_output=True, text=True)
    check(out.returncode == 0, "the recipe runs from the command line" + ("" if not out.returncode else ": " + out.stderr[-2000:]))
    check(lastStructure(os.path.join(work, "cli")) == web, "the command line gives the same structure as the web GUI")

    # 2b. the same through the agent skill's helper (.claude/skills/ambuild), as an AI agent
    #     following SKILL.md would: validate a recipe with local files, preview, submit
    if os.path.isdir("/skill"):
        def skill(*args):
            out = subprocess.run([sys.executable, "/skill/scripts/ambuild_api.py", "--url", webapi.API] + list(args),
                                 capture_output=True, text=True)
            if out.returncode != 0:
                check(False, "skill: {0}: {1}".format(" ".join(args[:2]), (out.stderr or out.stdout)[-500:]))
            return json.loads(out.stdout)

        local = dict(body, name=name + " (skill)", fragments=[
            dict(f, car=os.path.join(blocks, os.path.basename(f["name"]) + ".car"),
                 csv=os.path.join(blocks, os.path.basename(f["name"]) + ".csv")) for f in body["fragments"]])
        recipePath = os.path.join(work, "skill-recipe.json")
        with open(recipePath, "w") as f:
            json.dump(local, f)
        check(skill("validate", "--recipe", recipePath)["valid"], "skill: a recipe with local files validates")
        preview = skill("submit", "--recipe", recipePath, "--backend", "local", "--seed", "11")
        check("preview" in preview and webapi.call("GET", "/api/submissions")["submissions"][0]["name"] != local["name"],
              "skill: submit without --yes only previews")
        queued = skill("submit", "--recipe", recipePath, "--backend", "local", "--seed", "11", "--yes")
        row = waitFor(queued["submission_id"], FINAL, 900)
        check(row["state"] == "finished", "skill: the submitted run finished")
        run = skill("run", queued["run_id"])
        check(run["summary"]["status"] == "finished" and run["poreblazer"], "skill: reads the run and its Poreblazer result")
        check(skill("structure", queued["run_id"])["atoms"] > 0, "skill: summarises the run's structure")
        check(lastStructure(os.path.join(work, "cli")).splitlines()[2:] ==
              webapi.get(webapi.call("GET", "/api/runs/{0}/structures".format(queued["run_id"]))["frames"][-1]["url"])
              .decode().splitlines()[2:], "skill: the same recipe and seed built the same structure again")

    # 3. cancelling a running submission (a long MD run; methane only, which the parameters cover)
    refs = {n: webapi.upload(os.path.join(blocks, n)) for n in ("ch4.car", "ch4.csv")}
    long = {"recipe_version": 1, "name": name + " (cancelled)", "cell": {"box": [25, 25, 25]},
            "fragments": [{"type": "A", "car": refs["ch4.car"], "csv": refs["ch4.csv"], "name": "ch4"}],
            "bond_types": ["A:a-A:a"],
            "stages": [{"op": "seed", "count": 4}, {"op": "md", "cycles": 100000000}], "seed": 1}
    sub = webapi.call("POST", "/api/submissions", {"recipe": long, "owner": "e2e check"})
    waitFor(sub["submission_id"], ("running",) + FINAL, 300)
    time.sleep(10)
    check(webapi.call("POST", "/api/submissions/{0}/cancel".format(sub["submission_id"]))["state"] == "cancelling",
          "a running submission is cancelling")
    row = waitFor(sub["submission_id"], FINAL, 180)
    check(row["state"] == "cancelled", "and then cancelled ({0})".format(row["state"]))
    run = webapi.call("GET", "/api/runs/" + sub["run_id"])
    check(run["summary"]["status"] == "failed" and "Cancelled" in (run["summary"]["error"] or ""),
          "its run was uploaded as failed: {0}".format(run["summary"]["error"]))
    print("all submission checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
