"""The queue and what feeds it: input files (blobs), saved recipes, submissions and the
agents that run them (schema.sql). Submission states and who moves them are described in
docs/web-gui.md; agents claim work with FOR UPDATE SKIP LOCKED, as leases."""
import hashlib
import os
import secrets
import threading
import uuid

from psycopg.types.json import Jsonb

from ambuild import campaign as ab_campaign
from ambuild import recipe as ab_recipe

SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
LEASE_SECONDS = 120  # a claimed submission returns to the queue if its agent goes quiet this long
MAX_BLOB_BYTES = 20 * 1024 * 1024
BACKENDS = ["local", "slurm"]  # where runs run
CONTROLLER = "campaigns"  # the campaign controller's kind of agent token (it runs nothing)


def agentKinds():
    return BACKENDS + [CONTROLLER]


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
        "s.sweep_id, sw.name AS sweep_name, s.point, "
        "coalesce(s.seed, (s.recipe->>'seed')::bigint) AS seed_used, "
        "s.external_id, s.run_id, s.attempts, s.error, s.created, s.claimed, s.started, s.finished, "
        "a.name AS agent, (r.run_id IS NOT NULL) AS uploaded, r.status AS run_status "
        "FROM submissions s LEFT JOIN agents a USING (agent_id) LEFT JOIN runs r ON r.run_id = s.run_id "
        "LEFT JOIN sweeps sw ON sw.sweep_id = s.sweep_id "
        "{0} ORDER BY (s.state IN ('finished', 'failed', 'cancelled')), s.priority DESC, s.created DESC, "
        "s.submission_id DESC LIMIT %s".format(where), args + [limit]).fetchall()


def getSubmission(conn, submissionId):
    return conn.execute(
        "SELECT s.*, a.name AS agent, (r.run_id IS NOT NULL) AS uploaded, r.status AS run_status "
        "FROM submissions s LEFT JOIN agents a USING (agent_id) LEFT JOIN runs r ON r.run_id = s.run_id "
        "LEFT JOIN sweeps sw ON sw.sweep_id = s.sweep_id "
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


def createAgent(conn, name, backend):
    """A new agent and its token (returned once; stored hashed). A revoked agent's name can
    be used again, with a new token; a current agent's cannot."""
    if backend not in agentKinds():
        raise Conflict("backend must be one of {0}".format(", ".join(agentKinds())))
    existing = conn.execute("SELECT revoked FROM agents WHERE name = %s", (name,)).fetchone()
    if existing is not None and not existing["revoked"]:
        raise Conflict("An agent called {0} exists; revoke it first to replace its token".format(name))
    token = newToken()
    return registerAgent(conn, name, backend, token), token


def revokeAgent(conn, agentId):
    """Revoke the agent's token; its claimed but unstarted work returns to the queue"""
    row = conn.execute("UPDATE agents SET revoked = true WHERE agent_id = %s RETURNING name", (agentId,)).fetchone()
    if row is None:
        raise LookupError("No such agent")
    conn.execute("UPDATE submissions SET state = 'queued', agent_id = NULL, claimed = NULL, lease_expires = NULL, "
                 "updated = now() WHERE agent_id = %s AND state = 'claimed'", (agentId,))
    return row["name"]


def agentSubmissions(conn, agent):
    """The agent's unfinished submissions, for taking them up again after it restarts"""
    return conn.execute(
        "SELECT submission_id, name, state, external_id, run_id, seed, recipe, resources, attempts, owner "
        "FROM submissions WHERE agent_id = %s AND state IN ('claimed', 'submitted', 'running', 'cancelling') "
        "ORDER BY submission_id", (agent["agent_id"],)).fetchall()


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


def claimBatch(conn, agent, limit=500):
    """Queued submissions to start together (highest priority, then oldest first): if the
    first belongs to a sweep, all of that sweep's queued runs (up to limit), for one Slurm
    array job; otherwise that one. [] when the queue is empty."""
    requeueExpired(conn)
    first = conn.execute(
        "SELECT submission_id, sweep_id FROM submissions WHERE state = 'queued' AND backend = %s "
        "ORDER BY priority DESC, created, submission_id FOR UPDATE SKIP LOCKED LIMIT 1",
        (agent["backend"],)).fetchone()
    if first is None:
        return []
    if first["sweep_id"] is None:
        ids = [first["submission_id"]]
    else:
        ids = [r["submission_id"] for r in conn.execute(
            "SELECT submission_id FROM submissions WHERE state = 'queued' AND backend = %s AND sweep_id = %s "
            "ORDER BY sweep_index, submission_id FOR UPDATE SKIP LOCKED LIMIT %s",
            (agent["backend"], first["sweep_id"], limit)).fetchall()]
    rows = conn.execute(
        "UPDATE submissions SET state = 'claimed', agent_id = %s, claimed = now(), attempts = attempts + 1, "
        "lease_expires = now() + make_interval(secs => %s), updated = now() "
        "WHERE submission_id = ANY(%s) RETURNING *",
        (agent["agent_id"], LEASE_SECONDS, ids)).fetchall()
    return sorted(rows, key=lambda r: (r["sweep_index"] if r["sweep_index"] is not None else -1, r["submission_id"]))


# --- sweeps

def createSweep(conn, name, recipe, spec, runs, backend, owner, priority=0):
    """The sweep and a submission per run (ambuild.sweep.expand)"""
    sweep = conn.execute(
        "INSERT INTO sweeps (name, recipe, spec, backend, owner) VALUES (%s, %s, %s, %s, %s) RETURNING *",
        (name, Jsonb(recipe), Jsonb(spec), backend, owner)).fetchone()
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO submissions (name, recipe, recipe_sha256, seed, backend, resources, priority, owner, run_id, "
            "sweep_id, point, sweep_index) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [("{0} #{1}".format(name, run["index"] + 1), Jsonb(run["recipe"]), ab_recipe.recipeHash(run["recipe"]),
              run["seed"], backend, Jsonb(run["recipe"].get("resources", {})), priority, owner, str(uuid.uuid4()),
              sweep["sweep_id"], Jsonb(run["point"]), run["index"]) for run in runs])
    return sweep


def listSweeps(conn, limit=200):
    return conn.execute(
        "SELECT sw.sweep_id, sw.name, sw.backend, sw.owner, sw.created, count(s.*) AS runs, "
        "count(*) FILTER (WHERE s.state = 'finished') AS finished, "
        "count(*) FILTER (WHERE s.state IN ('failed', 'cancelled')) AS failed, "
        "count(*) FILTER (WHERE s.state IN ('queued', 'claimed', 'submitted', 'running', 'cancelling')) AS active "
        "FROM sweeps sw LEFT JOIN submissions s ON s.sweep_id = sw.sweep_id "
        "GROUP BY sw.sweep_id ORDER BY sw.created DESC LIMIT %s", (limit,)).fetchall()


def getSweep(conn, sweepId):
    return conn.execute("SELECT * FROM sweeps WHERE sweep_id = %s", (sweepId,)).fetchone()


def sweepRuns(conn, sweepId):
    return conn.execute(
        "SELECT s.submission_id, s.state, s.point, s.sweep_index, s.run_id, s.error, s.external_id, "
        "coalesce(s.seed, (s.recipe->>'seed')::bigint) AS seed, (r.run_id IS NOT NULL) AS uploaded "
        "FROM submissions s LEFT JOIN runs r ON r.run_id = s.run_id WHERE s.sweep_id = %s "
        "ORDER BY s.sweep_index, s.submission_id", (sweepId,)).fetchall()


def cancelSweep(conn, sweepId):
    """Cancel every unfinished run of the sweep; returns how many"""
    return conn.execute(
        "UPDATE submissions SET state = CASE WHEN state IN ('queued', 'claimed') THEN 'cancelled' "
        "ELSE 'cancelling' END, finished = CASE WHEN state IN ('queued', 'claimed') THEN now() END, "
        "lease_expires = NULL, updated = now() "
        "WHERE sweep_id = %s AND state IN ('queued', 'claimed', 'submitted', 'running')", (sweepId,)).rowcount


def retrySweep(conn, sweepId):
    """Queue the sweep's failed and cancelled runs again, each as a new run; returns how many"""
    return conn.execute(
        "UPDATE submissions SET state = 'queued', run_id = gen_random_uuid(), agent_id = NULL, external_id = NULL, "
        "error = NULL, lease_expires = NULL, claimed = NULL, started = NULL, finished = NULL, updated = now() "
        "WHERE sweep_id = %s AND state IN ('failed', 'cancelled')", (sweepId,)).rowcount


# --- campaigns

def createCampaign(conn, name, recipe, spec, backend, owner):
    return conn.execute(
        "INSERT INTO campaigns (name, recipe, spec, backend, owner) VALUES (%s, %s, %s, %s, %s) RETURNING *",
        (name, Jsonb(recipe), Jsonb(spec), backend, owner)).fetchone()


def listCampaigns(conn, states=(), limit=200):
    where, args = "", []
    if states:
        where, args = "WHERE c.state = ANY(%s)", [list(states)]
    return conn.execute(
        "SELECT c.campaign_id, c.name, c.backend, c.state, c.message, c.owner, c.created, c.finished, "
        "c.spec->>'method' AS method, (c.spec->'budget'->>'runs')::int AS budget, "
        "(SELECT count(*) FROM trials t WHERE t.campaign_id = c.campaign_id) AS trials, "
        "(SELECT count(*) FROM submissions s JOIN trials t USING (trial_id) WHERE t.campaign_id = c.campaign_id) "
        "AS runs FROM campaigns c {0} ORDER BY c.created DESC LIMIT %s".format(where), args + [limit]).fetchall()


def getCampaign(conn, campaignId):
    return conn.execute("SELECT * FROM campaigns WHERE campaign_id = %s", (campaignId,)).fetchone()


def campaignTrials(conn, campaignId):
    return conn.execute("SELECT * FROM trials WHERE campaign_id = %s ORDER BY number", (campaignId,)).fetchall()


def campaignRuns(conn, campaignId):
    """Every run of the campaign's trials"""
    return conn.execute(
        "SELECT s.submission_id, s.trial_id, s.state, s.run_id, s.error, "
        "coalesce(s.seed, (s.recipe->>'seed')::bigint) AS seed, (r.run_id IS NOT NULL) AS uploaded "
        "FROM submissions s JOIN trials t USING (trial_id) LEFT JOIN runs r ON r.run_id = s.run_id "
        "WHERE t.campaign_id = %s ORDER BY t.number, s.sweep_index", (campaignId,)).fetchall()


def createRound(conn, campaign, points, runs, proposedBy):
    """Queue a round: a sweep of the points (runs: ambuild.sweep-style dicts, len(points)
    times the replicate seeds, in point order) and a trial per point; returns (round, sweep, trials)"""
    last = conn.execute("SELECT coalesce(max(round), 0) AS r, coalesce(max(number), -1) AS n FROM trials "
                        "WHERE campaign_id = %s", (campaign["campaign_id"],)).fetchone()
    rnd, number = last["r"] + 1, last["n"] + 1
    spec = campaign["spec"]
    sweepSpec = {"parameters": [{k: p[k] for k in ("name", "path", "all") if k in p} for p in spec["parameters"]],
                 "rows": points, "seeds": ab_campaign.seeds(spec)}
    sweep = createSweep(conn, "{0} · round {1}".format(campaign["name"], rnd), campaign["recipe"], sweepSpec, runs,
                        campaign["backend"], campaign["owner"])
    perPoint = len(runs) // len(points)
    trials = []
    for i, params in enumerate(points):
        t = conn.execute(
            "INSERT INTO trials (campaign_id, number, round, params, proposed_by, sweep_id) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
            (campaign["campaign_id"], number + i, rnd, Jsonb(params), proposedBy, sweep["sweep_id"])).fetchone()
        conn.execute("UPDATE submissions SET trial_id = %s WHERE sweep_id = %s AND sweep_index BETWEEN %s AND %s",
                     (t["trial_id"], sweep["sweep_id"], i * perPoint, (i + 1) * perPoint - 1))
        trials.append(t)
    return rnd, sweep, trials


def setCampaignState(conn, campaignId, state, message=None):
    """pause, resume, stop (cancelling its unfinished runs) or finish a campaign"""
    allowed = {"paused": ("active",), "active": ("paused", "finished"), "stopped": ("active", "paused"),
               "finished": ("active",)}[state]
    row = conn.execute(
        "UPDATE campaigns SET state = %s, message = coalesce(%s, message), "
        "finished = CASE WHEN %s IN ('stopped', 'finished') THEN now() END "
        "WHERE campaign_id = %s AND state = ANY(%s) RETURNING *",
        (state, message, state, campaignId, list(allowed))).fetchone()
    if row is None:
        raise Conflict("A campaign cannot go to {0} from its state".format(state))
    if state == "stopped":
        for s in conn.execute("SELECT DISTINCT sweep_id FROM trials WHERE campaign_id = %s AND sweep_id IS NOT NULL",
                              (campaignId,)).fetchall():
            cancelSweep(conn, s["sweep_id"])
    return row


def extendCampaign(conn, campaignId, runs):
    """Add runs to the budget; a finished campaign becomes active again"""
    row = conn.execute(
        "UPDATE campaigns SET spec = jsonb_set(spec, '{budget,runs}', to_jsonb((spec->'budget'->>'runs')::int + %s)), "
        "state = CASE WHEN state = 'finished' THEN 'active' ELSE state END, "
        "finished = CASE WHEN state = 'finished' THEN NULL ELSE finished END "
        "WHERE campaign_id = %s AND state <> 'stopped' RETURNING *", (runs, campaignId)).fetchone()
    if row is None:
        raise Conflict("A stopped campaign cannot be extended")
    return row
