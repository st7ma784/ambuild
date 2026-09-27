"""The queue and what feeds it: input files (blobs), saved recipes, submissions and the
agents that run them (schema.sql). Submission states and who moves them are described in
docs/web-gui.md; agents claim work with FOR UPDATE SKIP LOCKED, as leases."""
import hashlib
import os
import secrets
import threading
import uuid

from psycopg.types.json import Jsonb

from ambuild import recipe as ab_recipe

SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
LEASE_SECONDS = 120  # a claimed submission returns to the queue if its agent goes quiet this long
MAX_BLOB_BYTES = 20 * 1024 * 1024
BACKENDS = ["local"]  # "slurm" arrives with the Slurm agent (milestone 4)

ACTIVE = ("queued", "claimed", "submitted", "running", "cancelling")
FINAL = ("finished", "failed", "cancelled")
# state -> the states an agent may move its submission to
AGENT_MOVES = {
    "claimed": {"queued", "submitted", "running", "failed"},
    "submitted": {"running", "finished", "failed", "cancelled"},
    "running": {"running", "finished", "failed", "cancelled"},
    "cancelling": {"cancelled", "finished", "failed"},
}

_schemaLock = threading.Lock()
_schemaReady = False


class Conflict(Exception):
    """A change that the submission's state does not allow"""


def ensureSchema(conn):
    """Create the tables if they are not there (once per process)"""
    global _schemaReady
    if _schemaReady:
        return
    with _schemaLock:
        if not _schemaReady:
            applySchema(conn)
            _schemaReady = True


def applySchema(conn):
    with open(SCHEMA_FILE, encoding="utf-8") as f:
        sql = f.read()
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('ambuild-web schema'))")
    conn.execute(sql)
    conn.commit()


# --- blobs

def blobKey(settings, sha256):
    return "{0}blobs/sha256/{1}".format(settings.s3_prefix, sha256)


def getBlob(conn, sha256):
    return conn.execute("SELECT * FROM blobs WHERE sha256 = %s", (sha256,)).fetchone()


def listBlobs(conn, limit=200):
    return conn.execute("SELECT * FROM blobs ORDER BY created DESC LIMIT %s", (limit,)).fetchall()


def addBlob(conn, sha256, name, size, uri, owner):
    conn.execute(
        "INSERT INTO blobs (sha256, name, size, uri, owner) VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (sha256) DO NOTHING", (sha256, name, size, uri, owner))
    return getBlob(conn, sha256)


def missingBlobs(conn, digests):
    """The digests with no stored blob"""
    if not digests:
        return []
    found = {r["sha256"] for r in conn.execute("SELECT sha256 FROM blobs WHERE sha256 = ANY(%s)",
                                                (list(digests),)).fetchall()}
    return [d for d in digests if d not in found]


# --- recipes

def saveRecipe(conn, body, owner):
    """Save body as the next version of its name"""
    row = conn.execute(
        "INSERT INTO recipes (name, version, body, sha256, owner) "
        "SELECT %s, coalesce(max(version), 0) + 1, %s, %s, %s FROM recipes WHERE name = %s RETURNING *",
        (body["name"], Jsonb(body), ab_recipe.recipeHash(body), owner, body["name"])).fetchone()
    return row


def listRecipes(conn):
    """The latest version of each saved recipe"""
    return conn.execute(
        "SELECT DISTINCT ON (name) recipe_id, name, version, sha256, owner, created FROM recipes "
        "ORDER BY name, version DESC").fetchall()


def getRecipe(conn, recipeId):
    return conn.execute("SELECT * FROM recipes WHERE recipe_id = %s", (recipeId,)).fetchone()


# --- submissions

def createSubmission(conn, recipe, owner, seed=None, backend="local", priority=0, recipeId=None, name=None):
    return conn.execute(
        "INSERT INTO submissions (name, recipe, recipe_sha256, recipe_id, seed, backend, resources, priority, "
        "owner, run_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (name or recipe["name"], Jsonb(recipe), ab_recipe.recipeHash(recipe), recipeId, seed, backend,
         Jsonb(recipe.get("resources", {})), priority, owner, str(uuid.uuid4()))).fetchone()


def listSubmissions(conn, states=(), limit=200):
    where, args = "", []
    if states:
        where = "WHERE s.state = ANY(%s)"
        args.append(list(states))
    return conn.execute(
        "SELECT s.submission_id, s.name, s.recipe_sha256, s.seed, s.backend, s.state, s.priority, s.owner, "
        "coalesce(s.seed, (s.recipe->>'seed')::bigint) AS seed_used, "
        "s.external_id, s.run_id, s.attempts, s.error, s.created, s.claimed, s.started, s.finished, "
        "a.name AS agent, (r.run_id IS NOT NULL) AS uploaded, r.status AS run_status "
        "FROM submissions s LEFT JOIN agents a USING (agent_id) LEFT JOIN runs r ON r.run_id = s.run_id "
        "{0} ORDER BY (s.state IN ('finished', 'failed', 'cancelled')), s.priority DESC, s.created DESC, "
        "s.submission_id DESC LIMIT %s".format(where), args + [limit]).fetchall()


def getSubmission(conn, submissionId):
    return conn.execute(
        "SELECT s.*, a.name AS agent, (r.run_id IS NOT NULL) AS uploaded, r.status AS run_status "
        "FROM submissions s LEFT JOIN agents a USING (agent_id) LEFT JOIN runs r ON r.run_id = s.run_id "
        "WHERE s.submission_id = %s", (submissionId,)).fetchone()


def submissionForRun(conn, runId):
    return conn.execute("SELECT submission_id, name, seed, state FROM submissions WHERE run_id = %s",
                        (runId,)).fetchone()


def cancel(conn, submissionId):
    """Cancel: at once if no agent has started it, else ask its agent (cancelling)"""
    row = conn.execute(
        "UPDATE submissions SET state = CASE WHEN state IN ('queued', 'claimed') THEN 'cancelled' "
        "ELSE 'cancelling' END, finished = CASE WHEN state IN ('queued', 'claimed') THEN now() END, "
        "lease_expires = NULL, updated = now() "
        "WHERE submission_id = %s AND state IN ('queued', 'claimed', 'submitted', 'running') RETURNING state",
        (submissionId,)).fetchone()
    if row is None:
        raise Conflict("Only queued or running submissions can be cancelled")
    return row["state"]


def retry(conn, submissionId):
    """Queue a failed or cancelled submission again, as a new run"""
    row = conn.execute(
        "UPDATE submissions SET state = 'queued', run_id = %s, agent_id = NULL, external_id = NULL, error = NULL, "
        "lease_expires = NULL, claimed = NULL, started = NULL, finished = NULL, updated = now() "
        "WHERE submission_id = %s AND state IN ('failed', 'cancelled') RETURNING state",
        (str(uuid.uuid4()), submissionId)).fetchone()
    if row is None:
        raise Conflict("Only failed or cancelled submissions can be retried")
    return row["state"]


# --- agents

def hashToken(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def newToken():
    return secrets.token_urlsafe(32)


def registerAgent(conn, name, backend, token):
    """Create the agent, or give it this backend and token (and un-revoke it)"""
    return conn.execute(
        "INSERT INTO agents (name, backend, token_sha256) VALUES (%s, %s, %s) "
        "ON CONFLICT (name) DO UPDATE SET backend = excluded.backend, token_sha256 = excluded.token_sha256, "
        "revoked = false RETURNING agent_id, name, backend", (name, backend, hashToken(token))).fetchone()


def agentForToken(conn, token):
    if not token:
        return None
    return conn.execute("SELECT * FROM agents WHERE token_sha256 = %s AND NOT revoked",
                        (hashToken(token),)).fetchone()


def listAgents(conn):
    """Every agent; live: it sent a heartbeat within a lease"""
    return conn.execute(
        "SELECT agent_id, name, backend, revoked, host, version, capabilities, summary, last_heartbeat, "
        "extract(epoch FROM now() - last_heartbeat)::float AS seconds_since, "
        "(NOT revoked AND last_heartbeat > now() - make_interval(secs => %s)) IS TRUE AS live, "
        "(SELECT count(*) FROM submissions s WHERE s.agent_id = a.agent_id AND s.state IN "
        "('claimed', 'submitted', 'running', 'cancelling')) AS active "
        "FROM agents a ORDER BY name", (LEASE_SECONDS,)).fetchall()


def heartbeat(conn, agent, host=None, version=None, capabilities=None, summary=None, active=None):
    """Record that the agent is alive, renew its leases; returns the submissions it should cancel.

    active: the submissions the agent is running; any other it had started are lost (the
    agent restarted) and are marked failed."""
    if active is not None:
        conn.execute(
            "UPDATE submissions SET state = 'failed', error = 'The agent lost this run (did it restart?)', "
            "finished = now(), updated = now() WHERE agent_id = %s AND state IN ('submitted', 'running', 'cancelling') "
            "AND NOT submission_id = ANY(%s)", (agent["agent_id"], [int(i) for i in active]))
    conn.execute(
        "UPDATE agents SET last_heartbeat = now(), host = %s, version = %s, capabilities = %s, summary = %s "
        "WHERE agent_id = %s",
        (host, version, Jsonb(capabilities) if capabilities is not None else None,
         Jsonb(summary) if summary is not None else None, agent["agent_id"]))
    conn.execute(
        "UPDATE submissions SET lease_expires = now() + make_interval(secs => %s) "
        "WHERE agent_id = %s AND state = 'claimed'", (LEASE_SECONDS, agent["agent_id"]))
    return [r["submission_id"] for r in conn.execute(
        "SELECT submission_id FROM submissions WHERE agent_id = %s AND state = 'cancelling' ORDER BY submission_id",
        (agent["agent_id"],)).fetchall()]


def requeueExpired(conn):
    """Return claims whose agent stopped heart-beating to the queue"""
    return conn.execute(
        "UPDATE submissions SET state = 'queued', agent_id = NULL, claimed = NULL, lease_expires = NULL, "
        "updated = now() WHERE state = 'claimed' AND lease_expires < now()").rowcount


def claim(conn, agent):
    """The next queued submission for the agent's backend (highest priority, then oldest), or None"""
    requeueExpired(conn)
    return conn.execute(
        "UPDATE submissions SET state = 'claimed', agent_id = %s, claimed = now(), attempts = attempts + 1, "
        "lease_expires = now() + make_interval(secs => %s), updated = now() "
        "WHERE submission_id = (SELECT submission_id FROM submissions WHERE state = 'queued' AND backend = %s "
        "ORDER BY priority DESC, created, submission_id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *",
        (agent["agent_id"], LEASE_SECONDS, agent["backend"])).fetchone()


def agentUpdate(conn, agent, submissionId, state, externalId=None, error=None):
    """An agent reporting on its submission; raises LookupError (not its own) or Conflict"""
    row = conn.execute("SELECT state, agent_id FROM submissions WHERE submission_id = %s FOR UPDATE",
                       (submissionId,)).fetchone()
    if row is None or row["agent_id"] != agent["agent_id"]:
        raise LookupError("No such submission for this agent")
    if state not in AGENT_MOVES.get(row["state"], set()):
        raise Conflict("Cannot go from {0} to {1}".format(row["state"], state))
    if state == "queued":  # released unstarted
        return conn.execute(
            "UPDATE submissions SET state = 'queued', agent_id = NULL, claimed = NULL, lease_expires = NULL, "
            "updated = now() WHERE submission_id = %s RETURNING *", (submissionId,)).fetchone()
    return conn.execute(
        "UPDATE submissions SET state = %s, external_id = coalesce(%s, external_id), error = coalesce(%s, error), "
        "lease_expires = NULL, "
        "started = CASE WHEN %s IN ('submitted', 'running') THEN coalesce(started, now()) ELSE started END, "
        "finished = CASE WHEN %s IN ('finished', 'failed', 'cancelled') THEN now() END, updated = now() "
        "WHERE submission_id = %s RETURNING *",
        (state, externalId, error, state, state, submissionId)).fetchone()
