"""Stored runs: the list, a run's page, its files, events and comparisons (pages and API)."""
import json
import posixpath
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, StreamingResponse

from ambuild_web import db, formatting, storage

router = APIRouter()

MAX_COMPARE = 4
STEP_METRICS = [  # (column, label) plotted against step
    ("num_particles", "atoms"),
    ("num_blocks", "blocks"),
    ("density", "density (g/cm³)"),
    ("num_free_endgroups", "free end groups"),
    ("potential_energy", "potential energy"),
]


def _float(value):
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        raise HTTPException(422, "Not a number: {0}".format(value))


def _page(value):
    return int(value) if value and value.isdigit() and int(value) > 0 else 1


def _runFilter(request):
    q = request.query_params
    return db.RunFilter(
        status=[s for s in q.getlist("status") if s],
        q=q.get("q", "").strip(),
        children=q.get("children") in ("1", "on", "true"),
        min_surface_area=_float(q.get("min_sa")),
        max_surface_area=_float(q.get("max_sa")),
        min_pld=_float(q.get("min_pld")),
        max_pld=_float(q.get("max_pld")),
        with_poreblazer=q.get("poreblazer") in ("1", "on", "true"),
        sort=q.get("sort", "started") if q.get("sort", "started") in db.SORTS else "started",
        descending=q.get("dir", "desc") != "asc",
        page=_page(q.get("page")),
    )


def _runOr404(conn, runId):
    runId = db.parseRunId(runId)
    run = db.getRun(conn, runId) if runId else None
    if run is None:
        raise HTTPException(404, "No such run")
    return run


def _stepCharts(stepRows, labelled=None):
    """Chart specs, one per metric, for one run's steps or several runs' (labelled: [(label, rows)])"""
    runs = labelled if labelled is not None else [("", stepRows)]
    charts = []
    for column, label in STEP_METRICS:
        series = []
        for name, rows in runs:
            points = [(r["step"], r[column]) for r in rows if r.get(column) is not None]
            if points:
                series.append({"label": name or label, "x": [p[0] for p in points], "y": [p[1] for p in points]})
        if series:
            charts.append({"id": "steps-" + column, "title": label, "xlabel": "step", "ylabel": label,
                           "series": series})
    return charts


def _psdCharts(poreRows, labelFor=None):
    """PSD and cumulative PSD chart specs; one series per Poreblazer result"""
    charts = []
    for column, title, ylabel in (("psd", "Pore size distribution", "-dV/dd"),
                                  ("psd_cumulative", "Cumulative pore volume", "fraction")):
        series = []
        for p in poreRows:
            points = p.get(column) or []
            if points:
                label = labelFor(p) if labelFor else "step {0}".format(p["step"] if p["step"] is not None else "?")
                series.append({"label": label, "x": [pt[0] for pt in points], "y": [pt[1] for pt in points]})
        if series:
            charts.append({"id": column, "title": title, "xlabel": "pore diameter (Å)", "ylabel": ylabel,
                           "series": series, "legend": True})
    return charts


# --- pages

@router.get("/runs", response_class=HTMLResponse, include_in_schema=False)
def runsPage(request: Request):
    f = _runFilter(request)
    with db.connect(request.app.state.settings) as conn:
        runs, total = db.listRuns(conn, f)
    kept = [(k, v) for k, v in request.query_params.multi_items() if k != "page"]

    def pageUrl(n):
        return "/runs?" + urlencode(kept + [("page", n)])

    return request.app.state.render(request, "runs.html", runs=runs, total=total, f=f,
                                    pages=max(1, -(-total // db.PAGE_SIZE)), sorts=list(db.SORTS),
                                    page_url=pageUrl)


@router.get("/runs/{runId}", response_class=HTMLResponse, include_in_schema=False)
def runPage(request: Request, runId: str):
    with db.connect(request.app.state.settings) as conn:
        run = _runOr404(conn, runId)
        firstEvents = db.events(conn, run["summary"]["run_id"], limit=100)
    summary = run["summary"]
    return request.app.state.render(
        request, "run.html", r=run, s=summary, run_json=run["run"]["run_json"],
        step_charts=_stepCharts(run["steps"]), psd_charts=_psdCharts(run["pore_results"]),
        events=firstEvents, next_offset=len(firstEvents) if len(firstEvents) == 100 else None,
        run_json_text=json.dumps(run["run"]["run_json"], indent=2, default=str),
        quote=quote)


@router.get("/runs/{runId}/events", response_class=HTMLResponse, include_in_schema=False)
def runEventsPartial(request: Request, runId: str, type: str = "", offset: int = 0):
    runId = db.parseRunId(runId)
    if not runId:
        raise HTTPException(404, "No such run")
    with db.connect(request.app.state.settings) as conn:
        rows = db.events(conn, runId, etype=type or None, offset=offset, limit=100)
    return request.app.state.render(request, "_events.html", events=rows, run_id=runId, etype=type,
                                    next_offset=offset + len(rows) if len(rows) == 100 else None)


@router.get("/runs/{runId}/files/{path:path}", include_in_schema=False)
def runFile(request: Request, runId: str, path: str, inline: bool = False):
    return _streamFile(request, runId, path, inline)


@router.get("/compare", response_class=HTMLResponse, include_in_schema=False)
def comparePage(request: Request, run: list[str] = Query(default=[])):
    runIds = []
    for r in run:
        runId = db.parseRunId(r)
        if runId and runId not in runIds:
            runIds.append(runId)
    if len(runIds) < 2:
        raise HTTPException(422, "Choose at least two runs to compare")
    runIds = runIds[:MAX_COMPARE]
    with db.connect(request.app.state.settings) as conn:
        runs = [db.getRun(conn, r) for r in runIds]
    runs = [r for r in runs if r]
    if len(runs) < 2:
        raise HTTPException(404, "No such runs")
    names = ["{0} ({1})".format(r["summary"]["label"], r["summary"]["run_id"][:8]) for r in runs]
    rows = _compareRows(runs)
    pores = []
    for name, r in zip(names, runs):
        for p in r["pore_results"]:
            pores.append(dict(p, run_name=name))
    return request.app.state.render(
        request, "compare.html", runs=runs, names=names, rows=rows, pores=pores,
        step_charts=_stepCharts(None, labelled=list(zip(names, [r["steps"] for r in runs]))),
        psd_charts=_psdCharts(pores, labelFor=lambda p: "{0} step {1}".format(p["run_name"], p["step"])))


def _compareRows(runs):
    """(label, [value per run], differ) rows of settings and results"""
    def get(run, *keys):
        value = run["run"]["run_json"]
        for k in keys:
            value = value.get(k) if isinstance(value, dict) else None
        return value

    def inputsDigest(run, kind):
        entries = sorted((i.get("path"), i.get("sha256")) for i in get(run, "inputs") or [] if i.get("kind") == kind)
        return ", ".join("{0} {1}".format(posixpath.basename(p or ""), (h or "")[:10]) for p, h in entries) or "–"

    spec = [
        ("status", lambda r: r["summary"]["status"]),
        ("script", lambda r: r["summary"]["label"]),
        ("started", lambda r: formatting.when(r["summary"]["started"])),  # compared as shown
        ("duration (s)", lambda r: round(r["summary"]["duration"]) if r["summary"]["duration"] else None),
        ("box (Å)", lambda r: get(r, "cell", "box_dim")),
        ("atom margin", lambda r: get(r, "cell", "atom_margin")),
        ("bond margin", lambda r: get(r, "cell", "bond_margin")),
        ("bond angle margin (°)", lambda r: get(r, "cell", "bond_angle_margin_degrees")),
        ("seed", lambda r: get(r, "random", "seed")),
        ("Ambuild version", lambda r: r["summary"]["ambuild_version"]),
        ("git commit", lambda r: (r["summary"]["git_commit"] or "")[:10] or None),
        ("building blocks", lambda r: inputsDigest(r, "blocks")),
        ("parameter files", lambda r: inputsDigest(r, "params")),
        ("steps", lambda r: r["summary"]["last_step"]),
        ("atoms", lambda r: r["summary"]["num_particles"]),
        ("blocks", lambda r: r["summary"]["num_blocks"]),
        ("density (g/cm³)", lambda r: r["summary"]["density"]),
        ("surface area (m²/g)", lambda r: r["summary"]["surface_area_m2_g"]),
        ("pore limiting diameter (Å)", lambda r: r["summary"]["pore_limiting_diameter_a"]),
        ("maximum pore diameter (Å)", lambda r: r["summary"]["maximum_pore_diameter_a"]),
        ("helium volume (cm³/g)", lambda r: r["summary"]["helium_volume_cm3_g"]),
    ]
    rows = []
    for label, fn in spec:
        values = [fn(r) for r in runs]
        rows.append((label, values, len({json.dumps(v, default=str) for v in values}) > 1))
    return rows


# --- API

@router.get("/api/runs", tags=["runs"])
def apiRuns(request: Request):
    """Recorded runs, newest first by default. Filters: status (repeatable), q, children,
    min_sa/max_sa (surface area, m²/g), min_pld/max_pld (Å), poreblazer, sort, dir, page"""
    f = _runFilter(request)
    with db.connect(request.app.state.settings) as conn:
        runs, total = db.listRuns(conn, f)
    return jsonable_encoder({"total": total, "page": f.page, "page_size": db.PAGE_SIZE, "runs": runs})


@router.get("/api/runs/{runId}", tags=["runs"])
def apiRun(request: Request, runId: str):
    """One run: run.json, summary, parent and child runs, steps, Poreblazer results, files"""
    with db.connect(request.app.state.settings) as conn:
        run = _runOr404(conn, runId)
    return jsonable_encoder(run)


@router.get("/api/runs/{runId}/events", tags=["runs"])
def apiRunEvents(request: Request, runId: str, type: str = "", offset: int = 0, limit: int = 200):
    """A run's events (events.jsonl), in order"""
    with db.connect(request.app.state.settings) as conn:
        run = _runOr404(conn, runId)
        rows = db.events(conn, run["summary"]["run_id"], etype=type or None, offset=offset, limit=limit)
    return jsonable_encoder({"offset": offset, "events": rows})


@router.get("/api/runs/{runId}/files/{path:path}", tags=["runs"])
def apiRunFile(request: Request, runId: str, path: str, inline: bool = False):
    """A file of the run (one of those listed in its files), streamed from object storage"""
    return _streamFile(request, runId, path, inline)


def _streamFile(request, runId, path, inline):
    settings = request.app.state.settings
    runId = db.parseRunId(runId)
    if not runId:
        raise HTTPException(404, "No such run")
    with db.connect(settings) as conn:
        row = db.fileRow(conn, runId, path)  # only files the run recorded: no other keys reachable
    if row is None:
        raise HTTPException(404, "No such file in this run")
    try:
        chunks, size = storage.openObject(settings, row["uri"])
    except Exception as exc:
        raise HTTPException(502, "Could not read the file from object storage: {0}".format(type(exc).__name__))
    name = posixpath.basename(path)
    headers = {
        "Content-Disposition": "{0}; filename*=UTF-8''{1}".format("inline" if inline else "attachment", quote(name)),
        "X-Content-SHA256": row["sha256"],
        "X-Content-Type-Options": "nosniff",
    }
    if size is not None:
        headers["Content-Length"] = str(size)
    return StreamingResponse(chunks, media_type=storage.contentType(path, inline), headers=headers)
