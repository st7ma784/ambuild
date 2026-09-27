"""Check the uploaded runs through the web GUI for run_test.sh: every run (and its child
runs) is browsable, and every file downloads with its recorded sha256.

    check_web.py RUN_ID [RUN_ID ...]

AMBUILD_WEB_URL: the web GUI (default http://web:8000). Only the standard library.
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("AMBUILD_WEB_URL", "http://web:8000").rstrip("/")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return r.status, r.read(), dict(r.headers)


def getJson(path):
    status, body, _ = get(path)
    assert status == 200, (path, status)
    return json.loads(body)


def waitForWeb():
    for _ in range(60):
        try:
            if get("/healthz")[0] == 200:
                return
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(2)
    raise SystemExit("The web GUI at {0} did not answer".format(BASE))


def checkRun(runId):
    run = getJson("/api/runs/" + runId)
    status, page, _ = get("/runs/" + runId)
    assert status == 200 and runId.encode() in page, runId
    checked = 0
    for f in run["files"]:
        status, body, headers = get("/api/runs/{0}/files/{1}".format(runId, urllib.parse.quote(f["path"])))
        digest = hashlib.sha256(body).hexdigest()
        assert status == 200 and digest == f["sha256"], (runId, f["path"], digest, f["sha256"])
        assert headers.get("X-Content-SHA256", headers.get("x-content-sha256")) == f["sha256"]
        checked += 1
    return run, checked


def main(runIds):
    waitForWeb()
    seen = []
    queue = list(runIds)
    while queue:
        runId = queue.pop(0)
        if runId in seen:
            continue
        run, checked = checkRun(runId)
        seen.append(runId)
        queue += [c["run_id"] for c in run["children"]]
        print("web: run {0} ({1}): page ok, {2} files downloaded with matching sha256".format(
            runId, run["summary"]["status"], checked))
    listedIds = {r["run_id"] for r in getJson("/api/runs?children=1&page=1")["runs"]}
    missing = [r for r in seen if r not in listedIds]
    assert not missing, ("not in the run list", missing)
    print("web: all {0} runs browsable and listed".format(len(seen)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
