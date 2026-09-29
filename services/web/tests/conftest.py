"""Recorded runs for the web tests, uploaded with ambuild-upload's own code into the
PostgreSQL and S3 of deploy/docker-compose.yml."""
import hashlib
import json
import os
import uuid

import pytest

TOKEN = uuid.uuid4().hex[:8]  # in every test run's script names, so queries find only these runs


def _psd(scale):
    return [[d / 4.0, scale * (d % 7) / 10.0] for d in range(1, 40)]


def pore(step, sa, pld, directory):
    return {
        "version": "3.0.5", "returncode": 0, "system_volume_A3": 15625.0, "system_mass_g_mol": 1200.0,
        "system_density_g_cm3": 0.13, "helium_volume_A3": 13000.0, "helium_volume_cm3_g": 6.5,
        "geometric_volume_A3": 14000.0, "geometric_volume_cm3_g": 7.0, "surface_area_A2": sa * 2.0,
        "surface_area_m2_cm3": sa / 5.0, "surface_area_m2_g": sa, "pore_limiting_diameter_A": pld,
        "maximum_pore_diameter_A": pld + 3.0, "percolated_dimensions": 1,
        "psd": _psd(sa / 1000.0), "psd_cumulative": [[d / 4.0, 1.0 - d / 40.0] for d in range(40)],
        "directory": directory,
    }


def structure(step, natoms=6):
    """An extended XYZ checkpoint, as Cell.writeStructure writes it (export format 1)"""
    lines = ["{0}".format(natoms),
             'Lattice="25.000000 0.0 0.0 0.0 25.000000 0.0 0.0 0.0 25.000000" '
             'Properties=species:S:1:pos:R:3:type:S:1:charge:R:1:fragment:S:1:block:I:1 pbc="T T T" step={0} '
             'run_id="test" ambuild_version="2.0.1" export_version=1'.format(step)]
    for i in range(natoms):
        carbon = i % 2 == 0
        lines.append("{0} {1:.6f} {2:.6f} {3:.6f} {4} {5:.4f} {6} {7}".format(
            "C" if carbon else "H", 1.0 + i, 2.0 + step, 3.0, "ca" if carbon else "ha", 0.0,
            "A" if i < natoms // 2 else "B", 1 + i // 3))
    return ("\n".join(lines) + "\n").encode()


def writeRun(path, name, status="finished", parentRunId=None, seed=None, nsteps=3, pores=(), error=None,
             extraFiles=None, structures=(), xyzArtifact=False):
    """A run directory in the format of ambuild.ab_run; returns (run id, {relpath: bytes})"""
    runId = str(uuid.uuid4())
    script = "inputs/script/webtest-{0}-{1}.py".format(TOKEN, name)
    files = {
        script: b"# build script\n",
        "inputs/params/bond_params.csv": b"a,b,1.0\n",
        "ambuild.csv": b"step,type\n",
        "step_1.pkl.gz": os.urandom(2048),
        "final.xyz": b"2\ncell\nC 0 0 0\nH 1 0 0\n",
    }
    files.update(extraFiles or {})
    for step in structures:
        files["step_{0}.xyz".format(step)] = structure(step)
    for rel, content in files.items():
        full = os.path.join(path, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(content)
    run = {
        "schema_version": 1, "run_id": runId, "parent_run_id": parentRunId, "status": status,
        "started": "2026-09-27T10:00:00+00:00",
        "finished": None if status == "running" else "2026-09-27T10:07:30+00:00",
        "error": error,
        "ambuild": {"version": "2.0.1", "git_commit": "0123456789abcdef", "git_dirty": False},
        "environment": {"python": "3.12.3", "platform": "Linux", "hostname": "node-" + name, "numpy": "2.1",
                        "hoomd": "7.2.0"},
        "command": ["python", "webtest-{0}-{1}.py".format(TOKEN, name)],
        "scheduler": {"type": "slurm", "variables": {"SLURM_JOB_ID": "4242"}},
        "cell": {"box_dim": [25.0, 25.0, 25.0], "atom_margin": 0.5, "bond_margin": 0.5,
                 "bond_angle_margin_degrees": 15.0, "params_dir": "/params"},
        "random": {"seed": seed, "state": "inputs/random/random_state.json"},
        "inputs": [
            {"kind": "script", "path": script, "sha256": hashlib.sha256(files[script]).hexdigest(), "size": 15},
            {"kind": "params", "path": "inputs/params/bond_params.csv", "sha256": "x", "size": 8},
        ],
    }
    with open(os.path.join(path, "run.json"), "w") as f:
        json.dump(run, f)
    events = [{"type": "run_started", "step": 0, "timestamp": 1.0, "data": {"run_id": runId}}]
    for i in range(1, nsteps + 1):
        events.append({"type": "step", "step": i, "timestamp": 1.0 + i, "data": {
            "step": i, "type": "grow" if i > 1 else "seed", "time": 0.5, "tot_time": 0.5 * i, "num_frags": 3 * i,
            "num_particles": 30 * i, "num_blocks": 4 - min(i, 3), "density": 0.05 * i, "num_free_endGroups": 12 + i,
            "potential_energy": -1.5 * i, "num_tries": i, "fragment_types": {"A": 3 * i}, "file_count": i}})
    events.append({"type": "artifact", "step": nsteps, "timestamp": 20.0,
                   "data": {"relpath": "step_1.pkl.gz", "kind": "pickle", "path": "/x/step_1.pkl.gz"}})
    for step in structures:
        events.append({"type": "artifact", "step": step, "timestamp": 20.0 + step,
                       "data": {"relpath": "step_{0}.xyz".format(step), "kind": "structure"}})
    if xyzArtifact:  # a run from before structure files: only writeXyz output
        events.append({"type": "artifact", "step": nsteps, "timestamp": 25.0,
                       "data": {"relpath": "final.xyz", "kind": "xyz"}})
    for p in pores:
        events.append({"type": "pore_result", "step": p["step"], "timestamp": 21.0, "data": p["result"]})
    if status != "running":
        events.append({"type": "run_finished", "step": nsteps, "timestamp": 30.0,
                       "data": {"status": status, "error": error}})
    with open(os.path.join(path, "events.jsonl"), "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return runId, files


@pytest.fixture(scope="session")
def recorded(tmp_path_factory):
    """Four uploaded runs: a build with its own and a child's Poreblazer results, a failed
    build, and a second finished build. Skipped without the Compose services."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs PostgreSQL and S3")
    import psycopg
    from ambuild_ingest.ingest import applySchema, uploadRun
    from ambuild_ingest.rundir import RunDirectory
    from ambuild_ingest.store import ObjectStore

    root = tmp_path_factory.mktemp("runs")
    runs = {}
    a, filesA = writeRun(str(root / "a"), "alpha", seed=11, nsteps=3, structures=(2, 1, 3),
                         pores=[{"step": 3, "result": pore(3, 1500.0, 7.5, "/runs/a/poreblazer_1")}],
                         extraFiles={"notes/odd name & more.txt": b"a file with an awkward name\n"})
    child, filesChild = writeRun(str(root / "child"), "alpha-child", parentRunId=a, nsteps=0,
                                 pores=[{"step": 2, "result": pore(2, 1400.0, 7.0, "/runs/child/poreblazer_2")}])
    b, filesB = writeRun(str(root / "b"), "beta", status="failed", seed=12, nsteps=2, xyzArtifact=True,
                         error="RuntimeError: deliberate failure")
    c, filesC = writeRun(str(root / "c"), "gamma", seed=13, nsteps=4,
                         pores=[{"step": 4, "result": pore(4, 2600.0, 11.0, "/runs/c/poreblazer_1")}])
    store = ObjectStore()
    store.ensureBucket()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        applySchema(conn)
        for name in ("a", "child", "b", "c"):
            uploadRun(RunDirectory(str(root / name)), store, conn)
    runs.update(a=a, child=child, b=b, c=c, files={a: filesA, child: filesChild, b: filesB, c: filesC},
                token=TOKEN)
    yield runs
    # Leave the database and bucket as they were (the demo shares them)
    allIds = [a, child, b, c]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM runs WHERE run_id = ANY(%s::uuid[])", (allIds,))
    for runId in allIds:
        listing = store.client.list_objects_v2(Bucket=store.bucket, Prefix=store.key(runId, ""))
        for obj in listing.get("Contents", []):
            store.client.delete_object(Bucket=store.bucket, Key=obj["Key"])
