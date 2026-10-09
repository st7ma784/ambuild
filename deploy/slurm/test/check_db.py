"""Check the uploaded runs for run_test.sh:

    check_db.py OK_RUN FAILED_RUN CANCELLED_RUN MULTITASK_RUN XTB_RUN
"""
import os
import sys

import psycopg

ok, failed, cancelled, multitask, xtb = sys.argv[1:6]
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    def one(sql, *args):
        return conn.execute(sql, args).fetchone()

    status, slurm = one("SELECT status, slurm_job_id FROM runs WHERE run_id = %s", ok)
    assert status == "finished" and slurm, (status, slurm)
    steps = one("SELECT count(*) FROM steps WHERE run_id = %s", ok)[0]
    pickles = one("SELECT count(*) FROM files WHERE run_id = %s AND kind = 'pickle'", ok)[0]
    children = conn.execute(
        "SELECT run_id, status, run_json->'scheduler'->'variables'->>'SLURM_ARRAY_TASK_ID' "
        "FROM runs WHERE parent_run_id = %s", (ok,)).fetchall()
    assert pickles == 3 and len(children) == 3, (pickles, children)
    assert all(c[1] == "finished" and c[2] is not None for c in children), children
    pore = conn.execute(
        "SELECT p.surface_area_m2_g, p.pore_limiting_diameter_a FROM pore_results p "
        "JOIN runs r ON r.run_id = p.run_id WHERE r.parent_run_id = %s", (ok,)).fetchall()
    assert len(pore) == 3 and all(sa is not None and sa > 0 for sa, _ in pore), pore
    print("build run {0}: finished, {1} steps, {2} pickles".format(ok, steps, pickles))
    print("  poreblazer child runs: {0}".format(sorted(str(c[0]) for c in children)))
    print("  surface areas (m^2/g): {0}".format(sorted(round(p[0], 1) for p in pore)))

    status, error = one("SELECT status, error FROM runs WHERE run_id = %s", failed)
    assert status == "failed" and "deliberate failure" in error, (status, error)
    print("failing run {0}: {1} ({2})".format(failed, status, error))

    status = one("SELECT status FROM runs WHERE run_id = %s", cancelled)[0]
    assert status == "incomplete", status
    print("cancelled run {0}: {1}".format(cancelled, status))

    status, ntasks = one(
        "SELECT status, run_json->'scheduler'->'variables'->>'SLURM_NTASKS' FROM runs "
        "WHERE run_id = %s", multitask)
    assert (status, ntasks) == ("finished", "2"), (status, ntasks)
    print("two-task run {0}: {1}, HOOMD launcher started 2 ranks".format(multitask, status))

    # the xTB fan-out: a child run per pickle, each with its result, settings and files
    children = conn.execute(
        "SELECT r.run_id, r.status, r.run_json->'scheduler'->'variables'->>'SLURM_ARRAY_TASK_ID', e.data "
        "FROM runs r JOIN events e ON e.run_id = r.run_id AND e.type = 'xtb_result' "
        "WHERE r.parent_run_id = %s", (xtb,)).fetchall()
    assert len(children) == 3, children
    assert all(c[1] == "finished" and c[2] is not None for c in children), children
    for _, _, _, data in children:
        assert (data["returncode"], data["converged"], data["mode"]) == (0, True, "relax"), data
        assert data["relax"]["max_steps"] == 7 and data["threads"] == 1, data
        assert data["memory_estimate_mb"] > 150, data
    kinds = {k for (k,) in conn.execute(
        "SELECT DISTINCT f.kind FROM files f JOIN runs r ON r.run_id = f.run_id WHERE r.parent_run_id = %s", (xtb,))}
    assert {"xtb", "xtb_relaxed", "xtb_structure", "xtb_topology"} <= kinds, kinds
    print("xTB run {0}: {1} child runs, atoms {2}".format(
        xtb, len(children), sorted(c[3]["atoms"] for c in children)))
print("Slurm end-to-end test passed")
