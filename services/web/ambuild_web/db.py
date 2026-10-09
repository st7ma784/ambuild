"""Read-only queries over the tables ambuild-upload fills (services/ingest schema.sql)."""
import contextlib
import posixpath
import uuid
from dataclasses import dataclass, field

import psycopg
from psycopg.rows import dict_row

from ambuild import conduction as ab_conduction
from ambuild import ionmap as ab_ionmap
from ambuild import xtb as ab_xtb

PAGE_SIZE = 50

# Poreblazer results shown for a run: its own, or its child runs' (a Slurm fan-out
# records one child run per checkpoint), latest step first
POREBLAZER_COLUMNS = [
    "surface_area_m2_g", "surface_area_a2", "surface_area_m2_cm3", "helium_volume_a3", "helium_volume_cm3_g",
    "geometric_volume_a3", "geometric_volume_cm3_g", "pore_limiting_diameter_a", "maximum_pore_diameter_a",
    "percolated_dimensions", "system_volume_a3", "system_mass_g_mol", "system_density_g_cm3",
]

SORTS = {  # public name -> SQL (whitelisted: never interpolate user input)
    "started": "started",
    "status": "status",
    "atoms": "num_particles",
    "surface_area": "surface_area_m2_g",
    "pld": "pore_limiting_diameter_a",
}


@contextlib.contextmanager
def connect(settings):
    timeoutMs = int(max(settings.check_timeout, 10) * 1000)
    with psycopg.connect(settings.database_url, row_factory=dict_row,
                         connect_timeout=max(1, int(settings.check_timeout)),
                         options="-c statement_timeout={0}".format(timeoutMs)) as conn:
        yield conn


def parseRunId(text):
    """A run id as a uuid string, or None if text is not one"""
    try:
        return str(uuid.UUID(str(text)))
    except (ValueError, TypeError):
        return None


@dataclass
class RunFilter:
    status: list = field(default_factory=list)  # e.g. ["finished", "failed"]
    q: str = ""  # run id prefix, or text in the command or host
    children: bool = False  # include child runs (Poreblazer tasks, resumes)
    min_surface_area: float = None
    max_surface_area: float = None
    min_pld: float = None
    max_pld: float = None
    with_poreblazer: bool = False
    sort: str = "started"
    descending: bool = True
    page: int = 1


def _summarySql():
    pore = ", ".join("p." + c for c in POREBLAZER_COLUMNS)
    return """
        SELECT r.run_id, r.parent_run_id, r.status, r.started, r.finished, r.error,
               r.ambuild_version, r.git_commit, r.slurm_job_id,
               r.run_json->'command' AS command,
               r.run_json->'environment'->>'hostname' AS host,
               r.run_json->'random'->>'seed' AS seed,
               r.run_json->'cell'->'box_dim' AS box,
               jsonb_path_query_first(r.run_json, '$.inputs[*] ? (@.kind == "script").path') #>> '{{}}' AS script,
               jsonb_path_query_first(r.run_json, '$.inputs[*] ? (@.kind == "recipe").name') #>> '{{}}' AS recipe,
               s.step AS last_step, s.num_particles, s.num_blocks, s.density,
               p.run_id AS pore_run_id, p.directory AS pore_directory, {pore},
               (SELECT count(*) FROM runs c WHERE c.parent_run_id = r.run_id) AS children,
               (SELECT jsonb_object_agg(latest.ion, latest.data) FROM (
                    SELECT DISTINCT ON (e.data->>'ion') e.data->>'ion' AS ion, e.data FROM events e
                    WHERE e.run_id = r.run_id AND e.type = 'ion_map_result' AND e.data->>'sites' IS NOT NULL
                    ORDER BY e.data->>'ion', e.step DESC NULLS LAST, e.seq DESC) latest) AS ion_maps,
               (SELECT e.data FROM events e
                    WHERE e.run_id = r.run_id AND e.type = 'conduction_result' AND e.data->>'sites' IS NOT NULL
                    ORDER BY e.step DESC NULLS LAST, e.seq DESC LIMIT 1) AS conduction,
               (SELECT e.data FROM events e
                    WHERE e.type = 'xtb_result' AND e.data->>'fmax_eV_A' IS NOT NULL
                      AND e.run_id IN (SELECT r.run_id UNION ALL
                                       SELECT c.run_id FROM runs c WHERE c.parent_run_id = r.run_id)
                    ORDER BY e.step DESC NULLS LAST, e.timestamp DESC NULLS LAST, e.seq DESC LIMIT 1) AS xtb
        FROM runs r
        LEFT JOIN LATERAL (
            SELECT step, num_particles, num_blocks, density FROM steps
            WHERE run_id = r.run_id ORDER BY step DESC LIMIT 1) s ON true
        LEFT JOIN LATERAL (
            SELECT pr.* FROM pore_results pr
            WHERE pr.run_id = r.run_id
               OR pr.run_id IN (SELECT c.run_id FROM runs c WHERE c.parent_run_id = r.run_id)
            ORDER BY pr.step DESC NULLS LAST, pr.directory DESC LIMIT 1) p ON true
    """.format(pore=pore)


def listRuns(conn, f):
    """(runs, total) for a RunFilter, one page"""
    where, args = [], []
    if not f.children:
        where.append("parent_run_id IS NULL")
    if f.status:
        where.append("status = ANY(%s)")
        args.append(list(f.status))
    if f.q:
        where.append("(run_id::text LIKE %s OR command::text ILIKE %s OR host ILIKE %s OR script ILIKE %s OR recipe ILIKE %s)")
        like = "%" + f.q + "%"
        args += [f.q.lower() + "%", like, like, like, like]
    for column, low, high in (("surface_area_m2_g", f.min_surface_area, f.max_surface_area),
                              ("pore_limiting_diameter_a", f.min_pld, f.max_pld)):
        if low is not None:
            where.append("{0} >= %s".format(column))
            args.append(low)
        if high is not None:
            where.append("{0} <= %s".format(column))
            args.append(high)
    if f.with_poreblazer:
        where.append("pore_run_id IS NOT NULL")
    order = SORTS.get(f.sort, "started")
    sql = "SELECT *, count(*) OVER () AS total FROM ({0}) runs {1} ORDER BY {2} {3} NULLS LAST, run_id LIMIT %s OFFSET %s".format(
        _summarySql(), ("WHERE " + " AND ".join(where)) if where else "", order,
        "DESC" if f.descending else "ASC")
    page = max(1, f.page)
    rows = conn.execute(sql, args + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
    total = rows[0]["total"] if rows else 0
    return [_label(r) for r in rows], total


def runSummaries(conn, runIds):
    rows = conn.execute("SELECT * FROM ({0}) runs WHERE run_id = ANY(%s::uuid[])".format(_summarySql()),
                        (list(runIds),)).fetchall()
    byId = {str(r["run_id"]): _label(r) for r in rows}
    return [byId[i] for i in runIds if i in byId]


def getRun(conn, runId):
    """Everything about one run, or None"""
    row = conn.execute("SELECT * FROM runs WHERE run_id = %s", (runId,)).fetchone()
    if row is None:
        return None
    summary = runSummaries(conn, [runId])[0]
    children = conn.execute(
        "SELECT * FROM ({0}) runs WHERE parent_run_id = %s ORDER BY started, run_id".format(_summarySql()),
        (runId,)).fetchall()
    parent = runSummaries(conn, [str(row["parent_run_id"])])[0] if row["parent_run_id"] else None
    return {
        "run": row,
        "summary": summary,
        "parent": parent,
        "children": [_label(c) for c in children],
        "steps": steps(conn, runId),
        "pore_results": poreResults(conn, runId),
        "ion_maps": ionMaps(conn, runId),
        "conduction": conductionResults(conn, runId),
        "xtb": xtbResults(conn, runId),
        "files": files(conn, runId),
        "structures": structures(conn, runId),
        "event_counts": dict(
            (r["type"], r["n"]) for r in conn.execute(
                "SELECT type, count(*) AS n FROM events WHERE run_id = %s GROUP BY type ORDER BY type",
                (runId,)).fetchall()),
    }


def steps(conn, runId):
    return conn.execute("SELECT * FROM steps WHERE run_id = %s ORDER BY step", (runId,)).fetchall()


def ionMaps(conn, runId):
    """The run's ion maps (ion_map_result events: ambuild.ionmap), by step and ion"""
    rows = conn.execute(
        "SELECT step, data FROM events WHERE run_id = %s AND type = 'ion_map_result' ORDER BY step, seq",
        (runId,)).fetchall()
    return [dict(r["data"], step=r["step"]) for r in rows]


def conductionResults(conn, runId):
    """The run's conduction results (conduction_result events: ambuild.conduction), by step"""
    rows = conn.execute(
        "SELECT step, data FROM events WHERE run_id = %s AND type = 'conduction_result' ORDER BY step, seq",
        (runId,)).fetchall()
    return [dict(r["data"], step=r["step"]) for r in rows]


def xtbResults(conn, runId):
    """The xTB checks of the run and of its child runs (xtb_result events: ambuild.xtb; a
    Slurm fan-out records one child run per checkpoint checked), by step. Each names the
    run that holds its files"""
    rows = conn.execute(
        "SELECT e.run_id, e.step, e.data, (e.run_id <> %s) AS from_child FROM events e "
        "WHERE e.type = 'xtb_result' AND e.run_id IN "
        "(SELECT %s::uuid UNION ALL SELECT run_id FROM runs WHERE parent_run_id = %s) "
        "ORDER BY e.step NULLS LAST, e.timestamp NULLS LAST, e.seq", (runId, runId, runId)).fetchall()
    return [dict(r["data"], step=r["step"], run_id=str(r["run_id"]), from_child=r["from_child"]) for r in rows]


def poreResults(conn, runId, withChildren=True):
    """Poreblazer results of the run and, by default, of its child runs"""
    return conn.execute(
        "SELECT p.*, (p.run_id <> %s) AS from_child FROM pore_results p "
        "WHERE p.run_id = %s OR (%s AND p.run_id IN (SELECT run_id FROM runs WHERE parent_run_id = %s)) "
        "ORDER BY p.step NULLS LAST, p.directory",
        (runId, runId, withChildren, runId)).fetchall()


def files(conn, runId):
    return conn.execute("SELECT path, kind, step, size, sha256 FROM files WHERE run_id = %s ORDER BY path",
                        (runId,)).fetchall()


def structures(conn, runId):
    """The run's viewable structures, by step: the extended XYZ files dump() writes
    ("structure" artifacts); for runs recorded before those, its XYZ artifacts"""
    rows = conn.execute(
        "SELECT path, kind, step, size FROM files WHERE run_id = %s AND kind = 'structure' "
        "ORDER BY step NULLS LAST, path", (runId,)).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT path, kind, step, size FROM files WHERE run_id = %s AND kind = 'xyz' "
            "AND path NOT LIKE 'inputs/%%' ORDER BY step NULLS LAST, path", (runId,)).fetchall()
    return rows


def fileRow(conn, runId, path):
    return conn.execute("SELECT * FROM files WHERE run_id = %s AND path = %s", (runId, path)).fetchone()


def events(conn, runId, etype=None, offset=0, limit=200):
    args = [runId]
    typeClause = ""
    if etype:
        typeClause = "AND type = %s"
        args.append(etype)
    return conn.execute(
        "SELECT seq, type, step, timestamp, data FROM events WHERE run_id = %s {0} "
        "ORDER BY seq OFFSET %s LIMIT %s".format(typeClause),
        args + [max(0, offset), max(1, min(limit, 1000))]).fetchall()


def _label(row):
    """A short name for a run: its script, else its recipe, else its command"""
    row = dict(row)
    row["run_id"] = str(row["run_id"])
    if row.get("parent_run_id"):
        row["parent_run_id"] = str(row["parent_run_id"])
    if row.get("pore_run_id"):
        row["pore_run_id"] = str(row["pore_run_id"])
    row.update(ab_ionmap.metrics(row.get("ion_maps")))  # li_escape_barrier etc., from the latest map of each ion
    row.update(ab_conduction.metrics(row.get("conduction")))  # el_gap etc., from the latest conduction result
    row.update(ab_xtb.metrics(row.get("xtb")))  # xtb_fmax etc., from the latest xTB check, its own or a child run's
    command = row.get("command") or []
    script = row.get("script")
    if script:
        row["label"] = posixpath.basename(script)
    elif row.get("recipe"):
        row["label"] = row["recipe"]
    elif command:
        row["label"] = posixpath.basename(str(command[0])) + (" " + " ".join(map(str, command[1:])) if len(command) > 1 else "")
    else:
        row["label"] = row["run_id"][:8]
    if row.get("started") and row.get("finished"):
        row["duration"] = (row["finished"] - row["started"]).total_seconds()
    else:
        row["duration"] = None
    return row
