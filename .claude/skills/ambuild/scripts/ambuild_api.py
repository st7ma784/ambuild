#!/usr/bin/env python3
"""A command-line client for the Ambuild web GUI's API, for people and for AI agents
following the ambuild skill (SKILL.md beside this directory). Standard library only.

    ambuild_api.py [--url URL] COMMAND ...

Every command prints JSON. Commands that queue work (submit, sweep, campaign, propose,
cancel) only check and preview unless given --yes.

The web GUI's address comes from --url or AMBUILD_API_URL (default http://127.0.0.1:8080);
the owner recorded for new work from --owner or AMBUILD_OWNER.
"""
import argparse
import getpass
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_URL = "http://127.0.0.1:8080"
FILE_KEYS = ("car", "csv", "ambody")


class Client:
    def __init__(self, url):
        self.url = url.rstrip("/")

    def request(self, method, path, body=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "User-Agent": "ambuild-skill"})
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(text)
            except ValueError:
                detail = text
            fail({"error": "HTTP {0}".format(exc.code), "detail": detail})
        except urllib.error.URLError as exc:
            fail({"error": "cannot reach the web GUI at {0}: {1}".format(self.url, exc.reason),
                  "hint": "set AMBUILD_API_URL or --url (e.g. through an ssh tunnel)"})
        return payload if raw else json.loads(payload.decode() or "null")

    def get(self, path, **params):
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "", [])}, doseq=True)
        return self.request("GET", path + ("?" + query if query else ""))

    def post(self, path, body=None):
        return self.request("POST", path, body if body is not None else {})

    def upload(self, path):
        """Upload a file (stored once, by content); returns its reference"""
        boundary = uuid.uuid4().hex
        with open(path, "rb") as f:
            content = f.read()
        body = b"".join([
            "--{0}\r\n".format(boundary).encode(),
            'Content-Disposition: form-data; name="file"; filename="{0}"\r\n'.format(os.path.basename(path)).encode(),
            b"Content-Type: application/octet-stream\r\n\r\n", content, b"\r\n", "--{0}--\r\n".format(boundary).encode()])
        req = urllib.request.Request(self.url + "/api/blobs", data=body, method="POST",
                                     headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                return json.loads(response.read().decode())["ref"]
        except urllib.error.HTTPError as exc:
            fail({"error": "upload of {0} failed: HTTP {1}".format(path, exc.code),
                  "detail": exc.read().decode("utf-8", "replace")})


def out(data):
    json.dump(data, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def fail(data):
    out(data)
    sys.exit(1)


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def isRef(value):
    return isinstance(value, str) and value.startswith("sha256:") and len(value) == 71


def prepareRecipe(client, path):
    """A recipe file with its local files uploaded and replaced by their references"""
    recipe = load(path)
    base = os.path.dirname(os.path.abspath(path))

    def swap(block):
        for key in FILE_KEYS:
            value = block.get(key)
            if isinstance(value, str) and not isRef(value):
                local = value if os.path.isabs(value) else os.path.join(base, value)
                if not os.path.isfile(local):
                    fail({"error": "{0}: no such file {1}".format(key, local)})
                block[key] = client.upload(local)

    for frag in recipe.get("fragments", []):
        swap(frag)
    for name, value in list((recipe.get("params") or {}).items()):
        if not isRef(value):
            local = value if os.path.isabs(value) else os.path.join(base, value)
            recipe["params"][name] = client.upload(local)

    def stages(items):
        for stage in items or []:
            if "repeat" in stage:
                stages(stage.get("stages"))
            elif isinstance(stage.get("block"), dict):
                swap(stage["block"])

    stages(recipe.get("stages"))
    return recipe


def recipeFrom(client, args):
    """The recipe for a command: a file (--recipe) or a saved recipe (--recipe-id)"""
    if getattr(args, "recipe_id", None):
        return client.get("/api/recipes/{0}".format(args.recipe_id))["body"]
    if not getattr(args, "recipe", None):
        fail({"error": "give --recipe FILE or --recipe-id N"})
    return prepareRecipe(client, args.recipe)


def page(client, path):
    return client.url + path


def owner(args):
    return args.owner or os.environ.get("AMBUILD_OWNER") or "{0} via assistant".format(getpass.getuser())


def compactRun(r):
    keep = ("run_id", "label", "status", "started", "finished", "error", "seed", "box", "last_step", "num_particles",
            "num_blocks", "density", "surface_area_m2_g", "pore_limiting_diameter_a", "maximum_pore_diameter_a",
            "helium_volume_cm3_g", "percolated_dimensions", "children")
    return {k: r.get(k) for k in keep if r.get(k) is not None}


# --- commands

def cmdStatus(client, args):
    status = client.get("/api/status")
    out({"state": status["state"], "checks": [{"name": c["name"], "state": c["state"], "summary": c["summary"]}
                                              for c in status["checks"]], "page": page(client, "/status")})


def cmdRuns(client, args):
    data = client.get("/api/runs", q=args.q, status=args.status, min_sa=args.min_sa, max_sa=args.max_sa,
                      min_pld=args.min_pld, max_pld=args.max_pld, poreblazer=1 if args.poreblazer else None,
                      sort=args.sort, dir=args.dir, page=args.page)
    runs = [dict(compactRun(r), page=page(client, "/runs/" + r["run_id"])) for r in data["runs"][:args.limit]]
    out({"total": data["total"], "shown": len(runs), "runs": runs})


def cmdRun(client, args):
    data = client.get("/api/runs/" + args.run_id)
    run = data["run"]["run_json"]
    pore = [{k: p.get(k) for k in ("step", "surface_area_m2_g", "pore_limiting_diameter_a", "maximum_pore_diameter_a",
                                   "helium_volume_cm3_g", "percolated_dimensions", "from_child")}
            for p in data["pore_results"]]
    steps = data["steps"]
    out({"summary": compactRun(data["summary"]), "page": page(client, "/runs/" + args.run_id),
         "recipe": next((i.get("name") for i in run.get("inputs", []) if i.get("kind") == "recipe"), None),
         "cell": run.get("cell"), "random": run.get("random"), "ambuild": run.get("ambuild"),
         "poreblazer": pore, "steps": len(steps),
         "last_steps": [{k: s.get(k) for k in ("step", "type", "num_particles", "num_blocks", "density",
                                                "num_free_endgroups")} for s in steps[-args.steps:]],
         "structures": [{"step": f["step"], "path": f["path"]} for f in data["structures"]],
         "parent": data["parent"] and data["parent"]["run_id"], "children": [c["run_id"] for c in data["children"]]})


def cmdEvents(client, args):
    data = client.get("/api/runs/{0}/events".format(args.run_id), type=args.type, offset=args.offset, limit=args.limit)
    out(data)


def cmdStructure(client, args):
    frames = client.get("/api/runs/{0}/structures".format(args.run_id))["frames"]
    if not frames:
        fail({"error": "this run has no structure files"})
    frame = frames[-1] if args.step is None else next((f for f in frames if f["step"] == args.step), None)
    if frame is None:
        fail({"error": "no structure at step {0}".format(args.step), "steps": [f["step"] for f in frames]})
    text = client.request("GET", frame["url"].replace(client.url, ""), raw=True).decode()
    if args.xyz:
        sys.stdout.write(text)
        return
    lines = text.splitlines()
    n = int(lines[0])
    fragments, blocks, elements = {}, set(), {}
    for line in lines[2:2 + n]:
        parts = line.split()
        elements[parts[0]] = elements.get(parts[0], 0) + 1
        if len(parts) >= 6:
            fragments[parts[4]] = fragments.get(parts[4], 0) + 1
            blocks.add(parts[5])
    out({"step": frame["step"], "path": frame["path"], "header": lines[1], "atoms": n, "elements": elements,
         "atoms_by_fragment_type": fragments, "blocks": len(blocks), "steps": [f["step"] for f in frames]})


def cmdRecipes(client, args):
    out(client.get("/api/recipes"))


def cmdRecipe(client, args):
    out(client.get("/api/recipes/{0}".format(args.recipe_id)))


def cmdFormat(client, args):
    out(client.get("/api/recipe-format"))


def cmdValidate(client, args):
    recipe = recipeFrom(client, args)
    result = client.post("/api/recipes/validate", recipe)
    out(dict(result, recipe=recipe if args.show else None))


def cmdSave(client, args):
    recipe = recipeFrom(client, args)
    if not args.yes:
        out({"would_save": recipe["name"], "valid": client.post("/api/recipes/validate", recipe),
             "note": "run again with --yes to save"})
        return
    out(client.post("/api/recipes", recipe))


def cmdSubmit(client, args):
    recipe = recipeFrom(client, args)
    check = client.post("/api/recipes/validate", recipe)
    if not check["valid"]:
        fail({"valid": False, "errors": check["errors"]})
    preview = {"name": args.name or recipe["name"], "backend": args.backend, "seed": args.seed
               if args.seed is not None else recipe.get("seed"), "operations": check["operations"],
               "resources": recipe.get("resources", {}), "owner": owner(args)}
    if not args.yes:
        out({"preview": preview, "note": "nothing queued: run again with --yes once the user agrees"})
        return
    body = {"recipe": recipe, "backend": args.backend, "priority": args.priority, "owner": owner(args)}
    if args.seed is not None:
        body["seed"] = args.seed
    if args.name:
        body["name"] = args.name
    sub = client.post("/api/submissions", body)
    out({"submission_id": sub["submission_id"], "run_id": sub["run_id"], "state": sub["state"],
         "page": page(client, "/submissions/{0}".format(sub["submission_id"])),
         "run_page": page(client, "/runs/" + sub["run_id"])})


def cmdQueue(client, args):
    subs = client.get("/api/submissions", state=args.state)["submissions"][:args.limit]
    out([{k: s.get(k) for k in ("submission_id", "name", "state", "owner", "backend", "seed_used", "sweep_id",
                                "run_id", "uploaded", "error", "created")} for s in subs])


def cmdSubmission(client, args):
    s = client.get("/api/submissions/{0}".format(args.submission_id))
    s.pop("recipe", None)
    out(dict(s, page=page(client, "/submissions/{0}".format(args.submission_id)),
             run_page=page(client, "/runs/{0}".format(s["run_id"])) if s.get("uploaded") else None))


def cmdCancel(client, args):
    if not args.yes:
        s = client.get("/api/submissions/{0}".format(args.submission_id))
        out({"would_cancel": args.submission_id, "name": s["name"], "state": s["state"],
             "note": "run again with --yes once the user agrees"})
        return
    out(client.post("/api/submissions/{0}/cancel".format(args.submission_id)))


def cmdSweep(client, args):
    recipe = recipeFrom(client, args)
    spec = load(args.spec)
    body = dict({k: spec[k] for k in ("parameters", "rows", "seeds") if k in spec}, recipe=recipe,
                backend=args.backend, owner=owner(args), priority=args.priority)
    if args.name:
        body["name"] = args.name
    if not args.yes:
        preview = client.request("POST", "/api/sweeps", dict(body, preview=True))
        out({"preview": preview, "note": "nothing queued: run again with --yes once the user agrees"})
        return
    created = client.post("/api/sweeps", body)
    out(dict(created, page=page(client, "/sweeps/{0}".format(created["sweep_id"]))))


def cmdSweepGet(client, args):
    data = client.get("/api/sweeps/{0}".format(args.sweep_id))
    runs = [{"point": r["point"], "seed": r["seed"], "state": r["state"], "run_id": r["run_id"] if r["uploaded"] else None,
             "results": {k: v for k, v in r["results"].items() if v is not None}} for r in data["runs"]]
    out({"name": data["sweep"]["name"], "spec": data["sweep"]["spec"], "runs": runs,
         "page": page(client, "/sweeps/{0}".format(args.sweep_id))})


def cmdCampaign(client, args):
    recipe = recipeFrom(client, args)
    body = {"recipe": recipe, "spec": load(args.spec), "backend": args.backend, "owner": owner(args)}
    if args.name:
        body["name"] = args.name
    if not args.yes:
        out({"preview": client.post("/api/campaigns", dict(body, preview=True)),
             "note": "nothing started: run again with --yes once the user agrees"})
        return
    created = client.post("/api/campaigns", body)
    out({"campaign_id": created["campaign_id"], "name": created["name"],
         "page": page(client, "/campaigns/{0}".format(created["campaign_id"]))})


def cmdCampaignGet(client, args):
    d = client.get("/api/campaigns/{0}".format(args.campaign_id))
    trials = [{"number": t["number"], "round": t["round"], "params": t["params"], "by": t["proposed_by"],
               "state": t["score"].get("state"), "value": t["score"].get("value"), "means": t["score"].get("means"),
               "feasible": t["score"].get("feasible"), "runs": [r["run_id"] for r in t["runs"] if r["uploaded"]]}
              for t in d["trials"]]
    c = d["campaign"]
    out({"name": c["name"], "state": c["state"], "message": c["message"], "spec": c["spec"],
         "runs_used": d["runs_used"], "best": d["best"], "next": d["decision"],
         "trials": trials[-args.last:] if args.last else trials, "page": page(client, "/campaigns/{0}".format(c["campaign_id"]))})


def cmdPropose(client, args):
    points = load(args.points) if os.path.isfile(args.points) else json.loads(args.points)
    if not args.yes:
        d = client.get("/api/campaigns/{0}".format(args.campaign_id))
        spec = d["campaign"]["spec"]
        runs = len(points) * len(spec.get("seeds") or range(1, spec["replicates"] + 1))
        out({"would_queue": points, "runs": runs, "runs_used": d["runs_used"], "budget": spec["budget"]["runs"],
             "fits_budget": d["runs_used"] + runs <= spec["budget"]["runs"], "campaign_state": d["campaign"]["state"],
             "note": "nothing queued: run again with --yes once the user agrees"})
        return
    out(client.post("/api/campaigns/{0}/rounds".format(args.campaign_id),
                    {"points": points, "proposed_by": args.by or owner(args)}))


def cmdCampaignAction(client, args):
    if not args.yes:
        out({"would": args.action, "campaign": args.campaign_id, "note": "run again with --yes once the user agrees"})
        return
    out(client.post("/api/campaigns/{0}/{1}".format(args.campaign_id, args.action)))


def recipePaths(value, prefix=""):
    """(JSON pointer, value) of every setting a sweep or campaign could vary"""
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, list):
        if value and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            return [(prefix, value)]
        items = enumerate(value)
    else:
        return [(prefix, value)]
    found = []
    for key, item in items:
        if prefix == "" and key in ("recipe_version", "name", "description"):
            continue
        found.extend(recipePaths(item, prefix + "/" + str(key).replace("~", "~0").replace("/", "~1")))
    return found


def cmdPaths(client, args):
    recipe = client.get("/api/recipes/{0}".format(args.recipe_id))["body"] if args.recipe_id else load(args.recipe)
    out({path: value for path, value in recipePaths(recipe)
         if not isRef(value) and path.rsplit("/", 1)[-1] not in FILE_KEYS and not path.startswith("/params")})


def cmdAgents(client, args):
    out([{k: a.get(k) for k in ("name", "backend", "live", "host", "last_heartbeat", "active")}
         for a in client.get("/api/agents")["agents"] if not a.get("revoked")])


def cmdHash(client, args):
    """sha256 of a local file, as a recipe reference (no upload)"""
    with open(args.file, "rb") as f:
        out({"ref": "sha256:" + hashlib.sha256(f.read()).hexdigest()})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("AMBUILD_API_URL", DEFAULT_URL))
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name, fn, help, yes=False, recipe=False, work=False):
        p = sub.add_parser(name, help=help)
        p.set_defaults(fn=fn)
        if recipe:
            p.add_argument("--recipe", help="a recipe file (local file paths in it are uploaded)")
            p.add_argument("--recipe-id", type=int, help="or a saved recipe")
        if work:
            p.add_argument("--backend", default="slurm", help="where to run: slurm (default) or local")
            p.add_argument("--name")
            p.add_argument("--owner")
            p.add_argument("--priority", type=int, default=0)
        if yes:
            p.add_argument("--yes", action="store_true", help="really do it (the default is a preview)")
        return p

    command("status", cmdStatus, "the web GUI's database, storage and agents")
    p = command("runs", cmdRuns, "search recorded runs")
    p.add_argument("--q", help="text in the run id, script, recipe, command or host")
    p.add_argument("--status", action="append", help="finished, failed, running, incomplete")
    for flag in ("--min-sa", "--max-sa", "--min-pld", "--max-pld"):
        p.add_argument(flag, type=float)
    p.add_argument("--poreblazer", action="store_true", help="only runs with Poreblazer results")
    p.add_argument("--sort", choices=["started", "status", "atoms", "surface_area", "pld"])
    p.add_argument("--dir", choices=["asc", "desc"])
    p.add_argument("--page", type=int)
    p.add_argument("--limit", type=int, default=20)
    p = command("run", cmdRun, "one run: provenance, Poreblazer results, last steps, structures")
    p.add_argument("run_id")
    p.add_argument("--steps", type=int, default=5)
    p = command("events", cmdEvents, "a run's event log")
    p.add_argument("run_id")
    p.add_argument("--type")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=50)
    p = command("structure", cmdStructure, "summarise (or print, --xyz) a run's structure at a checkpoint")
    p.add_argument("run_id")
    p.add_argument("--step", type=int)
    p.add_argument("--xyz", action="store_true")
    command("recipes", cmdRecipes, "saved recipes")
    p = command("recipe", cmdRecipe, "a saved recipe")
    p.add_argument("recipe_id", type=int)
    command("format", cmdFormat, "the recipe operations and their arguments")
    p = command("validate", cmdValidate, "check a recipe (uploading its files)", recipe=True)
    p.add_argument("--show", action="store_true", help="print the recipe as sent")
    command("save", cmdSave, "save a recipe as the next version of its name", yes=True, recipe=True)
    p = command("submit", cmdSubmit, "queue one run of a recipe", yes=True, recipe=True, work=True)
    p.add_argument("--seed", type=int)
    p = command("queue", cmdQueue, "the queue")
    p.add_argument("--state", action="append")
    p.add_argument("--limit", type=int, default=30)
    p = command("submission", cmdSubmission, "one submission")
    p.add_argument("submission_id", type=int)
    p = command("cancel", cmdCancel, "cancel a submission", yes=True)
    p.add_argument("submission_id", type=int)
    p = command("sweep", cmdSweep, "queue a sweep (grid, CSV rows or seeds)", yes=True, recipe=True, work=True)
    p.add_argument("--spec", required=True, help='JSON file: {"parameters": [...], "rows": [...], "seeds": [...]}')
    p = command("sweep-get", cmdSweepGet, "a sweep and its runs' results")
    p.add_argument("sweep_id", type=int)
    p = command("campaign", cmdCampaign, "start a campaign", yes=True, recipe=True, work=True)
    p.add_argument("--spec", required=True, help="JSON file: the campaign (see SKILL.md)")
    p = command("campaign-get", cmdCampaignGet, "a campaign: trials, best, next step")
    p.add_argument("campaign_id", type=int)
    p.add_argument("--last", type=int, help="only the last N trials")
    p = command("propose", cmdPropose, "queue a round of points for a campaign", yes=True)
    p.add_argument("campaign_id", type=int)
    p.add_argument("points", help='JSON list of points, or a file of one, e.g. \'[{"box": 22.5, "grow": 8}]\'')
    p.add_argument("--by", help="who proposed them (default: the owner)")
    p.add_argument("--owner")
    p = command("campaign-action", cmdCampaignAction, "pause, resume or stop a campaign", yes=True)
    p.add_argument("campaign_id", type=int)
    p.add_argument("action", choices=["pause", "resume", "stop"])
    command("paths", cmdPaths, "every setting of a recipe with its JSON pointer (for sweeps and campaigns)",
            recipe=True)
    command("agents", cmdAgents, "the agents that run work, and whether they are online")
    p = command("hash", cmdHash, "a local file's reference (no upload)")
    p.add_argument("file")
    args = parser.parse_args(argv)
    args.fn(Client(args.url), args)


if __name__ == "__main__":
    main()
