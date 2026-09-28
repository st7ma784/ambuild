"""The slurmrest backend without a cluster: slurmrestd and the web API are fakes. (It is
run against a real slurmrestd by deploy/slurm/test/check_slurmrest.py.)"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

from ambuild_agent.agent import Config, Job
from ambuild_agent.slurmrest import SlurmRestBackend, SlurmRestError, minutes, stagedScript

SLURM_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "deploy", "slurm")
BLOB = b"a block file\n"
DIGEST = hashlib.sha256(BLOB).hexdigest()


class FakeRest:
    url = "http://slurm-head:6820"

    def __init__(self, jobs=None, failOn=None):
        self.submitted, self.deleted, self.jobs, self.failOn = [], [], jobs or {}, failOn
        self.paths, self.down = [], False

    def call(self, method, path, body=None, missingOk=False):
        if self.down:
            raise SlurmRestError("slurmrestd at {0} unreachable: refused".format(self.url))
        self.paths.append(path)
        if path == "/openapi/v3":
            return {"paths": {"/slurm/v0.0.41/ping": {}, "/slurm/v0.0.42/ping": {}, "/slurm/v0.0.43/ping": {}}}
        assert path.startswith("/slurm/v0.0.4"), path
        if method == "POST":
            if self.failOn and self.failOn in body["job"]["name"]:
                raise SlurmRestError("refused")
            self.submitted.append(body["job"])
            return {"job_id": 100 + len(self.submitted), "errors": [], "warnings": []}
        if method == "DELETE":
            self.deleted.append(path.rsplit("/", 1)[1])
            return {}
        jobId = path.rsplit("/", 1)[1]
        return {"jobs": self.jobs[jobId]} if jobId in self.jobs else None

    errors = staticmethod(lambda reply: "")


class FakeApi:
    def __init__(self, runs):
        self.runs = runs

    def call(self, method, path, body=None):
        assert (method, path) == ("GET", "/api/agent/submissions")
        return {"submissions": [dict(submission_id=k, run_status=s, run_error=e) for k, (s, e) in self.runs.items()]}


@pytest.fixture
def backend(tmp_path):
    config = Config(api_url="http://web", token="t", backend="slurmrest", directory=str(tmp_path / "agent"),
                    runs_root="/cluster/runs", slurm_dir=SLURM_DIR, slurmrestd_url=FakeRest.url, slurm_jwt="jwt",
                    slurm_user="chem1", partition="cpu", slurm_setup="source /opt/ambuild/bin/activate",
                    slurm_env={"POREBLAZER_EXE": "/opt/pb/poreblazer.exe"}, slurm_job={"account": "chem"})
    b = SlurmRestBackend(config)
    b.version, b.rest = "v0.0.41", FakeRest()
    os.makedirs(b.blobs)
    with open(os.path.join(b.blobs, DIGEST), "wb") as f:
        f.write(BLOB)
    return b


def submission(n, **extra):
    recipe = {"name": "r{0}".format(n), "fragments": [{"car": "sha256:" + DIGEST}], "stages": []}
    return dict({"submission_id": n, "run_id": "run-{0}".format(n), "recipe": recipe, "seed": n, "sweep_id": None,
                 "resources": {}}, **extra)


def test_time_limits_in_minutes():
    assert [minutes(t) for t in ("30", "90:30", "01:00:00", "00:00:20", "2-00", "1-02:30", "1-00:00:01")] == \
        [30, 91, 60, 1, 2880, 1590, 1441]


def test_a_build_is_a_build_job_then_an_upload_job(backend):
    sub = submission(7, resources={"cpus": 4, "gpus": 1, "memory_mb": 8000, "time": "02:00:00"})
    [job] = backend.startBatch([sub])
    build, upload = backend.rest.submitted
    assert (job.external_id, job.rundir) == ("slurm:101/102", "/cluster/runs/run-7")
    assert build["name"] == "ambuild-7" and build["partition"] == "cpu" and build["account"] == "chem"
    assert (build["cpus_per_task"], build["tres_per_job"], build["memory_per_node"], build["time_limit"]) == \
        (4, "gres/gpu:1", {"set": True, "number": 8000}, {"set": True, "number": 120})
    env = dict(e.split("=", 1) for e in build["environment"])
    assert env["AMBUILD_RUN_DIR"] == "/cluster/runs/run-7" and env["AMBUILD_SEED"] == "7"
    assert env["AMBUILD_RECIPE"] == "/cluster/runs/.ambuild-work/run-7/recipe.json"
    assert env["AMBUILD_LIVE_UPLOAD_EVERY"] == "60" and env["POREBLAZER_EXE"] == "/opt/pb/poreblazer.exe"
    assert not any(k in env for k in ("DATABASE_URL", "AWS_SECRET_ACCESS_KEY", "AMBUILD_AGENT_TOKEN"))
    assert build["standard_output"] == "/cluster/runs/%x-%j.out"
    assert "source /opt/ambuild/bin/activate" in build["script"] and "exec python -m ambuild.recipe" in build["script"]
    assert upload["dependency"] == "afterany:101" and upload["time_limit"] == {"set": True, "number": 30}
    assert dict(e.split("=", 1) for e in upload["environment"])["AMBUILD_RUN_DIR"] == "/cluster/runs/run-7"


def test_a_sweep_is_one_array_job(backend):
    jobs = backend.startBatch([submission(n, sweep_id=3) for n in (1, 2, 3)])
    build, upload = backend.rest.submitted
    assert build["array"] == "0-2%50" and build["name"] == "ambuild-sweep-3"
    assert [j.external_id for j in jobs] == ["slurm:101_0/102", "slurm:101_1/102", "slurm:101_2/102"]
    assert "AMBUILD_RUN_LIST=/cluster/runs/.ambuild-work/array-run-1.rundirs" in upload["environment"]


def test_a_refused_upload_job_cancels_the_build(backend):
    backend.rest.failOn = "upload"
    with pytest.raises(SlurmRestError):
        backend.startBatch([submission(1)])
    assert backend.rest.deleted == ["101"]


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("sha256sum"), reason="needs bash and coreutils")
def test_the_script_stages_its_files_and_checks_them(tmp_path):
    target = tmp_path / "work" / "recipe.json"
    content = json.dumps({"name": "x"}).encode()
    template = "#!/bin/bash\n#SBATCH --time=00:10:00\nset -euo pipefail\ncat {0}\n".format(target)
    script = stagedScript(template, {str(target): content, str(tmp_path / "blobs" / "b"): BLOB})
    assert script.index("#SBATCH") < script.index("ambuild_stage") < script.index("set -euo pipefail")
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env={"PATH": os.environ["PATH"]})
    assert out.returncode == 0, out.stderr
    assert out.stdout == content.decode() and (tmp_path / "blobs" / "b").read_bytes() == BLOB
    # a file that arrives damaged is not used
    target.unlink()
    bad = script.replace(hashlib.sha256(content).hexdigest(), "0" * 64)
    out = subprocess.run(["bash", "-c", bad], capture_output=True, text=True)
    assert out.returncode == 1 and "does not match its sha256" in out.stderr and not target.exists()


def test_job_states_from_slurmrestd(backend):
    backend.rest.jobs = {
        "101": [{"job_id": 101, "job_state": ["RUNNING"], "array_job_id": {"set": True, "number": 0},
                 "array_task_id": {"set": False, "number": 0}}],
        "201": [{"job_id": 202, "job_state": ["RUNNING"], "array_job_id": {"set": True, "number": 201},
                 "array_task_id": {"set": True, "number": 0}},
                {"job_id": 201, "job_state": ["PENDING"], "array_job_id": {"set": True, "number": 201},
                 "array_task_id": {"set": False, "number": 0}, "array_task_string": "1-2%50"}],
    }
    backend.beginPass()
    assert backend.states(["101", "201_0", "201_2", "999"]) == {"101": "RUNNING", "201_0": "RUNNING",
                                                                 "201_2": "PENDING", "999": "GONE"}


def test_outcomes_come_from_the_uploaded_runs(backend):
    backend.attach(FakeApi({1: ("finished", None), 2: ("failed", "RuntimeError: no"), 3: (None, None)}))
    backend.rest.jobs = {"50": [], "51": []}  # both ended long ago: slurmctld forgot them
    results = {}
    for n in (1, 2, 3):
        job = Job(submission=submission(n), rundir="", workdir="", handle={"build": "50", "upload": "51"},
                  state="running")
        backend.beginPass()
        results[n] = backend.poll(job) + (backend.uploaded(job),)
    assert results[1] == ("finished", None, True, True)
    assert results[2] == ("failed", "RuntimeError: no", True, True)
    assert results[3][0] == "failed" and "not uploaded" in results[3][1] and results[3][3] is False


def test_not_ready_while_slurmrestd_is_down(backend):
    backend.rest.down = True
    backend.beginPass()
    assert backend.ready() is False and "unreachable" in backend.problem
    assert backend.summary() == {"problem": backend.problem}


def test_a_token_slurmctld_refuses_is_reported_as_such():
    """slurmrestd passes a JWT on, and slurmctld's refusal comes back as HTTP 511 (error
    1007); an unknown job is a 404 with errors; a 404 without them is not an ended job"""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from ambuild_agent.slurmrest import SlurmRest

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            code, body = {"/diag": (511, {"errors": [{"error": "Protocol authentication error", "error_number": 1007}]}),
                          "/job/9": (404, {"jobs": [], "errors": [{"error": "Invalid job id specified"}]}),
                          }.get(self.path, (404, None))
            self.send_response(code)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode() if body else b"Not Found")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        config = Config(api_url="", token="", slurmrestd_url="http://127.0.0.1:{0}".format(server.server_port),
                        slurm_jwt="bad", slurm_user="chem1")
        rest = SlurmRest(config)
        with pytest.raises(SlurmRestError, match="rejected the token for user 'chem1'"):
            rest.call("GET", "/diag")
        assert rest.call("GET", "/job/9", missingOk=True) is None
        with pytest.raises(SlurmRestError, match="HTTP 404"):
            rest.call("GET", "/wrong/path", missingOk=True)
    finally:
        server.shutdown()


def running(build="65", upload="66"):
    return Job(submission=submission(1), rundir="", workdir="", handle={"build": build, "upload": upload},
               state="running")


def test_a_restarted_agent_chooses_the_api_version_before_polling(backend):
    """A restarted agent polls the jobs it takes up before anything else: that must use the
    right API version, not end them (this once asked for /slurm/None/job/65)"""
    backend.version = None
    backend.rest.jobs = {"65": [{"job_id": 65, "job_state": ["RUNNING"], "array_job_id": {"set": True, "number": 0}}],
                         "66": [{"job_id": 66, "job_state": ["PENDING"], "array_job_id": {"set": True, "number": 0}}]}
    backend.beginPass()
    assert backend.poll(running()) == ("running", None, False)
    assert backend.version == "v0.0.42"  # the newest tested one offered
    assert backend.rest.paths[:2] == ["/openapi/v3", "/slurm/v0.0.42/job/65"]


def test_unknown_states_are_not_an_end(backend):
    backend.rest.down = True
    backend.beginPass()
    assert backend.poll(running()) == ("running", None, False)


def test_a_cancel_slurmrestd_refused_is_tried_again(backend):
    backend.rest.jobs = {"65": [{"job_id": 65, "job_state": ["RUNNING"], "array_job_id": {"set": True, "number": 0}}],
                         "66": [{"job_id": 66, "job_state": ["PENDING"], "array_job_id": {"set": True, "number": 0}}]}
    job = running()
    backend.rest.down = True
    backend.cancel(job)
    assert job.handle["cancel_pending"] and backend.rest.deleted == []
    backend.rest.down = False
    backend.beginPass()
    backend.poll(job)
    assert backend.rest.deleted == ["65"] and "cancel_pending" not in job.handle
