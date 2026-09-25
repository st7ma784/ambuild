"""Load a run directory into PostgreSQL and object storage.

Uploads are idempotent: files already stored with the same sha256 are skipped and
rows are upserted on their natural keys, so a run can be uploaded any number of
times, including while it is still being written.
"""
import logging
import os
import re

from ambuild_ingest.rundir import sha256File

logger = logging.getLogger(__name__)

SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

POREBLAZER_COLUMNS = [
    ("system_volume_a3", "system_volume_A3"),
    ("system_mass_g_mol", "system_mass_g_mol"),
    ("system_density_g_cm3", "system_density_g_cm3"),
    ("helium_volume_a3", "helium_volume_A3"),
    ("helium_volume_cm3_g", "helium_volume_cm3_g"),
    ("geometric_volume_a3", "geometric_volume_A3"),
    ("geometric_volume_cm3_g", "geometric_volume_cm3_g"),
    ("surface_area_a2", "surface_area_A2"),
    ("surface_area_m2_cm3", "surface_area_m2_cm3"),
    ("surface_area_m2_g", "surface_area_m2_g"),
    ("pore_limiting_diameter_a", "pore_limiting_diameter_A"),
    ("maximum_pore_diameter_a", "maximum_pore_diameter_A"),
    ("percolated_dimensions", "percolated_dimensions"),
]


def applySchema(conn):
    with open(SCHEMA_FILE) as f:
        conn.execute(f.read())
    conn.commit()


def _slurmJobId(run):
    scheduler = run.get("scheduler") or {}
    return (scheduler.get("variables") or {}).get("SLURM_JOB_ID")


def _basename(path):
    """Last component of a path recorded on Windows or POSIX"""
    return re.split(r"[\\/]", path.rstrip("\\/"))[-1]


def uploadRun(rundir, store, conn, finalise=False):
    """Upload rundir (a RunDirectory); return a summary dict.

    finalise: record a run still marked running as incomplete. Use it once the
    process that wrote the run has ended, e.g. from a Slurm afterany job.
    """
    from psycopg.types.json import Jsonb

    run = rundir.run
    runId = rundir.runId
    events = rundir.events()
    status = run["status"]
    if finalise and status == "running":
        status = "incomplete"

    # What each file is, from the artifact events and run.json inputs
    kinds = {}
    for entry in run.get("inputs", []):
        kinds[entry["path"]] = ("input/" + entry["kind"], None)
    for _, event in events:
        if event["type"] == "artifact" and event["data"].get("relpath"):
            kinds[event["data"]["relpath"]] = (event["data"]["kind"], event["step"])

    # Objects first, so that rows never point at missing objects
    files = []
    uploaded = 0
    for relpath in rundir.files():
        localPath = rundir.localPath(relpath)
        sha256 = sha256File(localPath)
        if store.upload(runId, relpath, localPath, sha256):
            uploaded += 1
        kind, step = kinds.get(relpath, (None, None))
        files.append(
            (runId, relpath, kind, step, os.path.getsize(localPath), sha256, store.uri(runId, relpath))
        )

    with conn.transaction():
        conn.execute(
            """
            INSERT INTO runs (run_id, parent_run_id, status, started, finished, error,
                              ambuild_version, git_commit, slurm_job_id, run_json)
            VALUES (%s, %s, %s, %s::timestamptz, %s::timestamptz, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id) DO UPDATE SET
                parent_run_id = EXCLUDED.parent_run_id,
                status = EXCLUDED.status,
                started = EXCLUDED.started,
                finished = EXCLUDED.finished,
                error = EXCLUDED.error,
                ambuild_version = EXCLUDED.ambuild_version,
                git_commit = EXCLUDED.git_commit,
                slurm_job_id = EXCLUDED.slurm_job_id,
                run_json = EXCLUDED.run_json,
                last_uploaded = now()
            """,
            (
                runId,
                run.get("parent_run_id"),
                status,
                run.get("started"),
                run.get("finished"),
                run.get("error"),
                run.get("ambuild", {}).get("version"),
                run.get("ambuild", {}).get("git_commit"),
                _slurmJobId(run),
                Jsonb(run),
            ),
        )
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO events (run_id, seq, type, step, timestamp, data)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, seq) DO NOTHING
                """,
                [
                    (runId, seq, e["type"], e.get("step"), e.get("timestamp"), Jsonb(e["data"]))
                    for seq, e in events
                ],
            )
            cur.executemany(
                """
                INSERT INTO steps (run_id, step, type, step_time, total_time, num_frags,
                                   num_particles, num_blocks, density, num_free_endgroups,
                                   potential_energy, num_tries, file_count, fragment_types)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, step) DO NOTHING
                """,
                [
                    (
                        runId,
                        d["step"],
                        d["type"],
                        d.get("time"),
                        d.get("tot_time"),
                        d.get("num_frags"),
                        d.get("num_particles"),
                        d.get("num_blocks"),
                        d.get("density"),
                        d.get("num_free_endGroups"),
                        d.get("potential_energy"),
                        d.get("num_tries"),
                        d.get("file_count"),
                        d.get("fragment_types"),
                    )
                    for d in (e["data"] for _, e in events if e["type"] == "step")
                ],
            )
            cur.executemany(
                """
                INSERT INTO files (run_id, path, kind, step, size, sha256, uri)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, path) DO UPDATE SET
                    kind = EXCLUDED.kind, step = EXCLUDED.step, size = EXCLUDED.size,
                    sha256 = EXCLUDED.sha256, uri = EXCLUDED.uri
                """,
                files,
            )
            columns = ", ".join(c for c, _ in POREBLAZER_COLUMNS)
            placeholders = ", ".join(["%s"] * (len(POREBLAZER_COLUMNS) + 7))
            cur.executemany(
                """
                INSERT INTO pore_results (run_id, directory, step, returncode, version,
                                          psd, psd_cumulative, {0})
                VALUES ({1})
                ON CONFLICT (run_id, directory) DO NOTHING
                """.format(columns, placeholders),
                [
                    tuple(
                        [
                            runId,
                            _basename(e["data"]["directory"]),
                            e.get("step"),
                            e["data"].get("returncode"),
                            e["data"].get("version"),
                            Jsonb(e["data"].get("psd")),
                            Jsonb(e["data"].get("psd_cumulative")),
                        ]
                        + [e["data"].get(key) for _, key in POREBLAZER_COLUMNS]
                    )
                    for _, e in events
                    if e["type"] == "pore_result"
                ],
            )

    summary = {
        "run_id": runId,
        "status": status,
        "events": len(events),
        "files": len(files),
        "uploaded": uploaded,
    }
    logger.info("Uploaded run %s: %s", rundir.path, summary)
    return summary
