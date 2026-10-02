"""Log recorded runs to MLflow: the recipe's settings as parameters, the final properties as
metrics, the recipe and run.json as artifacts (docs/mlflow.md).

One MLflow run per Ambuild run, in the experiment "ambuild/<recipe name>" ("ambuild/scripts"
for runs from Python scripts), found again by its tag ambuild.run_id, so logging is
idempotent: a run logged at its current status is skipped. Runs still running are not
logged.

- **Parameters:** the recipe flattened by JSON pointer, the same paths sweeps and campaigns
  vary ("cell/box", "stages/1/stages/0/count"), with blocks, joins and the parameter set by
  name; and the seed.
- **Metrics:** final_* from the last build step (density, atoms, blocks, free end groups,
  potential energy, build time), the latest Poreblazer result, the latest ion map of each
  ion (li_, na_, k_), the latest conduction result (el_); and the build's history per step
  (step/density, step/num_particles, step/num_blocks).

Configuration: MLFLOW_TRACKING_URI (none: nothing is logged) and AMBUILD_PUBLIC_URL (optional:
a link back to each run's page). MLflow being down never fails an upload: a warning is
logged instead. Needs mlflow-skinny (pip install "ambuild-ingest[mlflow]").
"""
import contextlib
import io
import json
import logging
import os
import tempfile
import urllib.request
from datetime import datetime

logger = logging.getLogger(__name__)

EXPERIMENT_PREFIX = "ambuild/"
SCRIPTS_EXPERIMENT = "ambuild/scripts"
RUN_TAG = "ambuild.run_id"
LOGGED_TAG = "ambuild.mlflow_logged"  # the status the run was logged at
MAX_VALUE = 500  # MLflow's oldest supported parameter value length
STATUS = {"finished": "FINISHED", "failed": "FAILED"}  # others (incomplete, cancelled): KILLED

POREBLAZER_KEYS = [
    "system_volume_A3", "system_mass_g_mol", "system_density_g_cm3", "helium_volume_A3", "helium_volume_cm3_g",
    "geometric_volume_A3", "geometric_volume_cm3_g", "surface_area_A2", "surface_area_m2_cm3", "surface_area_m2_g",
    "pore_limiting_diameter_A", "maximum_pore_diameter_A", "percolated_dimensions",
]
ION_PREFIXES = {"Li+": "li", "Na+": "na", "K+": "k"}
ION_FIELDS = ["site_energy", "escape_barrier", "lowest_barrier", "sites"]
CONDUCTION_FIELDS = [
    "gap", "conductance", "conductance_min", "conjugated_conductance", "tunnelling_share",
    "largest_domain_fraction", "radical_domains", "log_transmission", "log_transmission_min",
    "log_hopping", "log_hopping_min",
]
STEP_FIELDS = {"density": "density", "num_particles": "num_particles", "num_blocks": "num_blocks",
               "num_free_endGroups": "num_free_endgroups", "potential_energy": "potential_energy",
               "tot_time": "build_seconds"}
STEP_HISTORY = ["density", "num_particles", "num_blocks"]


def trackingUri():
    return os.environ.get("MLFLOW_TRACKING_URI") or None


def reachable(uri, timeout=3.0):
    """Whether the tracking server answers its health check"""
    try:
        with urllib.request.urlopen(uri.rstrip("/") + "/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)) and all(not isinstance(x, (dict, list)) for x in v):
        text = ",".join(_value(x) for x in v)
    elif isinstance(v, (dict, list)):
        text = json.dumps(v, sort_keys=True)
    else:
        text = str(v)
    return text if len(text) <= MAX_VALUE else text[:MAX_VALUE - 1] + "…"


def _flatten(value, path, out):
    if isinstance(value, dict):
        for k in sorted(value):
            _flatten(value[k], path + [str(k)], out)
    elif isinstance(value, list) and any(isinstance(x, (dict, list)) for x in value):
        for i, x in enumerate(value):
            _flatten(x, path + [str(i)], out)
    else:
        out["/".join(path)] = _value(value)


def recipeParams(recipe):
    """{name: value} for a recipe: everything that changes the build, flattened by JSON
    pointer; blocks, joins and the parameter set summarised by name"""
    out = {}
    fragments = recipe.get("fragments") or []
    out["blocks"] = _value([f.get("name") or f.get("type") for f in fragments])
    for f in fragments:
        out["fragments/{0}".format(f.get("type"))] = _value(f.get("name") or f.get("car"))
    out["bond_types"] = _value(recipe.get("bond_types") or [])
    params = recipe.get("params") or {}
    if params:
        first = str(next(iter(params.values())))
        out["params"] = first.replace("\\", "/").rsplit("/", 2)[-2] if "/" in first.replace("\\", "/") else first
    for key in ("cell", "stages", "max_bonds", "resources"):
        if key in recipe:
            _flatten(recipe[key], [key], out)
    if recipe.get("seed") is not None:
        out["recipe_seed"] = _value(recipe["seed"])
    out["recipe_version"] = _value(recipe.get("recipe_version"))
    return out


def finalMetrics(events):
    """{name: value} of the run's final properties, and [(name, value, step)] of its history"""
    metrics, history = {}, []
    steps = [e["data"] for _, e in events if e["type"] == "step"]
    if steps:
        last = steps[-1]
        for key, name in STEP_FIELDS.items():
            if isinstance(last.get(key), (int, float)):
                metrics["final_" + name] = last[key]
        for d in steps:
            for key in STEP_HISTORY:
                if isinstance(d.get(key), (int, float)):
                    history.append(("step/" + key, d[key], int(d["step"])))
    pores = [e["data"] for _, e in events if e["type"] == "pore_result" and e["data"].get("returncode") in (0, None)]
    if pores:
        for key in POREBLAZER_KEYS:
            if isinstance(pores[-1].get(key), (int, float)):
                metrics[key.lower()] = pores[-1][key]
    ions = {}
    for _, e in events:
        if e["type"] == "ion_map_result" and e["data"].get("sites") is not None:
            ions[e["data"].get("ion")] = e["data"]
    for ion, data in ions.items():
        prefix = ION_PREFIXES.get(ion)
        if prefix:
            for f in ION_FIELDS:
                if isinstance(data.get(f), (int, float)):
                    metrics["{0}_{1}".format(prefix, f)] = data[f]
    conduction = [e["data"] for _, e in events if e["type"] == "conduction_result" and e["data"].get("sites") is not None]
    if conduction:
        for f in CONDUCTION_FIELDS:
            v = conduction[-1].get(f)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                metrics["el_" + f] = v
    metrics.update(derivedMetrics(metrics))
    return metrics, history


def derivedMetrics(m):
    """Metrics computed from the others, for comparing runs of different sizes and shapes:
    void_fraction (helium-accessible volume over the cell's), pore_window_ratio (pore limiting
    over largest pore diameter: 1 is a uniform channel, near 0 cages behind narrow windows),
    single_framework (one block: 1), el_spans_all (conducts along all three axes: 1),
    free_end_groups_per_1000_atoms (how unfinished the network is), build_ms_per_atom"""
    out = {}

    def has(*keys):
        return all(isinstance(m.get(k), (int, float)) for k in keys)

    if has("helium_volume_a3", "system_volume_a3") and m["system_volume_a3"] > 0:
        out["void_fraction"] = m["helium_volume_a3"] / m["system_volume_a3"]
    if has("pore_limiting_diameter_a", "maximum_pore_diameter_a") and m["maximum_pore_diameter_a"] > 0:
        out["pore_window_ratio"] = m["pore_limiting_diameter_a"] / m["maximum_pore_diameter_a"]
    if has("final_num_blocks"):
        out["single_framework"] = 1.0 if m["final_num_blocks"] == 1 else 0.0
    if has("el_conductance_min"):
        out["el_spans_all"] = 1.0 if m["el_conductance_min"] > 0 else 0.0
    if has("final_num_free_endgroups", "final_num_particles") and m["final_num_particles"] > 0:
        out["free_end_groups_per_1000_atoms"] = 1000.0 * m["final_num_free_endgroups"] / m["final_num_particles"]
    if has("final_build_seconds", "final_num_particles") and m["final_num_particles"] > 0:
        out["build_ms_per_atom"] = 1000.0 * m["final_build_seconds"] / m["final_num_particles"]
    return out


def _millis(text):
    if not text:
        return None
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def recipeInput(run):
    """The run.json inputs entry for its recipe, or None (runs from scripts)"""
    return next((i for i in run.get("inputs", []) if i.get("kind") == "recipe"), None)


class Logger:
    """Logs runs to one MLflow tracking server"""

    def __init__(self, uri=None, webUrl=None, client=None):
        self.uri = uri or trackingUri()
        self.webUrl = (webUrl if webUrl is not None else os.environ.get("AMBUILD_PUBLIC_URL", "")).rstrip("/")
        if client is None:
            os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "2")
            os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "20")
            from mlflow.tracking import MlflowClient

            client = MlflowClient(tracking_uri=self.uri)
        self.client = client
        self.experiments = {}

    def experimentId(self, name):
        if name not in self.experiments:
            found = self.client.get_experiment_by_name(name)
            self.experiments[name] = found.experiment_id if found else self.client.create_experiment(name)
        return self.experiments[name]

    def existing(self, experimentId, runId):
        runs = self.client.search_runs([experimentId], filter_string="tags.`{0}` = '{1}'".format(RUN_TAG, runId),
                                       max_results=1)
        return runs[0] if runs else None

    def log(self, run, events, recipe=None):
        """Log one Ambuild run (its run.json, [(seq, event)] and recipe dict, or None for a
        script); returns the MLflow run id, or None if it was skipped"""
        with contextlib.redirect_stdout(io.StringIO()):  # MLflow prints a "View run" line per run
            return self._log(run, events, recipe)

    def _log(self, run, events, recipe):
        status = run.get("status")
        if status == "running":
            return None
        entry = recipeInput(run)
        name = (entry or {}).get("name")
        experiment = EXPERIMENT_PREFIX + name if name else SCRIPTS_EXPERIMENT
        experimentId = self.experimentId(experiment)
        runId = run["run_id"]
        found = self.existing(experimentId, runId)
        if found is not None and found.data.tags.get(LOGGED_TAG) == status:
            return None
        tags = {RUN_TAG: runId, "ambuild.status": status, "ambuild.version": (run.get("ambuild") or {}).get("version"),
                "ambuild.git_commit": (run.get("ambuild") or {}).get("git_commit"),
                "ambuild.parent_run_id": run.get("parent_run_id"),
                "ambuild.recipe": name, "ambuild.recipe_sha256": (entry or {}).get("recipe_sha256"),
                "ambuild.host": ((run.get("environment") or {}).get("hostname")),
                "ambuild.slurm_job_id": ((run.get("scheduler") or {}).get("variables") or {}).get("SLURM_JOB_ID")}
        if self.webUrl:
            tags["ambuild.url"] = "{0}/runs/{1}".format(self.webUrl, runId)
        if run.get("error"):
            tags["ambuild.error"] = _value(run["error"])
        tags = {k: str(v) for k, v in tags.items() if v not in (None, "")}
        if found is None:
            created = self.client.create_run(experimentId, start_time=_millis(run.get("started")), tags=tags,
                                             run_name="{0} {1}".format(name or "script", runId[:8]))
            mlflowId = created.info.run_id
        else:
            mlflowId = found.info.run_id
            for k, v in tags.items():
                self.client.set_tag(mlflowId, k, v)
        from mlflow.entities import Metric, Param

        params = recipeParams(recipe) if recipe else {}
        seed = (run.get("random") or {}).get("seed")
        if seed is not None:
            params["seed"] = _value(seed)
        if found is None:  # parameters can't change once logged
            items = [Param(k, v) for k, v in sorted(params.items())]
            for i in range(0, len(items), 100):
                self.client.log_batch(mlflowId, params=items[i:i + 100])
        metrics, history = finalMetrics(events)
        stamp = _millis(run.get("finished")) or _millis(run.get("started")) or 0
        items = [Metric(k, float(v), stamp, 0) for k, v in sorted(metrics.items())]
        items += [Metric(k, float(v), stamp, s) for k, v, s in history]
        for i in range(0, len(items), 1000):
            self.client.log_batch(mlflowId, metrics=items[i:i + 1000])
        with tempfile.TemporaryDirectory() as tmp:
            for fileName, content in (("run.json", run), ("recipe.json", recipe)):
                if content is not None:
                    path = os.path.join(tmp, fileName)
                    with open(path, "w") as f:
                        json.dump(content, f, indent=1)
                    self.client.log_artifact(mlflowId, path)
        self.client.set_tag(mlflowId, LOGGED_TAG, status)
        self.client.set_terminated(mlflowId, status=STATUS.get(status, "KILLED"), end_time=_millis(run.get("finished")))
        return mlflowId


def readRecipe(rundir):
    """The recipe dict recorded in a run directory, or None"""
    entry = recipeInput(rundir.run)
    if not entry:
        return None
    path = rundir.localPath(entry["path"])
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f)


def backfill(conn, store, mlflowLogger, limit=None):
    """Log every run in the database not yet logged at its status; returns (logged, skipped,
    failed). Recipes are read back from object storage"""
    rows = conn.execute(
        "SELECT run_id, run_json FROM runs WHERE status <> 'running' ORDER BY started NULLS LAST"
        + (" LIMIT %s" % int(limit) if limit else "")).fetchall()
    logged = skipped = failed = 0
    for runId, run in rows:
        events = [(r[0], {"type": r[1], "step": r[2], "data": r[3]}) for r in conn.execute(
            "SELECT seq, type, step, data FROM events WHERE run_id = %s ORDER BY seq", (runId,)).fetchall()]
        recipe = None
        entry = recipeInput(run)
        if entry:
            try:
                body = store.client.get_object(Bucket=store.bucket, Key=store.key(str(runId), entry["path"]))["Body"]
                recipe = json.loads(body.read())
            except Exception:
                logger.warning("No recipe in object storage for run %s", runId)
        try:
            if mlflowLogger.log(run, events, recipe) is None:
                skipped += 1
            else:
                logged += 1
        except Exception:
            logger.exception("Could not log run %s to MLflow", runId)
            failed += 1
    return logged, skipped, failed
