"""Campaigns: sweeps that aim at a goal (ambuild.campaign). A controller (services/campaigns)
or an outside decision-maker (method "external": a person, or an LLM agent) proposes
points in rounds through /api/campaigns/{id}/rounds; each round is queued as a sweep, one
Slurm array job, and each trial is scored from its replicate runs. Pages and API."""
import collections
import json

from fastapi import APIRouter, Body, Form, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ambuild import campaign as ab_campaign
from ambuild import ionmap as ab_ionmap
from ambuild import sweep as ab_sweep
from ambuild_web import db, queue
from ambuild_web.submissions import TEMPLATE, _connect, _int, _owner, checkRecipe, recipeText

router = APIRouter()

LABELS = {
    "surface_area_m2_g": "surface area (m²/g)", "pore_limiting_diameter_a": "pore limiting diameter (Å)",
    "maximum_pore_diameter_a": "maximum pore diameter (Å)", "helium_volume_cm3_g": "helium volume (cm³/g)",
    "percolated_dimensions": "percolated dimensions", "density": "density (g/cm³)", "num_particles": "atoms",
    "num_blocks": "blocks",
}
LABELS.update({m: ab_ionmap.metricLabel(m) for m in ab_ionmap.METRICS})  # from ion_map stages (liminal)

# The New campaign page's starting point: the Li-ion showcase (for li_ion_carbon, or any
# recipe of the same shape: a seed stage, then a repeat of grow and zip)
SHOWCASE = {
    "parameters": [
        {"name": "box", "path": "/cell/box", "all": True, "type": "float", "low": 20, "high": 35},
        {"name": "grow", "path": "/stages/1/stages/0/count", "type": "int", "low": 4, "high": 20},
        {"name": "passes", "path": "/stages/1/repeat", "type": "int", "low": 3, "high": 10},
        {"name": "zip_margin", "path": "/stages/1/stages/1/bond_margin", "type": "float", "low": 0.5, "high": 1.5},
    ],
    "constraints": [{"metric": "pore_limiting_diameter_a", "min": 1.52},
                    {"metric": "percolated_dimensions", "min": 1}],
    "objective": {"maximise": "density"},
    "replicates": 3, "method": "tpe", "initial_points": 8, "batch_size": 6,
    "budget": {"runs": 120}, "stop": {"no_improvement_rounds": 4},
}


def label(metric):
    return LABELS.get(metric, metric.replace("_", " "))


def _check(conn, recipe, spec):
    """(normalised spec, errors)"""
    if not isinstance(recipe, dict):
        return None, ["recipe: must be a JSON object"]
    errors = ["base recipe: " + e for e in checkRecipe(conn, recipe)]
    if errors:
        return None, errors
    errors = ab_campaign.validate(spec, recipe)
    return (ab_campaign.normalise(spec) if not errors else None), errors


def _create(request, conn, name, recipe, spec, backend, owner):
    if backend not in queue.BACKENDS:
        return None, ["backend: must be one of {0}".format(", ".join(queue.BACKENDS))]
    normalised, errors = _check(conn, recipe, spec)
    if errors:
        return None, errors
    row = queue.createCampaign(conn, (name or "").strip() or "{0} campaign".format(recipe["name"]), recipe,
                               normalised, backend, _owner(request, owner))
    return row, []


def campaignDetail(conn, row):
    """The campaign with its trials (each scored from its runs), best trial, runs used and
    what the controller should do next"""
    spec = row["spec"]
    trials = [dict(t) for t in queue.campaignTrials(conn, row["campaign_id"])]
    runs = [dict(r) for r in queue.campaignRuns(conn, row["campaign_id"])]
    summaries = {s["run_id"]: s for s in db.runSummaries(conn, [str(r["run_id"]) for r in runs if r["uploaded"]])}
    byTrial = collections.defaultdict(list)
    for r in runs:
        r["run_id"] = str(r["run_id"])
        s = summaries.get(r["run_id"], {})
        r["results"] = {m: s.get(m) for m in ab_campaign.METRICS}
        byTrial[r["trial_id"]].append(r)
    for t in trials:
        t["runs"] = byTrial[t["trial_id"]]
        t["score"] = ab_campaign.score(spec, t["runs"])
    best = ab_campaign.best(spec, trials)
    return {"campaign": row, "trials": trials, "best": best["number"] if best else None, "runs_used": len(runs),
            "decision": ab_campaign.decide(spec, trials, len(runs)) if row["state"] == "active" else None}


def _campaignOr404(conn, campaignId):
    row = queue.getCampaign(conn, campaignId)
    if row is None:
        raise HTTPException(404, "No such campaign")
    return row


def _round(conn, row, points, proposedBy):
    """Queue a round of points; returns (result, errors)"""
    spec = row["spec"]
    if row["state"] != "active":
        return None, ["the campaign is {0}, not active".format(row["state"])]
    if not isinstance(points, list) or not points:
        return None, ["points: a non-empty list of parameter values"]
    errors = []
    for i, point in enumerate(points):
        errors.extend("points[{0}].{1}".format(i, e) for e in ab_campaign.validatePoint(spec, point))
    if errors:
        return None, errors
    detail = campaignDetail(conn, row)
    seeds = ab_campaign.seeds(spec)
    if detail["runs_used"] + len(points) * len(seeds) > spec["budget"]["runs"]:
        return None, ["{0} points of {1} runs each would pass the budget ({2} of {3} runs used)".format(
            len(points), len(seeds), detail["runs_used"], spec["budget"]["runs"])]
    runs = []
    for point in points:
        body = ab_campaign.pointRecipe(row["recipe"], spec, point)
        for seed in seeds:
            runs.append({"index": len(runs), "point": point, "seed": seed, "recipe": body})
    rnd, sweep, trials = queue.createRound(conn, row, points, runs, (proposedBy or "external")[:64])
    return {"round": rnd, "sweep_id": sweep["sweep_id"], "trials": [t["number"] for t in trials],
            "runs": len(runs)}, []


# --- charts

def _spread(xs):
    """x values nudged apart where they repeat, so every point is drawn"""
    seen = collections.Counter()
    out = []
    for x in xs:
        out.append(x + seen[x] * 1e-6 * (abs(x) or 1))
        seen[x] += 1
    return out


def campaignCharts(spec, trials):
    metric = ab_campaign.objectiveMetric(spec) or (spec["constraints"][0]["metric"] if spec["constraints"] else None)
    done = [t for t in trials if t["score"].get("state") == "complete"]
    charts = []
    if not done or metric is None:
        return charts
    obj = spec.get("objective") or {}
    if obj:
        ylabel = label(metric) if "target" not in obj else "|{0} − {1}|".format(label(metric), obj["target"]["value"])
        xs = [t["number"] for t in done]
        best, bestXs, bestYs = None, [], []
        for t in done:
            if t["score"]["feasible"] and ab_campaign.isBetter(spec, t["score"]["value"], best):
                best = t["score"]["value"]
            if best is not None:
                bestXs.append(t["number"])
                bestYs.append(best)
        series = [{"label": "each trial", "x": xs, "y": [t["score"]["value"] for t in done], "scatter": True}]
        if bestXs:
            series.append({"label": "best feasible so far", "x": bestXs, "y": bestYs})
        charts.append({"id": "campaign-progress", "title": "Progress", "xlabel": "trial", "ylabel": ylabel,
                       "series": series, "legend": True})
    for p in spec["parameters"]:
        feasible, other = [], []
        for t in done:
            x = t["params"].get(p["name"])
            if p["type"] == "choice":
                x = p["choices"].index(x) if x in p["choices"] else None
            y = t["score"]["means"].get(metric)
            if x is None or y is None:
                continue
            (feasible if t["score"]["feasible"] else other).append((x, y))
        series = []
        for name, pts in (("feasible", feasible), ("not feasible", other)):
            if pts:
                pts.sort()
                series.append({"label": name, "x": _spread([x for x, _ in pts]), "y": [y for _, y in pts],
                               "scatter": True})
        if series:
            charts.append({"id": "campaign-" + p["name"], "title": "{0} against {1}".format(label(metric), p["name"]),
                           "xlabel": p["name"] + (" (choice)" if p["type"] == "choice" else ""), "ylabel": label(metric),
                           "series": series, "legend": True})
    return charts


# --- API

@router.post("/api/campaigns", tags=["campaigns"], status_code=201)
def apiCreateCampaign(request: Request, payload: dict = Body(...)):
    """Start a campaign: {"recipe": {...}} or {"recipe_id": n}, "spec" (parameters,
    constraints, objective, replicates, method, initial_points, batch_size, budget, stop;
    see ambuild.campaign), "name", "backend", "owner". With "preview": true, only checks it."""
    with _connect(request) as conn:
        recipe = payload.get("recipe")
        if recipe is None and payload.get("recipe_id") is not None:
            saved = queue.getRecipe(conn, _int(payload["recipe_id"], "recipe_id"))
            if saved is None:
                raise HTTPException(404, "No such recipe")
            recipe = saved["body"]
        spec = payload.get("spec")
        if payload.get("preview"):
            normalised, errors = _check(conn, recipe, spec)
            if errors:
                return JSONResponse({"valid": False, "errors": errors}, status_code=422)
            return {"valid": True, "spec": normalised,
                    "first_round_runs": normalised["initial_points"] * len(ab_campaign.seeds(normalised))}
        row, errors = _create(request, conn, payload.get("name"), recipe, spec, payload.get("backend", "local"),
                              payload.get("owner"))
    if errors:
        return JSONResponse({"valid": False, "errors": errors}, status_code=422)
    return jsonable_encoder({"campaign_id": row["campaign_id"], "name": row["name"], "spec": row["spec"]})


@router.get("/api/campaigns", tags=["campaigns"])
def apiCampaigns(request: Request, state: list[str] = None):
    """Campaigns, newest first (state=active for the controller)"""
    with _connect(request) as conn:
        return jsonable_encoder({"campaigns": queue.listCampaigns(conn, state or ())})


@router.get("/api/campaigns/{campaignId}", tags=["campaigns"])
def apiCampaign(request: Request, campaignId: int):
    """A campaign: its spec, trials (params, runs with results, score), best trial, runs
    used, and the next step ({"stop": reason} or {"propose": points}) while active"""
    with _connect(request) as conn:
        return jsonable_encoder(campaignDetail(conn, _campaignOr404(conn, campaignId)))


@router.post("/api/campaigns/{campaignId}/rounds", tags=["campaigns"], status_code=201)
def apiRound(request: Request, campaignId: int, payload: dict = Body(...)):
    """Queue a round of points, {"points": [{parameter: value}], "proposed_by": "..."}: from
    the controller, or from an outside decision-maker (method "external")"""
    with _connect(request) as conn:
        result, errors = _round(conn, _campaignOr404(conn, campaignId), payload.get("points"),
                                payload.get("proposed_by"))
    if errors:
        return JSONResponse({"valid": False, "errors": errors}, status_code=409)
    return result


def _setState(request, campaignId, state, message=None):
    with _connect(request) as conn:
        _campaignOr404(conn, campaignId)
        try:
            return queue.setCampaignState(conn, campaignId, state, message)["state"]
        except queue.Conflict as exc:
            raise HTTPException(409, str(exc))


@router.post("/api/campaigns/{campaignId}/finish", tags=["campaigns"])
def apiFinish(request: Request, campaignId: int, payload: dict = Body(default={})):
    """The controller ending an active campaign: {"reason": ...}"""
    return {"state": _setState(request, campaignId, "finished", str(payload.get("reason") or "finished")[:500])}


@router.post("/api/campaigns/{campaignId}/budget", tags=["campaigns"])
def apiBudget(request: Request, campaignId: int, payload: dict = Body(...)):
    """Add runs to the budget: {"runs": n}; a finished campaign carries on"""
    runs = _int(payload.get("runs"), "runs")
    if not runs or runs < 1:
        raise HTTPException(422, "runs: a positive integer")
    with _connect(request) as conn:
        _campaignOr404(conn, campaignId)
        try:
            row = queue.extendCampaign(conn, campaignId, runs)
        except queue.Conflict as exc:
            raise HTTPException(409, str(exc))
    return {"state": row["state"], "budget": row["spec"]["budget"]["runs"]}


@router.post("/api/campaigns/{campaignId}/{action}", tags=["campaigns"])
def apiAction(request: Request, campaignId: int, action: str):
    """pause, resume or stop (stopping cancels its unfinished runs)"""
    states = {"pause": "paused", "resume": "active", "stop": "stopped"}
    if action not in states:
        raise HTTPException(404, "No such action")
    return {"state": _setState(request, campaignId, states[action], "stopped by a user" if action == "stop" else None)}


# --- pages

def _newCampaignPage(request, conn, recipe_text, spec_text, name="", backend=None, errors=(), status_code=200):
    agents = queue.listAgents(conn)
    if backend is None:
        backend = next((a["backend"] for a in agents if a["live"] and a["backend"] == "slurm"),
                       next((a["backend"] for a in agents if a["live"] and a["backend"] in queue.BACKENDS),
                            queue.BACKENDS[0]))
    page = request.app.state.render(
        request, "campaign_new.html", recipe_text=recipe_text, spec_text=spec_text, name=name, backend=backend,
        errors=list(errors), recipes=queue.listRecipes(conn), backends=queue.BACKENDS, agents=agents,
        metrics=ab_campaign.METRICS, methods=ab_campaign.METHODS,
        controller=any(a["live"] for a in agents if a["backend"] == queue.CONTROLLER))
    page.status_code = status_code
    return page


@router.get("/campaigns", response_class=HTMLResponse, include_in_schema=False)
def campaignsPage(request: Request):
    with _connect(request) as conn:
        rows = queue.listCampaigns(conn)
    return request.app.state.render(request, "campaigns.html", campaigns=rows)


@router.get("/campaigns/new", response_class=HTMLResponse, include_in_schema=False)
def newCampaignPage(request: Request, recipe: int = None, campaign: int = None, spec: str = ""):
    """New campaign: from a saved recipe (?recipe=), or a copy of a campaign (?campaign=);
    ?spec= starts from one of Ambuild's example campaigns (ambuild.campaign.examples)"""
    with _connect(request) as conn:
        examples = ab_campaign.examples()
        if spec and spec not in examples:
            raise HTTPException(404, "No example campaign {0!r}".format(spec))
        body, name = TEMPLATE, ""
        spec = examples[spec] if spec else SHOWCASE
        if recipe is not None:
            saved = queue.getRecipe(conn, recipe)
            if saved is None:
                raise HTTPException(404, "No such recipe")
            body = saved["body"]
        elif campaign is not None:
            old = _campaignOr404(conn, campaign)
            body, spec, name = old["recipe"], old["spec"], old["name"]
        return _newCampaignPage(request, conn, recipeText(body), recipeText(spec), name=name)


def _parse(recipe, spec):
    try:
        return json.loads(recipe), json.loads(spec), []
    except ValueError as exc:
        return None, None, ["not valid JSON: {0}".format(exc)]


@router.post("/campaigns/check", response_class=HTMLResponse, include_in_schema=False)
def campaignCheck(request: Request, recipe: str = Form(""), spec: str = Form("")):
    recipeObj, specObj, errors = _parse(recipe, spec)
    normalised = None
    if not errors:
        with _connect(request) as conn:
            normalised, errors = _check(conn, recipeObj, specObj)
    return request.app.state.render(request, "_campaign_check.html", errors=errors, spec=normalised,
                                    seeds=ab_campaign.seeds(normalised) if normalised else [])


@router.post("/campaigns", include_in_schema=False)
def createCampaignForm(request: Request, recipe: str = Form(""), spec: str = Form(""), name: str = Form(""),
                       backend: str = Form("local")):
    recipeObj, specObj, errors = _parse(recipe, spec)
    with _connect(request) as conn:
        if not errors:
            row, errors = _create(request, conn, name, recipeObj, specObj, backend, None)
            if not errors:
                return RedirectResponse("/campaigns/{0}".format(row["campaign_id"]), status_code=303)
        return _newCampaignPage(request, conn, recipe, spec, name=name, backend=backend, errors=errors,
                                status_code=422)


@router.get("/campaigns/{campaignId}", response_class=HTMLResponse, include_in_schema=False)
def campaignPage(request: Request, campaignId: int):
    with _connect(request) as conn:
        row = _campaignOr404(conn, campaignId)
        detail = campaignDetail(conn, row)
        agents = queue.listAgents(conn)
    spec = row["spec"]
    trials = detail["trials"]
    best = next((t for t in trials if t["number"] == detail["best"]), None)
    metrics = ([ab_campaign.objectiveMetric(spec)] if ab_campaign.objectiveMetric(spec) else []) + \
        [c["metric"] for c in spec["constraints"] if c["metric"] != ab_campaign.objectiveMetric(spec)]
    return request.app.state.render(
        request, "campaign.html", c=row, spec=spec, detail=detail, trials=trials, best=best,
        charts=campaignCharts(spec, trials), metrics=metrics, label=label, sweep_label=ab_sweep.label,
        running=any(t["score"].get("state") == "running" for t in trials),
        controller=any(a["live"] for a in agents if a["backend"] == queue.CONTROLLER),
        example_point=json.dumps([{p["name"]: (p["choices"][0] if p["type"] == "choice" else p["low"])
                                   for p in spec["parameters"]}]))


@router.post("/campaigns/{campaignId}/propose", include_in_schema=False)
def proposeForm(request: Request, campaignId: int, points: str = Form(""), proposed_by: str = Form("")):
    try:
        pts = json.loads(points)
    except ValueError as exc:
        raise HTTPException(422, "points: not valid JSON: {0}".format(exc))
    with _connect(request) as conn:
        _, errors = _round(conn, _campaignOr404(conn, campaignId), pts,
                           proposed_by.strip() or _owner(request) or "external")
    if errors:
        raise HTTPException(409, "; ".join(errors))
    return RedirectResponse("/campaigns/{0}".format(campaignId), status_code=303)


@router.post("/campaigns/{campaignId}/budget", include_in_schema=False)
def budgetForm(request: Request, campaignId: int, runs: str = Form("")):
    apiBudget(request, campaignId, {"runs": runs})
    return RedirectResponse("/campaigns/{0}".format(campaignId), status_code=303)


@router.post("/campaigns/{campaignId}/{action}", include_in_schema=False)
def actionForm(request: Request, campaignId: int, action: str):
    apiAction(request, campaignId, action)
    return RedirectResponse("/campaigns/{0}".format(campaignId), status_code=303)
