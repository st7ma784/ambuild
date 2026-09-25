import os
import time

from ambuild_ingest import cli
from ambuild_ingest.rundir import RunDirectory, findRunDirectories


def test_events_skip_partial_last_line(runDir):
    path, runId = runDir(partialLastEvent=True)
    events = RunDirectory(path).events()
    assert [seq for seq, _ in events] == [0, 1, 2, 3, 4]
    assert events[0][1]["data"]["run_id"] == runId


def test_files_exclude_grids_and_child_runs(runDir):
    path, _ = runDir()
    runDir(os.path.join("run", "child"), parentRunId="p")
    assert RunDirectory(path).files() == [
        "ambuild.csv",
        "events.jsonl",
        "inputs/params/bond_params.csv",
        "poreblazer_1/poreblazer.log",
        "run.json",
        "step_1.pkl.gz",
    ]
    assert findRunDirectories(path) == [path, os.path.join(path, "child")]


def test_scan_selection(runDir, tmp_path):
    finished, _ = runDir("finished")
    uploaded, _ = runDir("uploaded")
    open(os.path.join(uploaded, cli.MARKER_FILE), "w").close()
    running, _ = runDir("running", status="running")
    stale, _ = runDir("stale", status="running")
    old = time.time() - 7200
    for name in ("run.json", "events.jsonl"):
        os.utime(os.path.join(stale, name), (old, old))

    args = cli.parseArgs(["--scan", str(tmp_path), "--stale-after", "3600"])
    selected = {os.path.basename(r.path): finalise for r, finalise in cli.selectRuns(args)}
    assert selected == {"finished": False, "stale": True}

    args = cli.parseArgs(["--scan", str(tmp_path)])
    assert [os.path.basename(r.path) for r, _ in cli.selectRuns(args)] == ["finished"]


def test_explicit_rundirs_recursive(runDir):
    path, _ = runDir()
    runDir(os.path.join("run", "child"))
    args = cli.parseArgs(["--finalise", "--recursive", path])
    selected = cli.selectRuns(args)
    assert [r.path for r, _ in selected] == [path, os.path.join(path, "child")]
    assert all(finalise for _, finalise in selected)
