"""A few calls to the web GUI's API, with the standard library only (for the demo scripts
and the end-to-end check in this directory)."""
import json
import os
import time
import urllib.error
import urllib.request
import uuid

API = os.environ.get("AMBUILD_API_URL", "http://web:8000").rstrip("/")


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode() or "null")


def get(path):
    with urllib.request.urlopen(API + path, timeout=60) as response:
        return response.read()


def upload(path):
    """Upload a file; returns its reference"""
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        content = f.read()
    body = b"".join([
        "--{0}\r\n".format(boundary).encode(),
        'Content-Disposition: form-data; name="file"; filename="{0}"\r\n'.format(os.path.basename(path)).encode(),
        b"Content-Type: application/octet-stream\r\n\r\n", content, b"\r\n",
        "--{0}--\r\n".format(boundary).encode(),
    ])
    request = urllib.request.Request(API + "/api/blobs", data=body, method="POST",
                                     headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())["ref"]


def waitForWeb(seconds=120):
    deadline = time.time() + seconds
    while True:
        try:
            get("/healthz")
            return
        except (urllib.error.URLError, OSError):
            if time.time() > deadline:
                raise
            time.sleep(2)


def demoRecipe(blocksDir, name="Demo: methane-benzene network", seed=6, stages=None):
    """The demo builds' recipe (make_runs.py), with its building blocks uploaded"""
    refs = {n: upload(os.path.join(blocksDir, n)) for n in ("ch4.car", "ch4.csv", "benzene2.car", "benzene2.csv")}
    return {
        "recipe_version": 1,
        "name": name,
        "description": "Methane and benzene: seeded, grown and zipped, then analysed with Poreblazer.",
        "cell": {"box": [25, 25, 25]},
        "fragments": [
            {"type": "A", "car": refs["ch4.car"], "csv": refs["ch4.csv"], "name": "ch4"},
            {"type": "B", "car": refs["benzene2.car"], "csv": refs["benzene2.csv"], "name": "benzene2"},
        ],
        "params": None,
        "bond_types": ["A:a-B:a", "B:a-B:a"],
        "stages": stages or [
            {"op": "seed", "count": 6},
            {"repeat": 2, "stages": [
                {"op": "grow", "count": 5, "max_tries": 50},
                {"op": "zip", "bond_margin": 1.0, "bond_angle_margin": 30},
            ]},
            {"op": "poreblazer", "threads": 2},
        ],
        "seed": seed,
    }
