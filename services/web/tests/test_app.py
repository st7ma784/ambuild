"""Tests for ambuild-web milestone 0: probes, the status page and API, and the owner cookie.

Tests marked "services" need the PostgreSQL and S3 of deploy/docker-compose.yml, with the
tables and bucket created (ambuild-upload --init): DATABASE_URL, AMBUILD_S3_BUCKET,
S3_ENDPOINT_URL and the AWS_* credentials in the environment. The rest run anywhere.
"""
import dataclasses
import hashlib
import os
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from ambuild_web import checks
from ambuild_web.app import createApp
from ambuild_web.config import Settings, redact

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(os.path.dirname(HERE), "ambuild_web", "static")
services = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs PostgreSQL and S3")


@pytest.fixture
def settings():
    return Settings.fromEnvironment()


def client(settings):
    return TestClient(createApp(settings))


# --- no services needed

def test_probes():
    c = client(Settings())
    assert c.get("/healthz").json() == {"status": "ok"}
    assert c.get("/readyz").json()["status"] == "ready"


def test_unconfigured_is_failed_with_the_reason():
    data = client(Settings()).get("/api/status").json()
    assert data["state"] == "fail"
    summaries = {c["name"]: c["summary"] for c in data["checks"]}
    assert summaries == {"PostgreSQL": "DATABASE_URL is not set", "Object storage": "AMBUILD_S3_BUCKET is not set"}


def test_redact():
    assert redact("postgresql://ambuild:s3cret@db:5432/x failed") == "postgresql://ambuild:***@db:5432/x failed"
    assert redact("host=db password=s3cret user=a") == "host=db password=*** user=a"
    assert "s3cret" not in redact("password='s3cret word' host=db")


def test_unreachable_database_is_failed_without_the_password():
    s = Settings(database_url="postgresql://ambuild:topsecretpw@127.0.0.1:1/ambuild", check_timeout=1)
    check = checks.checkPostgres(s)
    assert check.state == "fail"
    assert check.summary.startswith("Cannot connect")
    assert "topsecretpw" not in check.summary


def test_unreachable_storage_is_failed():
    s = Settings(s3_bucket="b", s3_endpoint_url="http://127.0.0.1:9", check_timeout=1)
    check = checks.checkS3(s)
    assert check.state == "fail"
    assert check.summary.startswith("Cannot reach the storage")


def test_owner_cookie():
    c = client(Settings())
    r = c.post("/owner", data={"name": "  Ada <Lovelace>  ", "next": "/status"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/status"
    assert c.cookies.get("ambuild_owner").strip('"') == "Ada Lovelace"
    page = c.get("/status").text
    assert 'value="Ada Lovelace"' in page and "You are" in page
    # clearing the name removes the cookie
    c.post("/owner", data={"name": "", "next": "/status"}, follow_redirects=False)
    assert not c.cookies.get("ambuild_owner")


@pytest.mark.parametrize("target", ["https://example.com/x", "//example.com/x", "javascript:alert(1)"])
def test_owner_redirect_stays_on_the_site(target):
    r = client(Settings()).post("/owner", data={"name": "a", "next": target}, follow_redirects=False)
    assert r.headers["location"] == "/"


def test_vendored_files_match_their_recorded_checksums():
    with open(os.path.join(STATIC, "VENDOR.md")) as f:
        recorded = dict(re.findall(r"^\| `([^`]+)` \|.*`([0-9a-f]{64})` \|$", f.read(), re.M))
    assert {"htmx.min.js", "uPlot.iife.min.js", "uPlot.min.css"} <= set(recorded)
    c = client(Settings())
    for name, sha in recorded.items():
        assert hashlib.sha256(c.get("/static/" + name).content).hexdigest() == sha, name


def test_status_page_renders_without_javascript():
    page = client(Settings()).get("/status").text
    assert "PostgreSQL" in page and "Object storage" in page and "Failed" in page
    assert 'hx-get="/status/cards"' in page and page.count('id="status-cards"') == 1


# --- against the Compose services

@services
def test_all_green(settings):
    data = client(settings).get("/api/status").json()
    postgres, storage = data["checks"][:2]  # then a card per agent, which may be offline here
    assert postgres["state"] == storage["state"] == "ok", data
    assert postgres["facts"]["server"] and "runs" in postgres["facts"]
    assert storage["facts"]["bucket"] == settings.s3_bucket


@services
def test_probe_object_is_removed(settings):
    import boto3

    checks.checkS3(settings)
    s3 = boto3.client("s3", endpoint_url=settings.s3_endpoint_url or None)
    listing = s3.list_objects_v2(Bucket=settings.s3_bucket, Prefix=settings.s3_prefix + "_ambuild-web-probe/")
    assert listing.get("KeyCount", 0) == 0


@services
def test_database_without_tables_needs_attention(settings):
    import psycopg

    name = "ambuild_empty_" + uuid.uuid4().hex[:8]
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute('CREATE DATABASE "{0}"'.format(name))
    try:
        url = settings.database_url.rsplit("/", 1)[0] + "/" + name
        check = checks.checkPostgres(dataclasses.replace(settings, database_url=url))
        assert check.state == "warn"
        assert "ambuild-upload --init" in check.summary and "runs" in check.summary
    finally:
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            conn.execute('DROP DATABASE "{0}"'.format(name))


@services
def test_missing_bucket_is_failed_with_the_fix(settings):
    check = checks.checkS3(dataclasses.replace(settings, s3_bucket="no-such-bucket-" + uuid.uuid4().hex[:8]))
    assert check.state == "fail"
    assert "does not exist" in check.summary and "--init" in check.summary


@services
def test_wrong_credentials_are_failed(settings, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "wrong-" + uuid.uuid4().hex)
    check = checks.checkS3(settings)
    assert check.state == "fail"
    assert "Access denied" in check.summary or "Cannot reach" in check.summary


@services
def test_cards_partial(settings):
    html = client(settings).get("/status/cards").text
    assert html.lstrip().startswith("<div id=\"status-cards\"") or 'id="status-cards"' in html
    assert "OK" in html


def test_templates_and_static_files_are_utf8():
    """Jinja reads templates as UTF-8; a file saved in another encoding breaks its page"""
    package = os.path.dirname(STATIC)
    for folder in ("templates", "static"):
        for name in os.listdir(os.path.join(package, folder)):
            with open(os.path.join(package, folder, name), "rb") as f:
                f.read().decode("utf-8")  # raises if not
