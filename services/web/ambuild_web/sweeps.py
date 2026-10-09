"""Sweeps: one recipe over a grid of parameter values, rows from a CSV file, or a list of
seeds (ambuild.sweep); each run is a submission, and a Slurm agent submits a sweep's runs
as one array job. Pages and API."""
import json
import statistics

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ambuild import conduction as ab_conduction
from ambuild import ionmap as ab_ionmap
from ambuild import sweep as ab_sweep
from ambuild import xtb as ab_xtb
from ambuild_web import db, queue
from ambuild_web.submissions import _connect, _int, _owner, checkRecipe, recipeText

router = APIRouter()

# Results a sweep page can plot: (column of the run summary, label)
METRICS = [
    ("surface_area_m2_g", "surface area (m²/g)"),
    ("pore_limiting_diameter_a", "pore limiting diameter (Å)"),
    ("maximum_pore_diameter_a", "maximum pore diameter (Å)"),
    ("helium_volume_cm3_g", "helium volume (cm³/g)"),
    ("density", "density (g/cm³)"),
    ("num_particles", "atoms"),
    ("num_blocks", "blocks"),
] + [(m, ab_ionmap.metricLabel(m)) for m in ab_ionmap.METRICS]  # from ion_map stages (liminal)
METRICS += [(m, ab_conduction.metricLabel(m)) for m in ab_conduction.METRICS]  # from conduction stages (liminal)
METRICS += [(m, ab_xtb.metricLabel(m)) for m in ab_xtb.METRICS]  # from xtb stages and xTB fan-outs
PREVIEW_ROWS = 50
DEFAULT_PARAMETERS = [{"name": "box", "path": "/cell/box", "all": True, "values": [25, 30, 35]}]


def _build(conn, recipe, spec):
    """(runs, errors) for a sweep of recipe"""
    errors = checkRecipe(conn, recipe) if isinstance(recipe, dict) else ["recipe: must be a JSON object"]
    if errors:
        return None, ["base recipe: " + e for e in errors]
    try:
        return ab_sweep.expand(recipe, spec), []
    except ab_sweep.SweepError as exc:
        return None, exc.errors


def _preview(runs):
    return {"runs": len(runs), "points": len({json.dumps(r["point"], sort_keys=True) for r in runs}),
            "first": [{"point": r["point"], "seed": r["seed"]} for r in runs[:PREVIEW_ROWS]]}


def _create(request, conn, name, recipe, spec, backend, priority, owner):
    if backend not in queue.BACKENDS:
        return None, ["backend: must be one of {0}".format(", ".join(queue.BACKENDS))]
    runs, errors = _build(conn, recipe, spec)
    if errors:
        return None, errors
    row = dict(queue.createSweep(conn, (name or "").strip() or recipe["name"], recipe, spec, runs, backend,
                                 _owner(request, owner), priority), runs=len(runs))
    return row, []


# --- results

def _mean(values):
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def sweepResults(conn, sweepId):
    """The sweep's runs with their results (from the run summaries), in order"""
    runs = [dict(r) for r in queue.sweepRuns(conn, sweepId)]
    summaries = {s["run_id"]: s for s in db.runSummaries(conn, [str(r["run_id"]) for r in runs if r["uploaded"]])}
    for r in runs:
        r["run_id"] = str(r["run_id"])
        s = summaries.get(r["run_id"], {})
        r["results"] = {column: s.get(column) for column, _ in METRICS}
        r["run_status"] = s.get("status")
    return runs


def sweepCharts(spec, runs, metric):
    """For each numeric parameter, the metric (mean over seeds) against it, one series per
    combination of the other parameters' values"""
    label = dict(METRICS)[metric]
    names = [p["name"] for p in spec.get("parameters", [])]
    charts = []
    for name in names:
        others = [n for n in names if n != name]
        groups = {}
        for r in runs:
            x = r["point"].get(name)
            if not isinstance(x, (int, float)) or isinstance(x, bool):
                continue
            key = ", ".join("{0}={1}".format(n, json.dumps(r["point"].get(n))) for n in others)
            groups.setdefault(key, {}).setdefault(x, []).append(r["results"].get(metric))
        series = []
        for key, byX in sorted(groups.items()):
            xs = sorted(byX)
            ys = [_mean(byX[x]) for x in xs]
            if any(y is not None for y in ys):
                series.append({"label": key or label, "x": xs, "y": ys})
        if series:
            charts.append({"id": "sweep-" + name, "title": "{0} against {1}".format(label, name), "xlabel": name,
                           "ylabel": label, "series": series, "legend": len(series) > 1})
    return charts


# --- API

def _specFrom(payload):
    return {k: payload[k] for k in ("parameters", "rows", "seeds") if k in payload}


@router.post("/api/sweeps", tags=["sweeps"], status_code=201)
def apiCreateSweep(request: Request, payload: dict = Body(...)):
    """Queue a sweep: {"recipe": {...}} or {"recipe_id": n}; "parameters" (each {"name",
    "path" (a JSON pointer into the recipe), "all", "values"}), or "rows" of values; "seeds";
    "name", "backend", "priority", "owner". With "preview": true, only lists the runs."""
    with _connect(request) as conn:
        recipe = payload.get("recipe")
        if recipe is None and payload.get("recipe_id") is not None:
            saved = queue.getRecipe(conn, _int(payload["recipe_id"], "recipe_id"))
            if saved is None:
                raise HTTPException(404, "No such recipe")
            recipe = saved["body"]
        spec = _specFrom(payload)
        if payload.get("preview"):
            runs, errors = _build(conn, recipe, spec)
            if errors:
                return JSONResponse({"valid": False, "errors": errors}, status_code=422)
            return JSONResponse(jsonable_encoder(dict(_preview(runs), valid=True)))
        row, errors = _create(request, conn, payload.get("name"), recipe, spec, payload.get("backend", "local"),
                              _int(payload.get("priority"), "priority", 0), payload.get("owner"))
    if errors:
        return JSONResponse({"valid": False, "errors": errors}, status_code=422)
    return jsonable_encoder({"sweep_id": row["sweep_id"], "name": row["name"], "runs": row["runs"]})


@router.get("/api/sweeps", tags=["sweeps"])
def apiSweeps(request: Request):
    """Sweeps, newest first, with their runs counted by state"""
    with _connect(request) as conn:
        return jsonable_encoder({"sweeps": queue.listSweeps(conn)})


def _sweepOr404(conn, sweepId):
    row = queue.getSweep(conn, sweepId)
    if row is None:
        raise HTTPException(404, "No such sweep")
    return row


@router.get("/api/sweeps/{sweepId}", tags=["sweeps"])
def apiSweep(request: Request, sweepId: int):
    """A sweep: its recipe and spec, and each run's point, seed, state and results"""
    with _connect(request) as conn:
        row = _sweepOr404(conn, sweepId)
        runs = sweepResults(conn, sweepId)
    return jsonable_encoder({"sweep": row, "runs": runs})


@router.post("/api/sweeps/{sweepId}/cancel", tags=["sweeps"])
def apiCancelSweep(request: Request, sweepId: int):
    """Cancel every unfinished run of the sweep"""
    with _connect(request) as conn:
        _sweepOr404(conn, sweepId)
        return {"cancelled": queue.cancelSweep(conn, sweepId)}


@router.post("/api/sweeps/{sweepId}/retry", tags=["sweeps"])
def apiRetrySweep(request: Request, sweepId: int):
    """Queue the sweep's failed and cancelled runs again"""
    with _connect(request) as conn:
        _sweepOr404(conn, sweepId)
        return {"queued": queue.retrySweep(conn, sweepId)}


# --- pages

async def _formInputs(recipe, parameters, seeds, rows_csv):
    """(recipe, spec, errors) from the New sweep form"""
    errors = []
    try:
        recipeObj = json.loads(recipe)
    except ValueError as exc:
        return None, None, ["recipe: not valid JSON: {0}".format(exc)]
    try:
        params = json.loads(parameters) if parameters.strip() else []
    except ValueError as exc:
        return recipeObj, None, ["parameters: not valid JSON: {0}".format(exc)]
    spec = {"parameters": params}
    if seeds.strip():
        try:
            spec["seeds"] = ab_sweep.parseSeeds(seeds)
        except ValueError as exc:
            errors.append("seeds: {0}".format(exc))
    if rows_csv is not None and rows_csv.filename:
        try:
            spec["rows"] = ab_sweep.parseCsv((await rows_csv.read()).decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as exc:
            errors.append("CSV file: {0}".format(exc))
        if isinstance(params, list):  # the rows give the values
            spec["parameters"] = [{k: v for k, v in p.items() if k != "values"} if isinstance(p, dict) else p
                                  for p in params]
    return recipeObj, spec, errors


def _newSweepPage(request, conn, recipe_text, parameters_text, seeds="", name="", backend=None, priority="0",
                  errors=(), status_code=200):
    agents = queue.listAgents(conn)
    if backend is None:
        backend = next((a["backend"] for a in agents if a["live"] and a["backend"] == "slurm"),
                       next((a["backend"] for a in agents if a["live"]), queue.BACKENDS[0]))
    page = request.app.state.render(
        request, "sweep_new.html", recipe_text=recipe_text, parameters_text=parameters_text, seeds=seeds,
        name=name, backend=backend, priority=priority, errors=list(errors), recipes=queue.listRecipes(conn),
        backends=queue.BACKENDS, agents=agents, max_runs=ab_sweep.MAX_RUNS)
    page.status_code = status_code
    return page


@router.get("/sweeps", response_class=HTMLResponse, include_in_schema=False)
def sweepsPage(request: Request):
    with _connect(request) as conn:
        rows = queue.listSweeps(conn)
    return request.app.state.render(request, "sweeps.html", sweeps=rows)


@router.get("/sweeps/new", response_class=HTMLResponse, include_in_schema=False)
def newSweepPage(request: Request, recipe: int = None, sweep: int = None):
    """New sweep: from a saved recipe (?recipe=), or a copy of a sweep (?sweep=)"""
    with _connect(request) as conn:
        from ambuild_web.submissions import TEMPLATE

        body, params, seeds, name = TEMPLATE, DEFAULT_PARAMETERS, "", ""
        if recipe is not None:
            saved = queue.getRecipe(conn, recipe)
            if saved is None:
                raise HTTPException(404, "No such recipe")
            body = saved["body"]
        elif sweep is not None:
            old = _sweepOr404(conn, sweep)
            body, name = old["recipe"], old["name"]
            params = old["spec"].get("parameters", [])
            seeds = ", ".join(str(s) for s in old["spec"].get("seeds", []))
        return _newSweepPage(request, conn, recipeText(body), recipeText(params), seeds=seeds, name=name)


@router.post("/sweeps/preview", response_class=HTMLResponse, include_in_schema=False)
async def sweepPreview(request: Request, recipe: str = Form(""), parameters: str = Form(""), seeds: str = Form(""),
                       rows_csv: UploadFile = File(None)):
    recipeObj, spec, errors = await _formInputs(recipe, parameters, seeds, rows_csv)
    preview = None
    if not errors:
        with _connect(request) as conn:
            runs, errors = _build(conn, recipeObj, spec)
        if not errors:
            preview = _preview(runs)
    names = [p.get("name") for p in (spec or {}).get("parameters", []) if isinstance(p, dict)]
    return request.app.state.render(request, "_sweep_preview.html", errors=errors, preview=preview, names=names)


@router.post("/sweeps/paths", response_class=HTMLResponse, include_in_schema=False)
def sweepPaths(request: Request, recipe: str = Form("")):
    """The recipe's settings and their pointers, to copy into parameters"""
    try:
        paths = ab_sweep.recipePaths(json.loads(recipe))
    except ValueError as exc:
        paths, error = [], "Not valid JSON: {0}".format(exc)
    else:
        error = None
    return request.app.state.render(request, "_sweep_paths.html", paths=paths, error=error)


@router.post("/sweeps", include_in_schema=False)
async def createSweepForm(request: Request, recipe: str = Form(""), parameters: str = Form(""), seeds: str = Form(""),
                          name: str = Form(""), backend: str = Form("local"), priority: str = Form("0"),
                          rows_csv: UploadFile = File(None)):
    recipeObj, spec, errors = await _formInputs(recipe, parameters, seeds, rows_csv)
    with _connect(request) as conn:
        if not errors:
            try:
                priorityValue = _int(priority.strip(), "priority", 0)
            except HTTPException as exc:
                errors = [exc.detail]
        if not errors:
            row, errors = _create(request, conn, name, recipeObj, spec, backend, priorityValue, None)
            if not errors:
                return RedirectResponse("/sweeps/{0}".format(row["sweep_id"]), status_code=303)
        return _newSweepPage(request, conn, recipe, parameters, seeds=seeds, name=name, backend=backend,
                             priority=priority, errors=errors, status_code=422)


@router.get("/sweeps/{sweepId}", response_class=HTMLResponse, include_in_schema=False)
def sweepPage(request: Request, sweepId: int, metric: str = "surface_area_m2_g"):
    if metric not in dict(METRICS):
        metric = "surface_area_m2_g"
    with _connect(request) as conn:
        row = _sweepOr404(conn, sweepId)
        runs = sweepResults(conn, sweepId)
    counts = {}
    for r in runs:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    names = [p["name"] for p in row["spec"].get("parameters", [])]
    return request.app.state.render(
        request, "sweep.html", sweep=row, runs=runs, counts=counts, names=names, metric=metric, metrics=METRICS,
        charts=sweepCharts(row["spec"], runs, metric), active=any(r["state"] in queue.ACTIVE for r in runs),
        seeds=row["spec"].get("seeds"))


@router.post("/sweeps/{sweepId}/{action}", include_in_schema=False)
def sweepAction(request: Request, sweepId: int, action: str):
    actions = {"cancel": queue.cancelSweep, "retry": queue.retrySweep}
    if action not in actions:
        raise HTTPException(404, "No such action")
    with _connect(request) as conn:
        _sweepOr404(conn, sweepId)
        actions[action](conn, sweepId)
    return RedirectResponse("/sweeps/{0}".format(sweepId), status_code=303)
