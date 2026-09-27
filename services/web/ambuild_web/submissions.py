"""Submitting builds: input files (blobs), saved recipes, the New run page, the queue and
each submission (pages and API). Agents take the work through ambuild_web.agents."""
import contextlib
import hashlib
import json
import posixpath
import re
from urllib.parse import quote

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from ambuild import recipe as ab_recipe
from ambuild_web import db, identity, queue, storage

router = APIRouter()

# The New run page's starting point when no recipe is chosen
TEMPLATE = {
    "recipe_version": 1,
    "name": "my build",
    "cell": {"box": [30, 30, 30]},
    "fragments": [{"type": "A", "car": "sha256:(upload the .car file)", "csv": "sha256:(and its .csv)",
                   "name": "benzene"}],
    "bond_types": ["A:a-A:a"],
    "stages": [
        {"op": "seed", "count": 10},
        {"repeat": 5, "stages": [{"op": "grow", "count": 5}, {"op": "zip", "bond_margin": 1.0}]},
    ],
    "seed": None,
}


@contextlib.contextmanager
def _connect(request):
    """A database connection with the queue tables in place"""
    with db.connect(request.app.state.settings) as conn:
        queue.ensureSchema(conn)
        yield conn


def _owner(request, given=None):
    return identity.cleanName(given) or identity.currentUser(request)


def _int(value, what, default=None):
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(422, "{0} must be an integer".format(what))


def checkRecipe(conn, recipe):
    """Problems with a recipe submitted through the web: the recipe's own, and files it
    references that were never uploaded"""
    errors = ab_recipe.validate(recipe)
    if errors:
        return errors
    missing = set(queue.missingBlobs(conn, ab_recipe.references(recipe)))
    return ["{0}: no uploaded file has this sha256".format(where)
            for where, ref in ab_recipe.fileReferences(recipe) if ref.split(":", 1)[1] in missing]


_TOP_ORDER = ["recipe_version", "name", "description", "cell", "fragments", "params", "bond_types", "max_bonds",
              "stages", "seed", "resources"]
# A list of numbers as json.dumps(indent=...) lays it out: one per line (a string never
# holds a raw newline, so this cannot match inside one)
_NUMBERS = re.compile(r"\[\n\s*(-?\d[\d.eE+-]*(?:,\n\s*-?\d[\d.eE+-]*)*)\n\s*\]")


def _ordered(value, first=()):
    """A recipe with its keys in reading order (the database's jsonb sorts them its own way)"""
    if isinstance(value, list):
        return [_ordered(v, first) for v in value]
    if not isinstance(value, dict):
        return value
    keys = [k for k in first if k in value] + [k for k in value if k not in first]
    return {k: _ordered(value[k], ("op", "repeat", "type") if k in ("stages", "fragments") else ()) for k in keys}


def recipeText(recipe):
    """A recipe as JSON for people: keys in reading order, lists of numbers on one line"""
    text = json.dumps(_ordered(recipe, _TOP_ORDER), indent=2, ensure_ascii=False)
    return _NUMBERS.sub(lambda m: "[" + ", ".join(x.strip() for x in m.group(1).split(",")) + "]", text)


def _parseRecipe(text):
    """(recipe, errors) from JSON text"""
    try:
        recipe = json.loads(text)
    except ValueError as exc:
        return None, ["Not valid JSON: {0}".format(exc)]
    return recipe, []


# --- blobs

def _storeBlob(request, conn, name, data):
    if len(data) > queue.MAX_BLOB_BYTES:
        raise HTTPException(413, "Files are limited to {0} MB".format(queue.MAX_BLOB_BYTES // (1024 * 1024)))
    if not data:
        raise HTTPException(422, "The file is empty")
    settings = request.app.state.settings
    digest = hashlib.sha256(data).hexdigest()
    existing = queue.getBlob(conn, digest)
    if existing:
        return existing, True
    try:
        uri = storage.putObject(settings, queue.blobKey(settings, digest), data)
    except Exception as exc:
        raise HTTPException(502, "Could not store the file: {0}".format(type(exc).__name__))
    name = posixpath.basename((name or "file").replace("\\", "/"))[:200] or "file"
    return queue.addBlob(conn, digest, name, len(data), uri, _owner(request)), False


def _blobJson(row, existed=None):
    data = {"sha256": row["sha256"], "ref": "sha256:" + row["sha256"], "name": row["name"], "size": row["size"],
            "owner": row["owner"], "created": row["created"]}
    if existed is not None:
        data["existed"] = existed
    return jsonable_encoder(data)


@router.post("/api/blobs", tags=["inputs"])
async def apiUploadBlob(request: Request, file: UploadFile = File(...)):
    """Upload a building block or parameter file; returns its reference ("sha256:<hex>").
    Files are stored once: uploading the same content again returns the same reference."""
    data = await file.read()
    with _connect(request) as conn:
        row, existed = _storeBlob(request, conn, file.filename, data)
    return _blobJson(row, existed)


@router.get("/api/blobs", tags=["inputs"])
def apiBlobs(request: Request):
    """Uploaded files, newest first"""
    with _connect(request) as conn:
        return {"blobs": [_blobJson(r) for r in queue.listBlobs(conn)]}


@router.get("/api/blobs/{sha256}", tags=["inputs"])
def apiBlob(request: Request, sha256: str):
    """An uploaded file's content"""
    with _connect(request) as conn:
        row = queue.getBlob(conn, sha256.lower())
    if row is None:
        raise HTTPException(404, "No such file")
    try:
        chunks, size = storage.openObject(request.app.state.settings, row["uri"])
    except Exception as exc:
        raise HTTPException(502, "Could not read the file from object storage: {0}".format(type(exc).__name__))
    headers = {"Content-Disposition": "attachment; filename*=UTF-8''" + quote(row["name"]),
               "X-Content-SHA256": row["sha256"]}
    if size is not None:
        headers["Content-Length"] = str(size)
    return StreamingResponse(chunks, media_type="application/octet-stream", headers=headers)


@router.post("/blobs/upload", response_class=HTMLResponse, include_in_schema=False)
async def uploadBlobs(request: Request, files: list[UploadFile] = File(...)):
    """The New run page's upload form (htmx): store the files, return the file list"""
    uploaded = []
    with _connect(request) as conn:
        for f in files:
            if f.filename:
                row, _ = _storeBlob(request, conn, f.filename, await f.read())
                uploaded.append(row["sha256"])
        blobs = queue.listBlobs(conn)
    return request.app.state.render(request, "_blobs.html", blobs=blobs, highlight=set(uploaded))


# --- recipes

@router.get("/api/recipe-format", tags=["recipes"])
def apiRecipeFormat():
    """The recipe operations and their arguments (ambuild.recipe)"""
    return ab_recipe.describe()


@router.post("/api/recipes/validate", tags=["recipes"])
def apiValidate(request: Request, recipe: dict = Body(...)):
    """Check a recipe: its structure, and that every file it references has been uploaded"""
    with _connect(request) as conn:
        errors = checkRecipe(conn, recipe)
    return {"valid": not errors, "errors": errors,
            "sha256": ab_recipe.recipeHash(recipe),
            "operations": ab_recipe.countSteps(recipe.get("stages")) if not errors else None}


@router.get("/api/recipes", tags=["recipes"])
def apiRecipes(request: Request):
    """Saved recipes: the latest version of each name"""
    with _connect(request) as conn:
        return jsonable_encoder({"recipes": queue.listRecipes(conn)})


@router.post("/api/recipes", tags=["recipes"], status_code=201)
def apiSaveRecipe(request: Request, recipe: dict = Body(...), owner: str = None):
    """Save a recipe as the next version of its name"""
    with _connect(request) as conn:
        errors = checkRecipe(conn, recipe)
        if errors:
            return JSONResponse({"valid": False, "errors": errors}, status_code=422)
        row = queue.saveRecipe(conn, recipe, _owner(request, owner))
    return jsonable_encoder(row)


@router.get("/api/recipes/{recipeId}", tags=["recipes"])
def apiRecipe(request: Request, recipeId: int):
    with _connect(request) as conn:
        row = queue.getRecipe(conn, recipeId)
    if row is None:
        raise HTTPException(404, "No such recipe")
    return jsonable_encoder(row)


# --- submissions

def _submit(request, conn, recipe, seed=None, backend="local", priority=0, recipeId=None, name=None, owner=None):
    """(submission, errors)"""
    errors = checkRecipe(conn, recipe)
    if backend not in queue.BACKENDS:
        errors.append("backend: must be one of {0}".format(", ".join(queue.BACKENDS)))
    if seed is not None and not 0 <= seed < 2 ** 63:
        errors.append("seed: must be a non-negative integer")
    if errors:
        return None, errors
    row = queue.createSubmission(conn, recipe, _owner(request, owner), seed=seed, backend=backend,
                                 priority=priority, recipeId=recipeId, name=name or None)
    return row, []


@router.post("/api/submissions", tags=["submissions"], status_code=201)
def apiSubmit(request: Request, payload: dict = Body(...)):
    """Queue one run: {"recipe": {...}} or {"recipe_id": n}, with optional "seed" (overrides
    the recipe's), "backend" (default local), "priority", "name" and "owner"."""
    with _connect(request) as conn:
        recipeId = payload.get("recipe_id")
        recipe = payload.get("recipe")
        if recipe is None and recipeId is not None:
            saved = queue.getRecipe(conn, _int(recipeId, "recipe_id"))
            if saved is None:
                raise HTTPException(404, "No such recipe")
            recipe = saved["body"]
        if not isinstance(recipe, dict):
            raise HTTPException(422, "Give a recipe or a recipe_id")
        row, errors = _submit(request, conn, recipe, seed=_int(payload.get("seed"), "seed"),
                              backend=payload.get("backend", "local"), priority=_int(payload.get("priority"),
                                                                                    "priority", 0),
                              recipeId=recipeId, name=payload.get("name"), owner=payload.get("owner"))
    if errors:
        return JSONResponse({"valid": False, "errors": errors}, status_code=422)
    return jsonable_encoder(row)


@router.get("/api/submissions", tags=["submissions"])
def apiSubmissions(request: Request, state: list[str] = None):
    """The queue: active submissions first (by priority, newest first), then finished ones"""
    with _connect(request) as conn:
        return jsonable_encoder({"submissions": queue.listSubmissions(conn, state or ())})


def _submissionOr404(conn, submissionId):
    row = queue.getSubmission(conn, submissionId)
    if row is None:
        raise HTTPException(404, "No such submission")
    return row


@router.get("/api/submissions/{submissionId}", tags=["submissions"])
def apiSubmission(request: Request, submissionId: int):
    with _connect(request) as conn:
        return jsonable_encoder(_submissionOr404(conn, submissionId))


def _change(request, submissionId, action):
    with _connect(request) as conn:
        _submissionOr404(conn, submissionId)
        try:
            state = action(conn, submissionId)
        except queue.Conflict as exc:
            raise HTTPException(409, str(exc))
    return state


@router.post("/api/submissions/{submissionId}/cancel", tags=["submissions"])
def apiCancel(request: Request, submissionId: int):
    """Cancel: queued work at once; running work is stopped by its agent (state cancelling)"""
    return {"state": _change(request, submissionId, queue.cancel)}


@router.post("/api/submissions/{submissionId}/retry", tags=["submissions"])
def apiRetry(request: Request, submissionId: int):
    """Queue a failed or cancelled submission again (as a new run)"""
    return {"state": _change(request, submissionId, queue.retry)}


@router.get("/api/agents", tags=["agents"])
def apiAgents(request: Request):
    """The agents: backend, last heartbeat, whether live (heard from within a lease), and
    how many submissions each is running"""
    with _connect(request) as conn:
        return jsonable_encoder({"agents": queue.listAgents(conn)})


# --- pages

def _newRunPage(request, conn, text, errors=(), seed="", priority=0, name="", recipeId=None, backend=None,
                status_code=200):
    agents = queue.listAgents(conn)
    if backend is None:  # default: a backend with an agent online
        backend = next((a["backend"] for a in agents if a["live"]), queue.BACKENDS[0])
    page = request.app.state.render(
        request, "submit.html", recipe_text=text, errors=list(errors), seed=seed, priority=priority, name=name,
        recipe_id=recipeId, recipes=queue.listRecipes(conn), blobs=queue.listBlobs(conn), highlight=set(),
        backends=queue.BACKENDS, backend=backend, format=ab_recipe.describe(), agents=agents)
    page.status_code = status_code
    return page


@router.get("/submit", response_class=HTMLResponse, include_in_schema=False)
def submitPage(request: Request, recipe: int = None, submission: int = None):
    """New run: from a saved recipe (?recipe=), a past submission (?submission=), or a template"""
    with _connect(request) as conn:
        body, seed, name, backend = TEMPLATE, "", "", None
        if recipe is not None:
            saved = queue.getRecipe(conn, recipe)
            if saved is None:
                raise HTTPException(404, "No such recipe")
            body = saved["body"]
        elif submission is not None:
            past = _submissionOr404(conn, submission)
            body, name, backend = past["recipe"], past["name"], past["backend"]
            seed = "" if past["seed"] is None else str(past["seed"])
        return _newRunPage(request, conn, recipeText(body), seed=seed, name=name, recipeId=recipe, backend=backend)


@router.post("/submit/validate", response_class=HTMLResponse, include_in_schema=False)
def submitValidate(request: Request, recipe: str = Form("")):
    recipeObj, errors = _parseRecipe(recipe)
    with _connect(request) as conn:
        if not errors:
            errors = checkRecipe(conn, recipeObj)
    operations = ab_recipe.countSteps(recipeObj.get("stages")) if not errors else None
    return request.app.state.render(request, "_validation.html", errors=errors, operations=operations)


@router.post("/submit", include_in_schema=False)
def submitForm(request: Request, recipe: str = Form(""), seed: str = Form(""), priority: str = Form("0"),
               backend: str = Form("local"), name: str = Form(""), action: str = Form("submit"),
               recipe_id: str = Form("")):
    recipeObj, errors = _parseRecipe(recipe)
    with _connect(request) as conn:
        if not errors and action == "save":
            errors = checkRecipe(conn, recipeObj)
            if not errors:
                saved = queue.saveRecipe(conn, recipeObj, _owner(request))
                return RedirectResponse("/submit?recipe={0}".format(saved["recipe_id"]), status_code=303)
        if not errors:
            try:
                seedValue = _int(seed.strip(), "seed")
                priorityValue = _int(priority.strip(), "priority", 0)
            except HTTPException as exc:
                errors = [exc.detail]
        if not errors:
            recipeId = _int(recipe_id, "recipe_id")
            if recipeId is not None:
                saved = queue.getRecipe(conn, recipeId)
                if saved is None or ab_recipe.recipeHash(saved["body"]) != ab_recipe.recipeHash(recipeObj):
                    recipeId = None  # edited since it was loaded
            row, errors = _submit(request, conn, recipeObj, seed=seedValue, backend=backend,
                                  priority=priorityValue, recipeId=recipeId, name=name.strip())
            if not errors:
                return RedirectResponse("/submissions/{0}".format(row["submission_id"]), status_code=303)
        return _newRunPage(request, conn, recipe, errors, seed=seed, priority=priority, name=name, backend=backend,
                           status_code=422)


@router.get("/queue", response_class=HTMLResponse, include_in_schema=False)
def queuePage(request: Request):
    with _connect(request) as conn:
        rows = queue.listSubmissions(conn)
        agents = queue.listAgents(conn)
    return request.app.state.render(request, "queue.html", submissions=rows, agents=agents,
                                    active=any(r["state"] in queue.ACTIVE for r in rows))


@router.get("/queue/rows", response_class=HTMLResponse, include_in_schema=False)
def queueRows(request: Request):
    with _connect(request) as conn:
        rows = queue.listSubmissions(conn)
        agents = queue.listAgents(conn)
    return request.app.state.render(request, "_queue.html", submissions=rows, agents=agents,
                                    active=any(r["state"] in queue.ACTIVE for r in rows))


@router.get("/submissions/{submissionId}", response_class=HTMLResponse, include_in_schema=False)
def submissionPage(request: Request, submissionId: int):
    with _connect(request) as conn:
        row = _submissionOr404(conn, submissionId)
    return request.app.state.render(request, "submission.html", sub=row, active=row["state"] in queue.ACTIVE,
                                    recipe_text=recipeText(row["recipe"]),
                                    operations=ab_recipe.countSteps(row["recipe"].get("stages")))


@router.post("/submissions/{submissionId}/{action}", include_in_schema=False)
def submissionAction(request: Request, submissionId: int, action: str):
    actions = {"cancel": queue.cancel, "retry": queue.retry}
    if action not in actions:
        raise HTTPException(404, "No such action")
    _change(request, submissionId, actions[action])
    return RedirectResponse("/submissions/{0}".format(submissionId), status_code=303)
