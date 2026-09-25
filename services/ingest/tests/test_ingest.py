"""Integration tests against PostgreSQL and S3-compatible storage.

Run with DATABASE_URL, AMBUILD_S3_BUCKET, S3_ENDPOINT_URL and AWS credentials set,
e.g. through deploy/docker-compose.yml (see deploy/README.md).
"""
import csv
import os

import pytest

from ambuild_ingest import cli
from ambuild_ingest.ingest import applySchema, uploadRun
from ambuild_ingest.rundir import RunDirectory
from ambuild_ingest.store import ObjectStore

pytestmark = pytest.mark.skipif(
    not (os.environ.get("DATABASE_URL") and os.environ.get("AMBUILD_S3_BUCKET")),
    reason="needs DATABASE_URL and AMBUILD_S3_BUCKET",
)


@pytest.fixture
def conn():
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        applySchema(conn)
        yield conn


@pytest.fixture
def store():
    store = ObjectStore()
    store.ensureBucket()
    return store


def count(conn, table, runId):
    return conn.execute(
        "SELECT count(*) FROM {0} WHERE run_id = %s".format(table), (runId,)
    ).fetchone()[0]


def test_upload_is_idempotent(runDir, conn, store):
    path, runId = runDir()
    first = uploadRun(RunDirectory(path), store, conn)
    second = uploadRun(RunDirectory(path), store, conn)

    assert first["uploaded"] == first["files"] == 6
    assert second["uploaded"] == 0
    for table, n in [("events", 5), ("steps", 1), ("files", 6), ("pore_results", 1)]:
        assert count(conn, table, runId) == n, table

    status, slurm, version = conn.execute(
        "SELECT status, slurm_job_id, ambuild_version FROM runs WHERE run_id = %s", (runId,)
    ).fetchone()
    assert (status, slurm, version) == ("finished", "42", "2.0.1")

    kinds = dict(
        conn.execute("SELECT path, kind FROM files WHERE run_id = %s", (runId,)).fetchall()
    )
    assert kinds["step_1.pkl.gz"] == "pickle"
    assert kinds["inputs/params/bond_params.csv"] == "input/params"
    assert "poreblazer_1/nitrogen_network.grd" not in kinds

    directory, sa, pld, psd = conn.execute(
        "SELECT directory, surface_area_m2_g, pore_limiting_diameter_a, psd "
        "FROM pore_results WHERE run_id = %s",
        (runId,),
    ).fetchone()
    assert (directory, sa, pld) == ("poreblazer_1", 13429.83, 9.59)
    assert psd == [[4.625, 0.0002], [4.875, 0.0006]]

    head = store.client.head_object(Bucket=store.bucket, Key=store.key(runId, "step_1.pkl.gz"))
    assert head["ContentLength"] == len("pickle")


def test_finalise_marks_running_run_incomplete(runDir, conn, store):
    path, runId = runDir(status="running")
    assert uploadRun(RunDirectory(path), store, conn)["status"] == "running"
    assert uploadRun(RunDirectory(path), store, conn, finalise=True)["status"] == "incomplete"
    assert conn.execute("SELECT status FROM runs WHERE run_id = %s", (runId,)).fetchone()[0] == (
        "incomplete"
    )


def test_real_ambuild_run_with_child(tmp_path, conn, store):
    """Record a build and a child run with Ambuild, then upload both with the CLI"""
    ab_cell = pytest.importorskip("ambuild.ab_cell")
    from ambuild import ab_util

    testsDir = os.environ.get("AMBUILD_TESTS_DIR", "/ambuild/tests")
    params = os.path.join(testsDir, "params")
    rundir = str(tmp_path / "run")
    with ab_cell.Cell([20, 20, 20], paramsDir=params, outputDir=rundir, recordRun=True) as cell:
        cell.libraryAddFragment(os.path.join(testsDir, "blocks", "ch4.car"), fragmentType="A")
        cell.addBondType("A:a-A:a")
        cell.seed(3)
        cell.growBlocks(2)
        pkl = cell.dump()
    child = ab_util.cellFromPickle(pkl, paramsDir=params, outputDir=os.path.join(rundir, "child"))
    childId = child.startRecording()
    child.growBlocks(1)
    child.close()

    assert cli.main(["--finalise", "--recursive", rundir]) == 0

    rows = dict(
        conn.execute(
            "SELECT run_id::text, parent_run_id::text FROM runs WHERE run_id IN (%s, %s)",
            (cell.runId, childId),
        ).fetchall()
    )
    assert rows == {cell.runId: None, childId: cell.runId}
    with open(os.path.join(rundir, "ambuild.csv"), newline="") as f:
        assert count(conn, "steps", cell.runId) == len(list(csv.DictReader(f)))
    paths = [
        p for (p,) in conn.execute("SELECT path FROM files WHERE run_id = %s", (cell.runId,))
    ]
    assert "step_1.pkl.gz" in paths
    assert not any(p.startswith("child/") for p in paths)
    assert os.path.isfile(os.path.join(rundir, cli.MARKER_FILE))
