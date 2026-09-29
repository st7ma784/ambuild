"""The recipe gallery (/gallery, /api/gallery): Ambuild's example recipes by family
(ambuild.gallery), each with its saved version in this GUI (to run, sweep or aim a
campaign at) and a summary of its recent finished runs."""
import statistics

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse

from ambuild import gallery as ab_gallery
from ambuild_web import db, queue
from ambuild_web.submissions import _connect

router = APIRouter()
RECENT = 20  # runs per recipe summarised


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def recentResults(conn, names):
    """{recipe name: {"runs", "latest", "pld", "surface", "density"}} over its latest
    finished runs (medians)"""
    rows = conn.execute(
        "SELECT recipe, run_id, started, pore_limiting_diameter_a, surface_area_m2_g, density FROM ({0}) runs "
        "WHERE recipe = ANY(%s) AND status = 'finished' AND parent_run_id IS NULL "
        "ORDER BY started DESC".format(db._summarySql()), (list(names),)).fetchall()
    byName = {}
    for r in rows:
        byName.setdefault(r["recipe"], []).append(r)
    out = {}
    for name, runs in byName.items():
        runs = runs[:RECENT]
        out[name] = {"runs": len(runs), "latest": str(runs[0]["run_id"]),
                     "pld": _median([r["pore_limiting_diameter_a"] for r in runs]),
                     "surface": _median([r["surface_area_m2_g"] for r in runs]),
                     "density": _median([r["density"] for r in runs])}
    return out


def galleryData(conn):
    families = ab_gallery.gallery()
    names = [r["name"] for f in families for r in f["recipes"]]
    saved = {r["name"]: r for r in queue.listRecipes(conn)}
    results = recentResults(conn, names)
    for family in families:
        for r in family["recipes"]:
            s = saved.get(r["name"])
            r["saved"] = {"recipe_id": s["recipe_id"], "version": s["version"]} if s else None
            r["results"] = results.get(r["name"])
    return families


@router.get("/api/gallery", tags=["recipes"])
def apiGallery(request: Request):
    """Ambuild's example recipes by family: what each builds, its saved version here (if
    saved), and its recent finished runs' median results"""
    with _connect(request) as conn:
        return jsonable_encoder({"families": galleryData(conn)})


@router.get("/gallery", response_class=HTMLResponse, include_in_schema=False)
def galleryPage(request: Request):
    with _connect(request) as conn:
        families = galleryData(conn)
    return request.app.state.render(request, "gallery.html", families=families)
