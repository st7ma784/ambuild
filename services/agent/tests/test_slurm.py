"""The slurm backend's submit command, without a cluster: submit_build.sh is a fake. (It
is run against a real cluster by deploy/slurm/test/check_agent.py.)"""
import subprocess
import types

from ambuild_agent import backends
from ambuild_agent.agent import Config


def started(tmp_path, monkeypatch, **settings):
    """The command the backend runs for one submission, and its job"""
    calls = []

    def run(command, **kw):
        calls.append(command)
        return types.SimpleNamespace(returncode=0, stdout="run r: build job 11, upload job 12\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    config = Config(api_url="http://web", token="t", backend="slurm", runs_root=str(tmp_path / "runs"),
                    slurm_dir="/opt/ambuild/deploy/slurm", partition="cpu", **settings)
    sub = {"submission_id": 3, "run_id": "run-3", "recipe": {"name": "r", "stages": []}, "seed": None,
           "resources": {"cpus": 4}}
    job = backends.SlurmBackend(config).start(sub)
    return calls[-1], job


def test_a_build_is_submitted_with_its_resources(tmp_path, monkeypatch):
    command, job = started(tmp_path, monkeypatch)
    assert command[0].endswith("submit_build.sh") and command[1] == "--recipe"
    assert command[2].endswith("recipe.json") and "--cpus-per-task=4" in command and "--partition=cpu" in command
    assert job.external_id == "slurm:11/12"


def test_the_xtb_fan_out_is_added_when_the_agent_is_set_to(tmp_path, monkeypatch):
    """AMBUILD_AGENT_XTB: each build's last checkpoint is checked with xTB, as a child run"""
    command, job = started(tmp_path, monkeypatch, xtb=True)
    assert command[1:3] == ["--xtb", "--recipe"] and job.external_id == "slurm:11/12"
    monkeypatch.setenv("AMBUILD_API_URL", "http://web")
    monkeypatch.setenv("AMBUILD_AGENT_TOKEN", "t")
    assert Config.fromEnvironment().xtb is False
    monkeypatch.setenv("AMBUILD_AGENT_XTB", "1")
    assert Config.fromEnvironment().xtb is True
