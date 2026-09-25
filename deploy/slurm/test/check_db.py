"""Check the uploaded runs for run_test.sh: check_db.py OK_RUN FAILED_RUN CANCELLED_RUN"""
import os
import sys

import psycopg

ok, failed, cancelled = sys.argv[1:4]
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
print("Slurm end-to-end test passed")
