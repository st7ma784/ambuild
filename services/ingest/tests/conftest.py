import json
import os
import uuid

import pytest

PORE_RESULT = {
    "version": "3.0.5",
    "returncode": 0,
    "system_volume_A3": 8000.0,
    "system_mass_g_mol": 772.0,
    "system_density_g_cm3": 0.16,
    "helium_volume_A3": 6964.364,
    "helium_volume_cm3_g": 5.433,
    "geometric_volume_A3": 7106.824,
    "geometric_volume_cm3_g": 5.544,
    "surface_area_A2": 1721.62,
    "surface_area_m2_cm3": 2152.02,
    "surface_area_m2_g": 13429.83,
    "pore_limiting_diameter_A": 9.59,
    "maximum_pore_diameter_A": 13.25,
    "percolated_dimensions": 1,
    "psd": [[4.625, 0.0002], [4.875, 0.0006]],
    "psd_cumulative": [[-0.125, 1.0], [0.125, 1.0]],
}


def writeRunDir(path, status="finished", parentRunId=None, partialLastEvent=False):
    """Write a run directory in the format of ambuild.ab_run; return its run id"""
    runId = str(uuid.uuid4())
    os.makedirs(os.path.join(path, "inputs", "params"))
    os.makedirs(os.path.join(path, "poreblazer_1"))
    files = {
        "inputs/params/bond_params.csv": "a,b,1.0\n",
        "ambuild.csv": "step,type\n1,seed\n",
        "step_1.pkl.gz": "pickle",
        "poreblazer_1/poreblazer.log": "log",
        "poreblazer_1/nitrogen_network.grd": "big grid",
    }
    for rel, content in files.items():
        with open(os.path.join(path, *rel.split("/")), "w") as f:
            f.write(content)
    run = {
        "schema_version": 1,
        "run_id": runId,
        "parent_run_id": parentRunId,
        "status": status,
        "started": "2026-09-25T18:00:00+00:00",
        "finished": None if status == "running" else "2026-09-25T18:05:00+00:00",
        "error": None,
        "ambuild": {"version": "2.0.1", "git_commit": "abc", "git_dirty": False},
        "scheduler": {"type": "slurm", "variables": {"SLURM_JOB_ID": "42"}},
        "inputs": [
            {"kind": "params", "path": "inputs/params/bond_params.csv", "sha256": "x", "size": 8}
        ],
    }
    with open(os.path.join(path, "run.json"), "w") as f:
        json.dump(run, f)
    step = {
        "step": 1, "type": "seed", "time": 0.1, "tot_time": 0.2, "num_frags": 3,
        "num_particles": 15, "num_blocks": 3, "density": 0.01, "num_free_endGroups": 12,
        "potential_energy": 0, "num_tries": 0, "fragment_types": "{'A': 3}", "file_count": 0,
    }
    events = [
        {"type": "run_started", "step": 0, "timestamp": 1.0, "data": {"run_id": runId}},
        {"type": "step", "step": 1, "timestamp": 2.0, "data": step},
        {"type": "artifact", "step": 2, "timestamp": 3.0,
         "data": {"relpath": "step_1.pkl.gz", "kind": "pickle", "path": "/x/step_1.pkl.gz"}},
        {"type": "pore_result", "step": 2, "timestamp": 4.0,
         "data": dict(PORE_RESULT, directory="C:\\runs\\x\\poreblazer_1")},
    ]
    if status != "running":
        events.append({"type": "run_finished", "step": 2, "timestamp": 5.0,
                       "data": {"status": status, "error": None}})
    with open(os.path.join(path, "events.jsonl"), "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
        if partialLastEvent:
            f.write('{"type": "st')
    return runId


@pytest.fixture
def runDir(tmp_path):
    """Factory: runDir(name, **kwargs) -> (path, run id)"""

    def make(name="run", **kwargs):
        path = str(tmp_path / name)
        return path, writeRunDir(path, **kwargs)

    return make
