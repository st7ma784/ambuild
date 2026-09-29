"""Tests for milestone 1: the run list, run pages, files, events and comparison."""
import hashlib
import json
import re
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from ambuild_web.app import createApp
from ambuild_web.config import Settings


@pytest.fixture(scope="module")
def client():
    return TestClient(createApp(Settings.fromEnvironment()))


def listed(client, **params):
    return client.get("/api/runs", params=params).json()


def ids(data):
    return {r["run_id"] for r in data["runs"]}


# --- the list

def test_list_shows_top_level_runs_with_child_results(client, recorded):
    data = listed(client, q=recorded["token"])
    assert ids(data) == {recorded["a"], recorded["b"], recorded["c"]}  # the child run is hidden
    alpha = next(r for r in data["runs"] if r["run_id"] == recorded["a"])
    assert alpha["children"] == 1
    assert alpha["label"] == "webtest-{0}-alpha.py".format(recorded["token"])
    assert alpha["num_particles"] == 90 and alpha["last_step"] == 3
    assert alpha["surface_area_m2_g"] == 1500.0  # its own result at step 3 is newer than the child's at 2


def test_list_includes_child_runs_on_request(client, recorded):
    assert recorded["child"] in ids(listed(client, q=recorded["token"], children=1))


def test_list_filters(client, recorded):
    t = recorded["token"]
    assert ids(listed(client, q=t, status="failed")) == {recorded["b"]}
    assert ids(listed(client, q=t, status=["failed", "finished"])) == {recorded["a"], recorded["b"], recorded["c"]}
    assert ids(listed(client, q=t, min_sa=2000)) == {recorded["c"]}
    assert ids(listed(client, q=t, max_pld=8)) == {recorded["a"]}
    assert ids(listed(client, q=t, poreblazer=1)) == {recorded["a"], recorded["c"]}
    assert ids(listed(client, q=recorded["b"][:8])) == {recorded["b"]}  # run id prefix
    assert ids(listed(client, q="node-gamma")) >= {recorded["c"]}  # host


def test_list_sorts_and_pages(client, recorded):
    t = recorded["token"]
    order = [r["run_id"] for r in listed(client, q=t, sort="surface_area", dir="desc")["runs"]]
    assert order[:2] == [recorded["c"], recorded["a"]]  # the failed run, with none, sorts last
    assert order[-1] == recorded["b"]
    assert listed(client, q=t, page=2)["runs"] == []
    assert listed(client, q=t, sort="nonsense")["total"] == 3  # unknown sorts fall back safely


def test_list_page_renders(client, recorded):
    html = client.get("/runs", params={"q": recorded["token"]}).text
    assert "3 runs" in html and "webtest-{0}-gamma.py".format(recorded["token"]) in html
    assert 'name="run" value="{0}"'.format(recorded["a"]) in html  # compare checkboxes
    assert "Failed" in html and "1,500.0" in html


def test_bad_filter_value_is_rejected(client):
    assert client.get("/api/runs", params={"min_sa": "lots"}).status_code == 422


# --- a run

def test_run_page(client, recorded):
    html = client.get("/runs/" + recorded["a"]).text
    for text in ("webtest-{0}-alpha.py".format(recorded["token"]), "Build steps", "Poreblazer", "Files",
                 "Provenance", "Events", "25.0 × 25.0 × 25.0 Å", "Related runs", recorded["child"][:8]):
        assert text in html, text
    charts = re.findall(r'<script type="application/json" id="([^"]+)-data">', html)
    assert "steps-num_particles" in charts and "psd" in charts and "psd_cumulative" in charts
    spec = json.loads(re.search(r'id="psd-data">(.*?)</script>', html, re.S).group(1))
    assert len(spec["series"]) == 2  # the run's own result and its child's


def test_failed_run_shows_its_error(client, recorded):
    html = client.get("/runs/" + recorded["b"]).text
    assert "deliberate failure" in html and "No Poreblazer results" in html


def test_child_run_links_to_its_parent(client, recorded):
    html = client.get("/runs/" + recorded["child"]).text
    assert "Started from" in html and "/runs/" + recorded["a"] in html


def test_unknown_runs_are_404(client, recorded):
    assert client.get("/runs/not-a-uuid").status_code == 404
    assert client.get("/runs/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/runs/00000000-0000-0000-0000-000000000000").status_code == 404


def test_run_api(client, recorded):
    data = client.get("/api/runs/" + recorded["a"]).json()
    assert data["summary"]["run_id"] == recorded["a"]
    assert [s["step"] for s in data["steps"]] == [1, 2, 3]
    assert {p["surface_area_m2_g"] for p in data["pore_results"]} == {1500.0, 1400.0}
    assert [c["run_id"] for c in data["children"]] == [recorded["child"]]
    assert data["run"]["run_json"]["random"]["seed"] == 11


def test_events_page_and_api(client, recorded):
    events = client.get("/api/runs/{0}/events".format(recorded["a"]), params={"type": "step"}).json()["events"]
    assert [e["step"] for e in events] == [1, 2, 3]
    partial = client.get("/runs/{0}/events".format(recorded["a"]), params={"type": "pore_result"}).text
    assert "pore_result" in partial and "run_started" not in partial


# --- files: every download matches its recorded sha256

def test_every_file_downloads_intact(client, recorded):
    for runId in (recorded["a"], recorded["child"], recorded["b"], recorded["c"]):
        listedFiles = client.get("/api/runs/" + runId).json()["files"]
        assert {f["path"] for f in listedFiles} >= set(recorded["files"][runId])
        for f in listedFiles:
            r = client.get("/api/runs/{0}/files/{1}".format(runId, quote(f["path"])))
            assert r.status_code == 200, f["path"]
            assert hashlib.sha256(r.content).hexdigest() == f["sha256"] == r.headers["x-content-sha256"]
            if f["path"] in recorded["files"][runId]:
                assert r.content == recorded["files"][runId][f["path"]]


def test_awkward_file_name_and_inline_view(client, recorded):
    path = "notes/odd name & more.txt"
    r = client.get("/runs/{0}/files/{1}".format(recorded["a"], quote(path)), params={"inline": 1})
    assert r.status_code == 200 and r.text == "a file with an awkward name\n"
    assert r.headers["content-type"].startswith("text/plain") and r.headers["content-disposition"].startswith("inline")
    download = client.get("/runs/{0}/files/final.xyz".format(recorded["a"]))
    assert download.headers["content-disposition"].startswith("attachment")


@pytest.mark.parametrize("path", ["missing.txt", "../" + "x", "..%2F..%2Fetc%2Fpasswd", "runs/other/step_1.pkl.gz"])
def test_only_recorded_files_are_served(client, recorded, path):
    assert client.get("/runs/{0}/files/{1}".format(recorded["a"], path)).status_code == 404


# --- compare

def test_compare(client, recorded):
    html = client.get("/compare", params=[("run", recorded["a"]), ("run", recorded["c"])]).text
    assert "Compare 2 runs" in html
    seedRow = re.search(r'<tr class="differs">\s*<th scope="row">seed.*?</tr>', html, re.S)
    assert seedRow and "11" in seedRow.group(0) and "13" in seedRow.group(0)
    boxRow = re.search(r'<tr >\s*<th scope="row">box \(Å\)', html) or re.search(r'<tr>\s*<th scope="row">box', html)
    assert boxRow  # same box: not highlighted
    assert 'id="steps-num_particles-data"' in html and 'id="psd-data"' in html


def test_compare_needs_two_runs(client, recorded):
    assert client.get("/compare", params={"run": recorded["a"]}).status_code == 422
    assert client.get("/compare", params=[("run", recorded["a"]), ("run", recorded["a"])]).status_code == 422


# --- structures (milestone 2)

def test_structures_api_lists_checkpoints_in_step_order(client, recorded):
    data = client.get("/api/runs/{0}/structures".format(recorded["a"])).json()
    assert data["box"] == [25.0, 25.0, 25.0]
    assert [f["step"] for f in data["frames"]] == [1, 2, 3]
    assert all(f["kind"] == "structure" for f in data["frames"])
    frame = client.get(data["frames"][2]["url"])
    assert frame.status_code == 200 and frame.headers["content-type"].startswith("text/plain")
    lines = frame.text.splitlines()
    assert lines[0] == "6" and 'Lattice="25.000000' in lines[1] and "step=3" in lines[1]
    assert lines[2].split()[4:] == ["ca", "0.0000", "A", "1"]  # type, charge, fragment, block


def test_run_page_has_the_viewer(client, recorded):
    html = client.get("/runs/" + recorded["a"]).text
    assert 'id="structure-viewer"' in html and "/static/3Dmol-min.js" in html and "/static/viewer.js" in html
    spec = json.loads(re.search(r'id="structure-data">(.*?)</script>', html, re.S).group(1))
    assert [f["step"] for f in spec["frames"]] == [1, 2, 3]


def test_older_runs_fall_back_to_xyz_artifacts(client, recorded):
    frames = client.get("/api/runs/{0}/structures".format(recorded["b"])).json()["frames"]
    assert [(f["path"], f["kind"]) for f in frames] == [("final.xyz", "xyz")]


def test_runs_without_structures_skip_the_viewer(client, recorded):
    html = client.get("/runs/" + recorded["c"]).text
    assert "No structure files" in html and "3Dmol-min.js" not in html


def test_poreblazer_over_the_build(client, recorded):
    html = client.get("/runs/" + recorded["a"]).text  # results at steps 2 (child) and 3 (own)
    assert "Over the build" in html and 'id="pore-surface_area_m2_g-data"' in html
    assert "Over the build" not in client.get("/runs/" + recorded["c"]).text  # one result only


# --- ion maps (ion_map stages running liminal: docs/ion-maps.md)

def test_ion_maps_give_metrics_a_table_and_viewer_overlays(client, recorded):
    summary = client.get("/api/runs/" + recorded["a"]).json()["summary"]
    # the latest map of each ion: Li+ at step 3, not step 2
    assert (summary["li_site_energy"], summary["li_escape_barrier"], summary["li_sites"]) == (-2.0, 1.5, 3)
    assert (summary["k_site_energy"], summary["k_escape_barrier"]) == (-4.0, 6.0)
    assert summary["na_site_energy"] is None
    html = client.get("/runs/" + recorded["a"]).text
    assert "Ion maps" in html and 'id="ion-map"' in html and "Tier: classical: test" in html
    assert "ion_map_1/K_plus/map.json" in html
    spec = client.get("/api/runs/{0}/structures".format(recorded["a"])).json()
    byStep = {f["step"]: f["ion_maps"] for f in spec["frames"]}
    assert byStep[1] == []
    assert [m["ion"] for m in byStep[2]] == ["Li+"] and byStep[2][0]["escape_energy"] == 8.0
    assert sorted(m["ion"] for m in byStep[3]) == ["K+", "Li+"]
    li = next(m for m in byStep[3] if m["ion"] == "Li+")
    assert li["escape_energy"] == -0.5 and li["map"].endswith("ion_map_1/Li_plus/map.json?inline=1")
    assert client.get(li["map"]).json()["format"] == "liminal-map"


def test_ion_metrics_can_be_plotted_and_aimed_at():
    from ambuild_web import campaigns, sweeps

    assert ("li_escape_barrier", "Li+ escape barrier from the lowest site (kcal/mol)") in sweeps.METRICS
    assert campaigns.LABELS["k_site_energy"] == "K+ lowest site energy (kcal/mol)"
