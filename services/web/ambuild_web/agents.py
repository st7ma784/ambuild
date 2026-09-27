"""The API agents use to take and report work (services/agent). Every call carries the
agent's token (Authorization: Bearer <token>); tokens are stored hashed and made with
`ambuild-web init --agent NAME:BACKEND`. An agent sees only its backend's queue and its
own submissions, and never connects to the database itself."""
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder

from ambuild_web import queue
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
    return jsonable_encoder({k: row[k] for k in ("submission_id", "name", "recipe", "recipe_sha256", "seed",
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


@router.post("/claim")
def claim(request: Request, agent=Depends(currentAgent)):
    """The next queued submission for this agent's backend, or {"submission": null}"""
    with _connect(request) as conn:
        row = queue.claim(conn, agent)
    return {"submission": _submissionForAgent(row) if row else None}


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
