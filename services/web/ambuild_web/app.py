"""The web application: pages (server-rendered, refreshed with htmx) and the REST API."""
import datetime
import os
from urllib.parse import urlparse

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ambuild_web import __version__, agents, checks, formatting, identity, runs, submissions, sweeps
from ambuild_web.config import Settings

HERE = os.path.dirname(os.path.abspath(__file__))
STATUS_REFRESH_SECONDS = 10


def createApp(settings=None):
    settings = settings or Settings.fromEnvironment()
    app = FastAPI(title="Ambuild", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
    templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))
    formatting.install(templates.env)

    def page(request, template, **context):
        context.update(request=request, title=settings.title, owner=identity.currentUser(request),
                       version=__version__, path=request.url.path)
        return templates.TemplateResponse(request, template, context)

    app.state.render = page
    app.include_router(runs.router)
    app.include_router(submissions.router)
    app.include_router(sweeps.router)
    app.include_router(agents.router)
    app.include_router(agents.manage)

    def statusData():
        results = checks.runChecks(settings)
        return {
            "state": checks.overall(results),
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "checks": [c.asDict() for c in results],
        }

    # --- pages
    @app.get("/", include_in_schema=False)
    def home():
        return RedirectResponse("/runs", status_code=307)

    @app.get("/status", response_class=HTMLResponse, include_in_schema=False)
    def statusPage(request: Request):
        return page(request, "status.html", status=statusData(), refresh=STATUS_REFRESH_SECONDS)

    @app.get("/status/cards", response_class=HTMLResponse, include_in_schema=False)
    def statusCards(request: Request):
        return page(request, "_status_cards.html", status=statusData(), refresh=STATUS_REFRESH_SECONDS)

    @app.post("/owner", include_in_schema=False)
    def setOwner(name: str = Form(""), next: str = Form("/")):
        # only redirect within the site
        target = next if next.startswith("/") and not next.startswith("//") and not urlparse(next).netloc else "/"
        response = RedirectResponse(target, status_code=303)
        name = identity.cleanName(name)
        if name:
            response.set_cookie(identity.COOKIE, name, max_age=365 * 24 * 3600, samesite="lax", httponly=True)
        else:
            response.delete_cookie(identity.COOKIE)
        return response

    # --- API
    @app.get("/api/status", tags=["status"])
    def apiStatus():
        """Reachability of PostgreSQL and object storage: state ok, warn or fail, with the reason"""
        data = statusData()
        return JSONResponse(data)

    # --- probes: the process itself, not its dependencies (those are on the status page,
    # and restarting this service would not fix them)
    @app.get("/healthz", tags=["probes"])
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", tags=["probes"])
    def readyz():
        return {"status": "ready", "version": __version__}

    return app


app = createApp()
