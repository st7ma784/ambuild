"""Tests for milestone 3: input files, saved recipes, submissions, the queue pages and the
agent API. Agents here use a "test" backend, so an agent of the demo stack running
against the same database never takes these submissions."""
import hashlib
from html import unescape
import json
import os
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from ambuild_web import queue
from ambuild_web.app import createApp
from ambuild_web.config import Settings

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs PostgreSQL and S3")

TOKEN = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def client():
    return TestClient(createApp(Settings.fromEnvironment()))


@pytest.fixture(scope="module", autouse=True)
def testBackend():
    original = list(queue.BACKENDS)
    queue.BACKENDS.append("test")
    yield
    queue.BACKENDS[:] = original


@pytest.fixture(scope="module")
def conn(client):
    import psycopg
    from psycopg.rows import dict_row

    client.get("/queue")  # creates the tables
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, autocommit=True) as c:
        yield c
        c.execute("DELETE FROM submissions WHERE backend = 'test' OR name LIKE %s", ("%" + TOKEN + "%",))
        c.execute("DELETE FROM agents WHERE name LIKE %s", ("%" + TOKEN + "%",))
        c.execute("DELETE FROM campaigns WHERE name LIKE %s", ("%" + TOKEN + "%",))
        c.execute("DELETE FROM sweeps WHERE name LIKE %s", ("%" + TOKEN + "%",))
        c.execute("DELETE FROM recipes WHERE name LIKE %s", ("%" + TOKEN + "%",))
        c.execute("DELETE FROM blobs WHERE name LIKE %s", ("%" + TOKEN + "%",))


@pytest.fixture(scope="module")
def blobs(client, conn):
    """Two uploaded files (unique content, so cleaning up never removes anyone else's)"""
    refs = {}
    for name in ("block-{0}.car".format(TOKEN), "block-{0}.csv".format(TOKEN)):
        r = client.post("/api/blobs", files={"file": (name, "content of {0}\n".format(name).encode())})
        assert r.status_code == 200, r.text
        refs[name.rsplit(".", 1)[1]] = r.json()["ref"]
    return refs


def agent(conn, name):
    """A new test agent's headers; the test queue starts empty for it"""
    conn.execute("UPDATE submissions SET state = 'cancelled' WHERE backend = 'test' AND state = 'queued'")
    token = queue.newToken()
    queue.registerAgent(conn, "{0}-{1}".format(name, TOKEN), "test", token)
    return {"Authorization": "Bearer " + token}


def recipe(blobs, name="recipe", **changes):
    body = {
        "recipe_version": 1,
        "name": "{0}-{1}".format(name, TOKEN),
        "cell": {"box": [20, 20, 20]},
        "fragments": [{"type": "A", "car": blobs["car"], "csv": blobs["csv"], "name": "block"}],
        "bond_types": ["A:a-A:a"],
        "stages": [{"op": "seed", "count": 2}, {"repeat": 3, "stages": [{"op": "grow", "count": 1}]}],
        "seed": 5,
    }
    body.update(changes)
    return body


def submit(client, blobs, **payload):
    payload.setdefault("recipe", recipe(blobs))
    payload.setdefault("backend", "test")
    r = client.post("/api/submissions", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def claimed(client, headers):
    return client.post("/api/agent/claim", headers=headers).json()["submission"]


def state(client, submissionId):
    return client.get("/api/submissions/{0}".format(submissionId)).json()["state"]


# --- files

def test_blobs_are_stored_once_by_content(client, blobs):
    content = "content of block-{0}.car\n".format(TOKEN).encode()
    digest = hashlib.sha256(content).hexdigest()
    assert blobs["car"] == "sha256:" + digest
    again = client.post("/api/blobs", files={"file": ("other-name-{0}.car".format(TOKEN), content)}).json()
    assert again["existed"] and again["name"] == "block-{0}.car".format(TOKEN)
    r = client.get("/api/blobs/" + digest)
    assert r.status_code == 200 and r.content == content and r.headers["x-content-sha256"] == digest
    assert client.get("/api/blobs/" + "0" * 64).status_code == 404
    assert client.post("/api/blobs", files={"file": ("empty.car", b"")}).status_code == 422
    assert digest in {b["sha256"] for b in client.get("/api/blobs").json()["blobs"]}


# --- recipes

def test_validate_names_missing_files(client, blobs):
    missing = "sha256:" + "ab" * 32
    body = recipe(blobs)
    body["fragments"][0]["csv"] = missing
    data = client.post("/api/recipes/validate", json=body).json()
    assert not data["valid"] and data["errors"] == ["fragments[0].csv: no uploaded file has this sha256"]
    ok = client.post("/api/recipes/validate", json=recipe(blobs)).json()
    assert ok["valid"] and ok["operations"] == 4
    bad = client.post("/api/recipes/validate", json=recipe(blobs, stages=[{"op": "seed"}])).json()
    assert bad["errors"] == ["stages[0].count: required"]


def test_saved_recipes_are_versioned(client, blobs):
    first = client.post("/api/recipes", json=recipe(blobs, "saved")).json()
    second = client.post("/api/recipes", json=recipe(blobs, "saved", seed=6)).json()
    assert (first["version"], second["version"]) == (1, 2)
    latest = [r for r in client.get("/api/recipes").json()["recipes"] if r["name"] == "saved-" + TOKEN]
    assert [r["version"] for r in latest] == [2]
    assert client.get("/api/recipes/{0}".format(first["recipe_id"])).json()["body"]["seed"] == 5
    assert client.post("/api/recipes", json={"name": "x"}).status_code == 422
    # submit a saved recipe by id
    sub = submit(client, blobs, recipe=None, recipe_id=second["recipe_id"])
    assert sub["recipe"]["seed"] == 6 and sub["recipe_id"] == second["recipe_id"]


# --- submissions

def test_submission_is_queued_with_a_run_id(client, blobs):
    sub = submit(client, blobs, seed=99, priority=3, owner="Tester")
    assert sub["state"] == "queued" and sub["seed"] == 99 and sub["priority"] == 3 and sub["owner"] == "Tester"
    uuid.UUID(sub["run_id"])
    listed = client.get("/api/submissions", params={"state": "queued"}).json()["submissions"]
    assert sub["submission_id"] in [s["submission_id"] for s in listed]


def test_invalid_submissions_are_rejected(client, blobs):
    r = client.post("/api/submissions", json={"recipe": recipe(blobs, cell={"box": [1]}), "backend": "test"})
    assert r.status_code == 422 and r.json()["errors"][0].startswith("cell.box")
    r = client.post("/api/submissions", json={"recipe": recipe(blobs), "backend": "moon"})
    assert r.status_code == 422 and "backend" in r.json()["errors"][0]
    assert client.post("/api/submissions", json={"recipe_id": 0}).status_code == 404
    assert client.post("/api/submissions", json={}).status_code == 422


# --- agents

def test_agent_calls_need_a_valid_token(client):
    assert client.post("/api/agent/claim").status_code == 401
    assert client.post("/api/agent/claim", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_agent_runs_a_submission_to_the_end(client, blobs, conn):
    headers = agent(conn, "runner")
    sub = submit(client, blobs, priority=100)
    got = claimed(client, headers)
    assert got["submission_id"] == sub["submission_id"] and got["recipe"] == sub["recipe"]
    assert got["run_id"] == sub["run_id"] and state(client, sub["submission_id"]) == "claimed"
    path = "/api/agent/submissions/{0}".format(sub["submission_id"])
    assert client.patch(path, json={"state": "running", "external_id": "host:42"}, headers=headers).json()["state"] == "running"
    beat = client.post("/api/agent/heartbeat", json={"host": "h", "active": [sub["submission_id"]]}, headers=headers)
    assert beat.json()["cancel"] == []
    assert client.patch(path, json={"state": "finished"}, headers=headers).status_code == 200
    row = client.get("/api/submissions/{0}".format(sub["submission_id"])).json()
    assert row["state"] == "finished" and row["external_id"] == "host:42" and row["started"] and row["finished"]
    assert client.patch(path, json={"state": "running"}, headers=headers).status_code == 409
    other = agent(conn, "other")
    assert client.patch(path, json={"state": "failed"}, headers=other).status_code == 404


def test_cancel_and_retry(client, blobs, conn):
    headers = agent(conn, "canceller")
    queued = submit(client, blobs)
    assert client.post("/api/submissions/{0}/cancel".format(queued["submission_id"])).json()["state"] == "cancelled"
    assert client.post("/api/submissions/{0}/cancel".format(queued["submission_id"])).status_code == 409

    sub = submit(client, blobs, priority=100)
    assert claimed(client, headers)["submission_id"] == sub["submission_id"]
    path = "/api/agent/submissions/{0}".format(sub["submission_id"])
    client.patch(path, json={"state": "running"}, headers=headers)
    assert client.post("/api/submissions/{0}/cancel".format(sub["submission_id"])).json()["state"] == "cancelling"
    beat = client.post("/api/agent/heartbeat", json={"active": [sub["submission_id"]]}, headers=headers).json()
    assert beat["cancel"] == [sub["submission_id"]]
    assert client.patch(path, json={"state": "running"}, headers=headers).status_code == 409
    client.patch(path, json={"state": "cancelled"}, headers=headers)
    assert state(client, sub["submission_id"]) == "cancelled"

    assert client.post("/api/submissions/{0}/retry".format(sub["submission_id"])).json()["state"] == "queued"
    again = claimed(client, headers)
    assert again["submission_id"] == sub["submission_id"] and again["attempts"] == 2
    assert again["run_id"] != sub["run_id"]  # each attempt is a new run
    client.patch(path, json={"state": "failed", "error": "boom"}, headers=headers)
    row = client.get("/api/submissions/{0}".format(sub["submission_id"])).json()
    assert row["state"] == "failed" and row["error"] == "boom"


def test_expired_claims_return_to_the_queue(client, blobs, conn):
    first, second = agent(conn, "quiet"), agent(conn, "lively")
    sub = submit(client, blobs, priority=100)
    assert claimed(client, first)["submission_id"] == sub["submission_id"]
    assert claimed(client, second) is None
    conn.execute("UPDATE submissions SET lease_expires = now() - interval '1 second' WHERE submission_id = %s",
                 (sub["submission_id"],))
    assert claimed(client, second)["submission_id"] == sub["submission_id"]


def test_runs_an_agent_forgot_are_failed(client, blobs, conn):
    headers = agent(conn, "forgetful")
    sub = submit(client, blobs, priority=100)
    claimed(client, headers)
    client.patch("/api/agent/submissions/{0}".format(sub["submission_id"]), json={"state": "running"}, headers=headers)
    client.post("/api/agent/heartbeat", json={"active": []}, headers=headers)
    row = client.get("/api/submissions/{0}".format(sub["submission_id"])).json()
    assert row["state"] == "failed" and "lost" in row["error"]


def test_a_released_claim_is_queued_again(client, blobs, conn):
    headers = agent(conn, "releaser")
    sub = submit(client, blobs, priority=100)
    claimed(client, headers)
    client.patch("/api/agent/submissions/{0}".format(sub["submission_id"]), json={"state": "queued"}, headers=headers)
    assert state(client, sub["submission_id"]) == "queued"
    client.post("/api/submissions/{0}/cancel".format(sub["submission_id"]))


# --- pages

def test_new_run_page(client, blobs):
    html = client.get("/submit").text
    assert '<textarea id="recipe"' in html and "block-{0}.car".format(TOKEN) in html
    assert 'data-ref="{0}"'.format(blobs["car"]) in html
    assert "<code>optimise</code>" in html and "<code>poreblazer</code>" in html  # the reference
    saved = client.post("/api/recipes", json=recipe(blobs, "page")).json()
    loaded = client.get("/submit", params={"recipe": saved["recipe_id"]}).text
    assert "page-{0}".format(TOKEN) in loaded


def test_check_button(client, blobs):
    ok = client.post("/submit/validate", data={"recipe": json.dumps(recipe(blobs))}).text
    assert "The recipe is valid: 4 operations" in ok
    bad = client.post("/submit/validate", data={"recipe": "{not json"}).text
    assert "Not valid JSON" in bad


def test_submit_form(client, blobs):
    r = client.post("/submit", data={"recipe": json.dumps(recipe(blobs, "form")), "seed": "12", "priority": "0",
                                     "backend": "test", "name": "", "action": "submit"}, follow_redirects=False)
    assert r.status_code == 303
    submissionId = int(re.search(r"/submissions/(\d+)", r.headers["location"]).group(1))
    page = client.get("/submissions/{0}".format(submissionId)).text
    assert "form-{0}".format(TOKEN) in page and "Queued" in page and 'hx-trigger="every 5s"' in page
    assert "form-{0}".format(TOKEN) in client.get("/queue").text
    r = client.post("/submissions/{0}/cancel".format(submissionId), follow_redirects=False)
    assert r.status_code == 303 and state(client, submissionId) == "cancelled"
    assert 'hx-trigger="every 5s"' not in client.get("/submissions/{0}".format(submissionId)).text
    # errors come back on the page
    r = client.post("/submit", data={"recipe": "[]", "backend": "test", "action": "submit"})
    assert r.status_code == 422 and "recipe: must be a JSON object" in r.text
    # save from the form
    r = client.post("/submit", data={"recipe": json.dumps(recipe(blobs, "formsave")), "action": "save"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/submit?recipe=")


def test_upload_from_the_page(client):
    name = "page-upload-{0}.car".format(TOKEN)
    html = client.post("/blobs/upload", files=[("files", (name, b"uploaded from the page\n"))]).text
    assert name in html and "just-added" in html


def test_run_page_links_its_submission(client, blobs, conn, recorded):
    sub = submit(client, blobs)
    client.post("/api/submissions/{0}/cancel".format(sub["submission_id"]))
    conn.execute("UPDATE submissions SET run_id = %s WHERE submission_id = %s", (recorded["c"], sub["submission_id"]))
    html = client.get("/runs/" + recorded["c"]).text
    assert "submission {0}".format(sub["submission_id"]) in html
    assert "/submit?submission={0}".format(sub["submission_id"]) in html


def test_recipe_text_is_readable_and_unchanged():
    from ambuild_web.submissions import recipeText

    body = {"stages": [{"count": 2, "op": "seed", "point": [1.5, -2, 3e-05]}], "name": "n [1,\n2]", "recipe_version": 1,
            "cell": {"box": [20, 20, 20]}}
    text = recipeText(body)
    assert json.loads(text) == body
    assert text.index('"recipe_version"') < text.index('"name"') < text.index('"cell"') < text.index('"stages"')
    assert '"box": [20, 20, 20]' in text and '"point": [1.5, -2, 3e-05]' in text
    assert text.index('"op"') < text.index('"count"')


# --- milestone 4: managing agents, resuming, status cards

def test_agents_are_added_and_revoked_through_the_api(client, blobs, conn):
    conn.execute("UPDATE submissions SET state = 'cancelled' WHERE backend = 'test' AND state = 'queued'")
    name = "api-{0}".format(TOKEN)
    created = client.post("/api/agents", json={"name": name, "backend": "test"})
    assert created.status_code == 201, created.text
    headers = {"Authorization": "Bearer " + created.json()["token"]}
    assert client.post("/api/agents", json={"name": name, "backend": "test"}).status_code == 409
    assert client.post("/api/agents", json={"name": "x", "backend": "moon"}).status_code == 409
    assert client.post("/api/agents", json={"name": "  ", "backend": "test"}).status_code == 422

    sub = submit(client, blobs)
    assert claimed(client, headers)["submission_id"] == sub["submission_id"]
    mine = client.get("/api/agent/submissions", headers=headers).json()["submissions"]
    assert [(s["submission_id"], s["state"]) for s in mine] == [(sub["submission_id"], "claimed")]
    assert mine[0]["recipe"]["name"] == sub["recipe"]["name"]

    revoked = client.post("/api/agents/{0}/revoke".format(created.json()["agent_id"])).json()
    assert revoked == {"name": name, "revoked": True}
    assert state(client, sub["submission_id"]) == "queued"  # its unstarted claim is released
    assert client.post("/api/agent/claim", headers=headers).status_code == 401
    again = client.post("/api/agents", json={"name": name, "backend": "test"})
    assert again.status_code == 201  # a revoked name can be used again, with a new token
    client.post("/api/submissions/{0}/cancel".format(sub["submission_id"]))


def test_resuming_sees_external_ids(client, blobs, conn):
    headers = agent(conn, "resumer")
    sub = submit(client, blobs)
    claimed(client, headers)
    path = "/api/agent/submissions/{0}".format(sub["submission_id"])
    client.patch(path, json={"state": "submitted", "external_id": "slurm:101/102"}, headers=headers)
    mine = client.get("/api/agent/submissions", headers=headers).json()["submissions"]
    assert [(s["state"], s["external_id"]) for s in mine] == [("submitted", "slurm:101/102")]
    page = client.get("/submissions/{0}".format(sub["submission_id"])).text
    assert "build 101, upload 102" in page
    client.patch(path, json={"state": "running"}, headers=headers)
    client.patch(path, json={"state": "finished"}, headers=headers)
    assert client.get("/api/agent/submissions", headers=headers).json()["submissions"] == []


def test_status_page_has_a_card_per_agent(client, conn):
    headers = agent(conn, "carded")
    client.post("/api/agent/heartbeat", headers=headers, json={
        "host": "login1", "version": "9.9", "summary": {
            "partitions": {"debug": {"default": True, "available": "up", "nodes": {"idle": 3, "allocated": 1}}},
            "last_error": "nothing much"}, "active": []})
    cards = {c["name"]: c for c in client.get("/api/status").json()["checks"]}
    card = cards["Agent carded-{0}".format(TOKEN)]
    assert card["state"] == "ok" and card["summary"].startswith("Heard from")
    assert card["facts"]["host"] == "login1" and card["facts"]["last error"] == "nothing much"
    assert card["facts"]["partition debug (default)"] == "up; 1 allocated, 3 idle"
    conn.execute("UPDATE agents SET last_heartbeat = now() - interval '2 hours' WHERE name = %s",
                 ("carded-" + TOKEN,))
    card = {c["name"]: c for c in client.get("/api/status").json()["checks"]}["Agent carded-{0}".format(TOKEN)]
    assert card["state"] == "fail" and "Not heard from for 2.0 h" in card["summary"]


def test_agents_page(client, conn):
    name = "page-{0}".format(TOKEN)
    html = client.post("/agents", data={"name": name, "backend": "test"}).text
    assert "Token for {0}".format(name) in html and "AMBUILD_AGENT_TOKEN=" in html
    token = re.search(r"AMBUILD_AGENT_TOKEN=(\S+)", html).group(1)
    assert queue.agentForToken(conn, token)["name"] == name
    listing = client.get("/agents").text
    assert name in listing and "AMBUILD_AGENT_TOKEN=" not in listing  # shown once only
    dup = client.post("/agents", data={"name": name, "backend": "test"})
    assert dup.status_code == 409 and "revoke it first" in dup.text
    agentId = queue.agentForToken(conn, token)["agent_id"]
    r = client.post("/agents/{0}/revoke".format(agentId), follow_redirects=False)
    assert r.status_code == 303 and queue.agentForToken(conn, token) is None


def test_new_run_page_says_which_backends_have_agents(client, conn):
    headers = agent(conn, "online")
    client.post("/api/agent/heartbeat", headers=headers, json={"active": []})
    html = client.get("/submit").text
    assert re.search(r'<option value="test"[^>]*>test</option>', html)  # the "test" agent is online
    conn.execute("UPDATE agents SET last_heartbeat = NULL WHERE backend = 'test'")
    html = client.get("/submit").text
    assert '>test (no agent online)</option>' in html


# --- milestone 5: sweeps

GRID = [{"name": "box", "path": "/cell/box", "all": True, "values": [20, 25, 30]},
        {"name": "grow", "path": "/stages/1/stages/0/count", "values": [1, 2, 3]}]


def makeSweep(client, blobs, name="sweep", **payload):
    payload.setdefault("recipe", recipe(blobs, name))
    payload.setdefault("parameters", GRID)
    payload.setdefault("backend", "test")
    payload.setdefault("name", "{0}-{1}".format(name, TOKEN))
    r = client.post("/api/sweeps", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_sweep_preview_and_problems(client, blobs):
    body = {"recipe": recipe(blobs), "parameters": GRID, "seeds": [1, 2], "preview": True}
    data = client.post("/api/sweeps", json=body).json()
    assert data["valid"] and data["runs"] == 18 and data["points"] == 9
    assert data["first"][0] == {"point": {"box": 20, "grow": 1}, "seed": 1}
    bad = client.post("/api/sweeps", json=dict(body, parameters=[dict(GRID[1], values=[1, 0])])).json()
    assert bad["errors"] == ["grow=0: stages[1].stages[0].count: must be at least 1"]
    bad = client.post("/api/sweeps", json=dict(body, parameters=[dict(GRID[0], path="/nowhere/x")])).json()
    assert bad["errors"][0].startswith("parameters[0].path")
    missing = recipe(blobs)
    missing["fragments"][0]["car"] = "sha256:" + "cd" * 32
    bad = client.post("/api/sweeps", json=dict(body, recipe=missing)).json()
    assert bad["errors"] == ["base recipe: fragments[0].car: no uploaded file has this sha256"]


def test_sweep_is_claimed_as_one_batch(client, blobs, conn):
    headers = agent(conn, "batcher")
    sweep = makeSweep(client, blobs)
    assert sweep["runs"] == 9
    single = submit(client, blobs)  # queued after the sweep
    batch = client.post("/api/agent/claim-batch", json={"limit": 100}, headers=headers).json()["submissions"]
    assert len(batch) == 9 and {s["sweep_id"] for s in batch} == {sweep["sweep_id"]}
    assert [s["recipe"]["cell"]["box"][0] for s in batch] == [20, 20, 20, 25, 25, 25, 30, 30, 30]
    assert [s["recipe"]["stages"][1]["stages"][0]["count"] for s in batch[:3]] == [1, 2, 3]
    alone = client.post("/api/agent/claim-batch", headers=headers).json()["submissions"]
    assert [s["submission_id"] for s in alone] == [single["submission_id"]]
    assert client.post("/api/agent/claim-batch", headers=headers).json()["submissions"] == []
    listed = client.get("/api/submissions").json()["submissions"]
    assert any(s["sweep_id"] == sweep["sweep_id"] and s["point"] == {"box": 20, "grow": 1} for s in listed)
    assert client.post("/api/sweeps/{0}/cancel".format(sweep["sweep_id"])).json() == {"cancelled": 9}
    client.post("/api/submissions/{0}/cancel".format(single["submission_id"]))


def test_sweep_page_plots_results_against_both_parameters(client, blobs, conn, recorded):
    small = [dict(GRID[0], values=[20, 25]), dict(GRID[1], values=[1, 2])]
    sweep = makeSweep(client, blobs, "plotted", parameters=small)
    runs = client.get("/api/sweeps/{0}".format(sweep["sweep_id"])).json()["runs"]
    assert [r["point"] for r in runs] == [{"box": 20, "grow": 1}, {"box": 20, "grow": 2},
                                         {"box": 25, "grow": 1}, {"box": 25, "grow": 2}]
    # two of its runs "finished" as recorded runs a and c, with their Poreblazer results
    for run, runId in zip(runs[:3:2], (recorded["a"], recorded["c"])):
        conn.execute("UPDATE submissions SET state = 'finished', run_id = %s WHERE submission_id = %s",
                     (runId, run["submission_id"]))
    data = client.get("/api/sweeps/{0}".format(sweep["sweep_id"])).json()["runs"]
    assert [r["results"]["surface_area_m2_g"] for r in data[:3:2]] == [1500.0, 2600.0]
    html = client.get("/sweeps/{0}".format(sweep["sweep_id"])).text
    specs = {m.group(1): json.loads(m.group(2)) for m in
             re.finditer(r'id="(sweep-[a-z]+)-data">(.*?)</script>', html, re.S)}
    assert set(specs) == {"sweep-box", "sweep-grow"}
    assert specs["sweep-box"]["series"] == [{"label": "grow=1", "x": [20, 25], "y": [1500.0, 2600.0]}]
    assert "surface area (m²/g) against box" in html and 'hx-trigger="every 10s"' in html
    density = client.get("/sweeps/{0}".format(sweep["sweep_id"]), params={"metric": "density"}).text
    assert "density (g/cm³) against grow" in density
    assert client.post("/api/sweeps/{0}/cancel".format(sweep["sweep_id"])).json() == {"cancelled": 2}
    assert client.post("/api/sweeps/{0}/retry".format(sweep["sweep_id"])).json() == {"queued": 2}
    client.post("/api/sweeps/{0}/cancel".format(sweep["sweep_id"]))
    assert "plotted-{0}".format(TOKEN) in client.get("/sweeps").text


def test_sweep_form_with_csv_rows(client, blobs):
    params = json.dumps([{"name": "box", "path": "/cell/box", "all": True},
                         {"name": "grow", "path": "/stages/1/stages/0/count"}])
    form = {"recipe": json.dumps(recipe(blobs, "csv")), "parameters": params, "seeds": "",
            "name": "csv-" + TOKEN, "backend": "test", "priority": "0"}
    csvFile = {"rows_csv": ("rows.csv", b"box,grow,seed\n22,1,5\n24,3,6\n", "text/csv")}
    preview = client.post("/sweeps/preview", data=form, files=csvFile).text
    assert re.search(r"2 runs: 2\s+points", preview) and "<code>24</code>" in preview
    r = client.post("/sweeps", data=form, files=csvFile, follow_redirects=False)
    assert r.status_code == 303
    sweepId = int(r.headers["location"].rsplit("/", 1)[1])
    runs = client.get("/api/sweeps/{0}".format(sweepId)).json()["runs"]
    assert [(r["point"], r["seed"]) for r in runs] == [({"box": 22, "grow": 1}, 5), ({"box": 24, "grow": 3}, 6)]
    copied = client.get("/sweeps/new", params={"sweep": sweepId}).text
    assert "csv-" + TOKEN in copied and "/stages/1/stages/0/count" in copied
    bad = client.post("/sweeps", data=dict(form, parameters="[{"), files=csvFile)
    assert bad.status_code == 422 and "parameters: not valid JSON" in bad.text
    paths = client.post("/sweeps/paths", data={"recipe": form["recipe"]}).text
    assert "<code>/cell/box</code>" in paths and "<code>/stages/1/repeat</code>" in paths
    client.post("/api/sweeps/{0}/cancel".format(sweepId))


# --- milestone 6: campaigns

def campaignSpec(**changes):
    s = {"parameters": [{"name": "box", "path": "/cell/box", "all": True, "type": "float", "low": 20, "high": 30},
                        {"name": "grow", "path": "/stages/1/stages/0/count", "type": "int", "low": 1, "high": 3}],
         "constraints": [{"metric": "density", "min": 0.1}], "objective": {"maximise": "surface_area_m2_g"},
         "replicates": 1, "initial_points": 2, "batch_size": 2, "budget": {"runs": 3}}
    s.update(changes)
    return s


def makeCampaign(client, blobs, name="campaign", **spec):
    r = client.post("/api/campaigns", json={"recipe": recipe(blobs, name), "spec": campaignSpec(**spec),
                                            "backend": "test", "name": "{0}-{1}".format(name, TOKEN)})
    assert r.status_code == 201, r.text
    return r.json()


def test_campaign_checks(client, blobs):
    ok = client.post("/api/campaigns", json={"recipe": recipe(blobs), "spec": campaignSpec(), "preview": True}).json()
    assert ok["valid"] and ok["first_round_runs"] == 2 and ok["spec"]["method"] == "tpe"
    bad = client.post("/api/campaigns", json={"recipe": recipe(blobs), "spec": campaignSpec(method="guess"),
                                              "preview": True})
    assert bad.status_code == 422 and bad.json()["errors"][0].startswith("method:")
    bad = client.post("/api/campaigns", json={"recipe": recipe(blobs), "backend": "moon", "spec": campaignSpec()})
    assert bad.status_code == 422 and "backend" in bad.json()["errors"][0]


def test_campaign_rounds_scores_and_states(client, blobs, conn, recorded):
    c = makeCampaign(client, blobs)
    cid = c["campaign_id"]
    detail = client.get("/api/campaigns/{0}".format(cid)).json()
    assert detail["trials"] == [] and detail["decision"] == {"stop": None, "propose": 2}
    bad = client.post("/api/campaigns/{0}/rounds".format(cid), json={"points": [{"box": 50, "grow": 1}]})
    assert bad.status_code == 409 and "box: must be a number from 20 to 30" in bad.json()["errors"][0]
    r = client.post("/api/campaigns/{0}/rounds".format(cid),
                    json={"points": [{"box": 22.5, "grow": 1}, {"box": 27.0, "grow": 3}], "proposed_by": "tester"})
    assert r.status_code == 201 and r.json()["round"] == 1 and r.json()["trials"] == [0, 1]
    detail = client.get("/api/campaigns/{0}".format(cid)).json()
    assert [t["score"]["state"] for t in detail["trials"]] == ["running", "running"]
    assert detail["decision"] == {"stop": None, "propose": 0} and detail["runs_used"] == 2
    assert detail["trials"][1]["runs"][0]["seed"] == 1
    assert client.post("/api/campaigns/{0}/rounds".format(cid),
                       json={"points": [{"box": 21, "grow": 2}, {"box": 22, "grow": 2}]}).status_code == 409  # budget
    # the two runs "finish" as recorded runs a (density 0.15, SA 1500) and c (0.2, 2600)
    for t, runId in zip(detail["trials"], (recorded["a"], recorded["c"])):
        conn.execute("UPDATE submissions SET state = 'finished', run_id = %s WHERE submission_id = %s",
                     (runId, t["runs"][0]["submission_id"]))
    detail = client.get("/api/campaigns/{0}".format(cid)).json()
    assert [t["score"]["value"] for t in detail["trials"]] == [1500.0, 2600.0]
    assert all(t["score"]["feasible"] for t in detail["trials"]) and detail["best"] == 1
    assert detail["decision"] == {"stop": None, "propose": 1}  # one run of budget left
    html = client.get("/campaigns/{0}".format(cid)).text
    assert 'id="campaign-progress-data"' in html and 'id="campaign-box-data"' in html
    assert "Trial 1 (round 1, proposed by tester)" in html
    # states
    assert client.post("/api/campaigns/{0}/pause".format(cid)).json() == {"state": "paused"}
    assert client.get("/api/campaigns/{0}".format(cid)).json()["decision"] is None
    assert client.post("/api/campaigns/{0}/rounds".format(cid), json={"points": [{"box": 21, "grow": 2}]}).status_code == 409
    assert client.post("/api/campaigns/{0}/resume".format(cid)).json() == {"state": "active"}
    assert client.post("/api/campaigns/{0}/finish".format(cid), json={"reason": "goal met"}).json() == {"state": "finished"}
    assert client.post("/api/campaigns/{0}/budget".format(cid), json={"runs": 4}).json() == {"state": "active", "budget": 7}
    assert client.post("/api/campaigns/{0}/stop".format(cid)).json() == {"state": "stopped"}
    assert client.post("/api/campaigns/{0}/budget".format(cid), json={"runs": 4}).status_code == 409
    listed = client.get("/api/campaigns", params={"state": "stopped"}).json()["campaigns"]
    assert any(x["campaign_id"] == cid and x["trials"] == 2 and x["runs"] == 2 for x in listed)


def test_campaign_pages_and_external_points(client, blobs, conn):
    html = unescape(client.get("/campaigns/new").text)
    assert "pore_limiting_diameter_a" in html and '"maximise": "density"' in html  # the Li-ion showcase
    form = {"recipe": json.dumps(recipe(blobs, "form")), "spec": json.dumps(campaignSpec(method="external")),
            "name": "external-" + TOKEN, "backend": "test"}
    assert "Ready: 2 parameters, method external" in client.post("/campaigns/check", data=form).text
    r = client.post("/campaigns", data=form, follow_redirects=False)
    assert r.status_code == 303
    cid = int(r.headers["location"].rsplit("/", 1)[1])
    page = client.get("/campaigns/{0}".format(cid)).text
    assert "Propose points yourself" in page and "waiting for points (external)" in page
    r = client.post("/campaigns/{0}/propose".format(cid), data={"points": '[{"box": 25, "grow": 2}]',
                                                                "proposed_by": "an agent"}, follow_redirects=False)
    assert r.status_code == 303
    trials = client.get("/api/campaigns/{0}".format(cid)).json()["trials"]
    assert [(t["params"], t["proposed_by"]) for t in trials] == [({"box": 25, "grow": 2}, "an agent")]
    assert "external-" + TOKEN in client.get("/campaigns").text
    client.post("/api/campaigns/{0}/stop".format(cid))
    created = client.post("/api/agents", json={"name": "controller-" + TOKEN, "backend": "campaigns"})
    assert created.status_code == 201
