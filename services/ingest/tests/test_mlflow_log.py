"""Logging runs to MLflow (ambuild_ingest.mlflow_log), against a stand-in client."""
import json
import os
import types

import pytest

pytest.importorskip("mlflow")

from ambuild_ingest import mlflow_log
from ambuild_ingest.rundir import RunDirectory
from conftest import PORE_RESULT, writeRunDir

RECIPE = {
    "recipe_version": 1, "name": "graphyne_large", "description": "long text that isn't a parameter",
    "cell": {"box": [60, 60, 60], "bond_margin": 0.5, "typed_bond_lengths": True},
    "fragments": [{"type": "A", "car": "sha256:1", "csv": "sha256:2", "name": "benzene_135"},
                  {"type": "B", "car": "sha256:3", "csv": "sha256:4", "name": "acetylene_cg"}],
    "params": {"bond_params.csv": "params/gaff_carbon/bond_params.csv"},
    "bond_types": ["A:a-B:g", "A:a-B:h"],
    "stages": [{"op": "seed", "count": 8},
               {"repeat": 10, "stages": [{"op": "grow", "count": 60}, {"op": "optimise", "rigid_body": True}]},
               {"op": "conduction", "through_space": True}],
    "seed": 31,
}


class FakeClient:
    """Just enough of MlflowClient: experiments, runs with tags, params, metrics, artifacts"""

    def __init__(self):
        self.experiments, self.runs = {}, {}

    def get_experiment_by_name(self, name):
        eid = self.experiments.get(name)
        return types.SimpleNamespace(experiment_id=eid) if eid else None

    def create_experiment(self, name):
        self.experiments[name] = str(len(self.experiments) + 1)
        return self.experiments[name]

    def _view(self, r):
        return types.SimpleNamespace(info=types.SimpleNamespace(run_id=r["id"]),
                                     data=types.SimpleNamespace(tags=dict(r["tags"])))

    def search_runs(self, experimentIds, filter_string, max_results):
        runId = filter_string.split("'")[1]
        found = [r for r in self.runs.values() if r["experiment"] in experimentIds and r["tags"].get("ambuild.run_id") == runId]
        return [self._view(r) for r in found[:max_results]]

    def create_run(self, experimentId, start_time=None, tags=None, run_name=None):
        rid = "m{0}".format(len(self.runs) + 1)
        self.runs[rid] = {"id": rid, "experiment": experimentId, "tags": dict(tags or {}), "params": {}, "metrics": [],
                          "artifacts": [], "name": run_name, "start": start_time, "status": "RUNNING"}
        return types.SimpleNamespace(info=types.SimpleNamespace(run_id=rid))

    def set_tag(self, rid, k, v):
        self.runs[rid]["tags"][k] = v

    def log_batch(self, rid, metrics=(), params=()):
        assert len(params) <= 100 and len(metrics) <= 1000
        for p in params:
            old = self.runs[rid]["params"].get(p.key)
            assert old in (None, p.value), "MLflow refuses to change a logged parameter"
            self.runs[rid]["params"][p.key] = p.value
        self.runs[rid]["metrics"] += [(m.key, m.value, m.step) for m in metrics]

    def log_artifact(self, rid, path):
        with open(path) as f:
            self.runs[rid]["artifacts"].append((os.path.basename(path), json.load(f)))

    def set_terminated(self, rid, status, end_time=None):
        self.runs[rid]["status"], self.runs[rid]["end"] = status, end_time


def run(status="finished", recipe=True):
    r = {"run_id": "11111111-2222-3333-4444-555555555555", "status": status, "parent_run_id": None,
         "started": "2026-09-25T18:00:00+00:00", "finished": "2026-09-25T18:05:00+00:00", "error": None,
         "ambuild": {"version": "2.0.1", "git_commit": "abc"}, "random": {"seed": 31},
         "inputs": [{"kind": "recipe", "path": "inputs/recipe.json", "name": "graphyne_large", "recipe_sha256": "f00"}]
         if recipe else []}
    return r


EVENTS = [
    (0, {"type": "step", "step": 1, "data": {"step": 1, "density": 0.1, "num_particles": 100, "num_blocks": 8}}),
    (1, {"type": "step", "step": 2, "data": {"step": 2, "density": 0.2, "num_particles": 300, "num_blocks": 2,
                                             "num_free_endGroups": 40, "potential_energy": -5.0, "tot_time": 42.0}}),
    (2, {"type": "pore_result", "step": 2, "data": PORE_RESULT}),
    (3, {"type": "ion_map_result", "step": 2, "data": {"ion": "Li+", "sites": 3, "site_energy": -2.0,
                                                       "escape_barrier": 1.5, "lowest_barrier": 0.2}}),
    (4, {"type": "conduction_result", "step": 2, "data": {"sites": 120, "gap": 3.5, "conductance": 0.02,
                                                          "log_transmission": -18.0, "log_hopping": -24.4,
                                                          "tunnelling_share": 1.0, "radical_domains": 0}}),
]


def test_a_recipe_run_is_logged_with_its_settings_and_results():
    client = FakeClient()
    mid = mlflow_log.Logger("http://x", webUrl="http://scc:8080/", client=client).log(run(), EVENTS, RECIPE)
    r = client.runs[mid]
    assert client.experiments == {"ambuild/graphyne_large": "1"}
    assert r["tags"]["ambuild.run_id"] == run()["run_id"] and r["tags"]["ambuild.recipe_sha256"] == "f00"
    assert r["tags"]["ambuild.url"] == "http://scc:8080/runs/" + run()["run_id"]
    p = r["params"]
    assert p["seed"] == "31" and p["cell/box"] == "60,60,60" and p["cell/typed_bond_lengths"] == "true"
    assert p["stages/1/repeat"] == "10" and p["stages/1/stages/0/count"] == "60"  # the paths sweeps vary
    assert p["stages/2/through_space"] == "true"
    assert p["blocks"] == "benzene_135,acetylene_cg" and p["bond_types"] == "A:a-B:g,A:a-B:h"
    assert p["params"] == "gaff_carbon" and "description" not in p
    final = {k: v for k, v, s in r["metrics"] if not k.startswith("step/")}
    assert final["final_density"] == 0.2 and final["final_num_blocks"] == 2 and final["final_build_seconds"] == 42.0
    assert final["surface_area_m2_g"] == PORE_RESULT["surface_area_m2_g"]
    assert final["pore_limiting_diameter_a"] == PORE_RESULT["pore_limiting_diameter_A"]
    assert final["li_escape_barrier"] == 1.5 and final["el_log_transmission"] == -18.0 and final["el_log_hopping"] == -24.4
    history = [(k, v, s) for k, v, s in r["metrics"] if k == "step/density"]
    assert history == [("step/density", 0.1, 1), ("step/density", 0.2, 2)]
    assert [name for name, _ in r["artifacts"]] == ["run.json", "recipe.json"]
    assert r["status"] == "FINISHED" and r["start"] < r["end"]


def test_logging_is_idempotent_and_follows_the_status():
    client = FakeClient()
    logger = mlflow_log.Logger("http://x", webUrl="", client=client)
    assert logger.log(run("running"), EVENTS, RECIPE) is None  # not until it ends
    first = logger.log(run("incomplete"), EVENTS, RECIPE)
    assert client.runs[first]["status"] == "KILLED"
    assert logger.log(run("incomplete"), EVENTS, RECIPE) is None  # already logged at this status
    again = logger.log(run("finished"), EVENTS, RECIPE)  # re-uploaded later as finished
    assert again == first and len(client.runs) == 1 and client.runs[first]["status"] == "FINISHED"


def test_a_failed_script_run():
    client = FakeClient()
    r = dict(run("failed", recipe=False), error="RuntimeError: boom")
    mid = mlflow_log.Logger("http://x", webUrl="", client=client).log(r, EVENTS[:2], None)
    assert list(client.experiments) == ["ambuild/scripts"]
    assert client.runs[mid]["status"] == "FAILED" and client.runs[mid]["tags"]["ambuild.error"] == "RuntimeError: boom"
    assert client.runs[mid]["params"] == {"seed": "31"}


def test_long_values_are_cut_and_many_params_batched():
    recipe = dict(RECIPE, bond_types=["X:x-Y:y"] * 200, stages=[{"op": "seed", "count": i} for i in range(150)])
    client = FakeClient()
    mid = mlflow_log.Logger("http://x", webUrl="", client=client).log(run(), [], recipe)
    p = client.runs[mid]["params"]
    assert len(p["bond_types"]) == mlflow_log.MAX_VALUE and p["bond_types"].endswith("…")
    assert len(p) > 150  # more than one batch of 100


def test_the_recipe_is_read_from_a_run_directory(tmp_path):
    path = str(tmp_path / "run")
    writeRunDir(path)
    rundir = RunDirectory(path)
    assert mlflow_log.readRecipe(rundir) is None  # this one has no recipe input
    rundir.run["inputs"].append({"kind": "recipe", "path": "inputs/recipe.json", "name": "x"})
    with open(os.path.join(path, "inputs", "recipe.json"), "w") as f:
        json.dump(RECIPE, f)
    assert mlflow_log.readRecipe(rundir)["name"] == "graphyne_large"


def test_an_unreachable_server_is_reported_quickly():
    assert mlflow_log.reachable("http://127.0.0.1:9", timeout=1.0) is False
