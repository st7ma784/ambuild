"""The API agents use to take and report work (services/agent). Every call carries the
agent's token (Authorization: Bearer <token>); tokens are stored hashed and made on the Agents page (/agents) or with
`ambuild-web init --agent NAME:BACKEND`. An agent sees only its backend's queue and its
own submissions, and never connects to the database itself."""
from fastapi import APIRouter, Body, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.encoders import jsonable_encoder

from ambuild_web import identity, queue
from ambuild_web.submissions import _connect

router = APIRouter(prefix="/api/agent", tags=["agents"])


def currentAgent(request: Request):
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Needs an agent token", headers={"WWW-Authenticate": "Bearer"})
    with _connect(request) as conn:
        agent = queue.agentForToken(conn, token.strip())
    if agent is None:
        raise HTTPException(401, "Unknown or revoked agent token", headers={"WWW-Authenticate": "Bearer"})
    return agent


def _submissionForAgent(row):
    """What an agent needs to run a submission"""
    return jsonable_encoder({k: row[k] for k in ("submission_id", "name", "recipe", "recipe_sha256", "seed", "sweep_id",
                                                  "resources", "run_id", "attempts", "owner")})


@router.post("/heartbeat")
def heartbeat(request: Request, payload: dict = Body(default={}), agent=Depends(currentAgent)):
    """I am alive: host, version, capabilities, a summary of the agent's resources, and
    "active", the submissions it is running (others it had started are marked failed).
    Returns the submissions the agent should stop (cancelled by users)."""
    with _connect(request) as conn:
        cancel = queue.heartbeat(conn, agent, host=payload.get("host"), version=payload.get("version"),
                                 capabilities=payload.get("capabilities"), summary=payload.get("summary"),
                                 active=payload.get("active"))
    return {"agent": agent["name"], "cancel": cancel, "lease_seconds": queue.LEASE_SECONDS}


@router.get("/submissions")
def mySubmissions(request: Request, agent=Depends(currentAgent)):
    """This agent's unfinished submissions (to take up again after a restart)"""
    with _connect(request) as conn:
        rows = queue.agentSubmissions(conn, agent)
    return {"submissions": [jsonable_encoder(r) for r in rows]}


@router.post("/claim")
def claim(request: Request, agent=Depends(currentAgent)):
    """The next queued submission for this agent's backend, or {"submission": null}"""
    with _connect(request) as conn:
        row = queue.claim(conn, agent)
    return {"submission": _submissionForAgent(row) if row else None}


@router.post("/claim-batch")
def claimBatch(request: Request, payload: dict = Body(default={}), agent=Depends(currentAgent)):
    """Queued submissions to start together: all of one sweep's queued runs (up to
    "limit"), for one Slurm array job, or the next single submission; {"submissions": []}
    when the queue is empty"""
    limit = max(1, min(int(payload.get("limit") or 500), 1000))
    with _connect(request) as conn:
        rows = queue.claimBatch(conn, agent, limit)
    return {"submissions": [_submissionForAgent(r) for r in rows]}


@router.patch("/submissions/{submissionId}")
def update(request: Request, submissionId: int, payload: dict = Body(...), agent=Depends(currentAgent)):
    """Report a submission's state: running, submitted, finished, failed, cancelled, or queued
    (released without starting); with external_id (e.g. host:pid) and error"""
    state = payload.get("state")
    with _connect(request) as conn:
        try:
            row = queue.agentUpdate(conn, agent, submissionId, state, externalId=payload.get("external_id"),
                                    error=(payload.get("error") or None) and str(payload["error"])[:4000])
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except queue.Conflict as exc:
            raise HTTPException(409, str(exc))
    return {"submission_id": row["submission_id"], "state": row["state"]}


# --- managing agents (pages and API). No sign-in yet: anyone on the lab network can add
# or revoke an agent, as they can submit; a token only lets an agent take and report work.
manage = APIRouter()


def _createAgent(request, name, backend):
    name = identity.cleanName(name).replace(" ", "-")
    if not name:
        raise HTTPException(422, "Give the agent a name")
    with _connect(request) as conn:
        try:
            row, token = queue.createAgent(conn, name, backend)
        except queue.Conflict as exc:
            raise HTTPException(409, str(exc))
    return row, token


def _revokeAgent(request, agentId):
    with _connect(request) as conn:
        try:
            return queue.revokeAgent(conn, agentId)
        except LookupError as exc:
            raise HTTPException(404, str(exc))


@manage.post("/api/agents", tags=["agents"], status_code=201)
def apiCreateAgent(request: Request, payload: dict = Body(...)):
    """Add an agent: {"name": ..., "backend": "local" | "slurm"}. The token is returned
    once, here; only its hash is kept."""
    row, token = _createAgent(request, payload.get("name", ""), payload.get("backend", ""))
    return {"agent_id": row["agent_id"], "name": row["name"], "backend": row["backend"], "token": token}


@manage.post("/api/agents/{agentId}/revoke", tags=["agents"])
def apiRevokeAgent(request: Request, agentId: int):
    """Revoke an agent's token; work it claimed but had not started is queued again"""
    return {"name": _revokeAgent(request, agentId), "revoked": True}


def _agentsPage(request, created=None, token=None, error=None, status_code=200):
    with _connect(request) as conn:
        agents = queue.listAgents(conn)
    page = request.app.state.render(request, "agents.html", agents=agents, backends=queue.BACKENDS,
                                    created=created, token=token, error=error,
                                    api_url=str(request.base_url).rstrip("/"))
    page.status_code = status_code
    return page


@manage.get("/agents", response_class=HTMLResponse, include_in_schema=False)
def agentsPage(request: Request):
    return _agentsPage(request)


@manage.post("/agents", response_class=HTMLResponse, include_in_schema=False)
def agentsCreate(request: Request, name: str = Form(""), backend: str = Form("")):
    try:
        row, token = _createAgent(request, name, backend)
    except HTTPException as exc:
        return _agentsPage(request, error=exc.detail, status_code=exc.status_code)
    return _agentsPage(request, created=row, token=token)


@manage.post("/agents/{agentId}/revoke", include_in_schema=False)
def agentsRevoke(request: Request, agentId: int):
    _revokeAgent(request, agentId)
    return RedirectResponse("/agents", status_code=303)
