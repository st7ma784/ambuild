"""Tests for milestone 3: input files, saved recipes, submissions, the queue pages and the
agent API. Agents here use a "test" backend, so an agent of the demo stack running
against the same database never takes these submissions."""
import hashlib
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
