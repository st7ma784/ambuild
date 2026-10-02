"""ambuild-review's scoring and picks, on synthetic strata with known answers."""
import copy

import pytest

from ambuild_ingest import review


def rec(i, experiment="ambuild/graphyne", box="60,60,60", status="FINISHED", **metrics):
    base = {"percolated_dimensions": 1, "pore_limiting_diameter_a": 8.0, "surface_area_m2_g": 4000.0,
            "el_log_transmission": -30.0, "el_log_hopping": -25.0, "el_conductance_min": 0.01,
            "final_num_blocks": 1, "final_density": 0.3}
    base.update(metrics)
    return {"id": "r{0}".format(i), "experiment": experiment, "experiment_id": "1", "status": status,
            "params": {"cell/box": box}, "metrics": base, "tags": {"ambuild.run_id": "run-{0}".format(i)}}


@pytest.fixture
def criteria():
    return review.loadCriteria()


def test_percentiles_share_ties_and_span_zero_to_one():
    assert review.percentiles({"a": 1, "b": 2, "c": 2, "d": 3}) == {"a": 0.0, "b": 0.5, "c": 0.5, "d": 1.0}
    assert review.percentiles({"a": 5}) == {"a": 0.5}


def test_the_best_are_picked_within_their_stratum_and_gated(criteria):
    runs = [rec(i, surface_area_m2_g=1000.0 + 100 * i, el_log_transmission=-40.0 + i) for i in range(10)]
    runs.append(rec(10, surface_area_m2_g=9999.0, el_log_transmission=0.0, percolated_dimensions=0))  # fails a gate
    runs += [rec(20 + i, box="30,30,30", surface_area_m2_g=50.0 + i) for i in range(4)]  # another stratum, worse values
    results, strata = review.review(runs, criteria)
    assert set(strata) == {"graphyne | cell/box=60,60,60", "graphyne | cell/box=30,30,30"}
    top = {rid for rid, o in results.items() if "top_porosity" in o["picks"]}
    assert {"r9", "r8", "r7"} <= top  # the best of the 60 A stratum
    assert "r10" not in top and results["r10"]["gates"] == ["percolated_dimensions"]
    assert len({"r20", "r21", "r22", "r23"} & top) == 3  # each stratum has its own best, however worse
    assert "top_both" in results["r9"]["picks"] and "pareto" in results["r9"]["picks"]
    assert results["r9"]["porosity"] > results["r0"]["porosity"]


def test_pareto_keeps_tradeoffs():
    pts = {"a": (1.0, 0.0), "b": (0.0, 1.0), "c": (0.5, 0.5), "d": (0.4, 0.4)}
    assert sorted(review.pareto(pts)) == ["a", "b", "c"]


def test_outliers_and_good_outliers(criteria):
    runs = [rec(i, surface_area_m2_g=4000.0 + 10 * (i % 5), final_density=0.30 + 0.001 * (i % 5)) for i in range(12)]
    runs.append(rec(50, surface_area_m2_g=9000.0))  # far better porosity
    runs.append(rec(51, final_density=0.9))  # odd density, not a scored metric
    results, _ = review.review(runs, criteria)
    assert "surface_area_m2_g_high" in results["r50"]["outliers"] and "good_outlier" in results["r50"]["picks"]
    assert "final_density_high" in results["r51"]["outliers"]
    assert "outlier" in results["r51"]["picks"] and "good_outlier" not in results["r51"]["picks"]
    small = [rec(i, surface_area_m2_g=100.0 * i) for i in range(4)] + [rec(9, surface_area_m2_g=1e6)]
    assert not any(o["outliers"] for o in review.review(small, criteria)[0].values())  # too few to judge


def test_edge_cases(criteria):
    runs = [rec(1, surface_area_m2_g=3000.0, percolated_dimensions=0), rec(2, final_num_blocks=6),
            rec(3, el_radical_domains=4, el_conductance=0.05), rec(4, status="FAILED"),
            rec(5, el_conductance_min=0.01, el_log_transmission=-80.0)]
    results, _ = review.review(runs, criteria)
    assert results["r1"]["edge_cases"] == ["porous_but_closed"]
    assert "fragmented" in results["r2"]["edge_cases"]
    assert "conducts_through_radicals" in results["r3"]["edge_cases"] and results["r3"]["gates"] == ["el_radical_domains"]
    assert "failed" in results["r4"]["edge_cases"] and "status" in results["r4"]["gates"]
    assert "spans_without_coherent_path" in results["r5"]["edge_cases"]


def test_the_stratified_set_spans_each_score(criteria):
    runs = [rec(i, surface_area_m2_g=100.0 * i, el_log_transmission=-50.0 + i) for i in range(21)]
    results, _ = review.review(runs, criteria)
    chosen = sorted(int(rid[1:]) for rid, o in results.items() if "stratified" in o["picks"])
    assert chosen == [2, 10, 18]  # 10th, 50th and 90th percentiles of both scores (they agree here)


def test_derived_metrics_fill_in_for_older_runs(criteria):
    r = rec(1, helium_volume_a3=60.0, system_volume_a3=100.0, maximum_pore_diameter_a=16.0)
    review.review([r], criteria)
    assert r["metrics"]["void_fraction"] == pytest.approx(0.6) and r["metrics"]["pore_window_ratio"] == pytest.approx(0.5)


def test_the_report_and_csv(criteria):
    runs = [rec(i, surface_area_m2_g=1000.0 + 100 * i) for i in range(6)]
    runs[0]["tags"]["ambuild.url"] = "http://scc:8080/runs/run-0"
    results, strata = review.review(runs, criteria)
    md, csvText = review.report(runs, results, strata, criteria, "http://scc:5050")
    assert md.startswith("# Ambuild run review") and "## Pareto front" in md and "## Strata" in md
    assert "(http://scc:5050/#/experiments/1/runs/r5)" in md
    assert csvText.splitlines()[0].startswith("mlflow_run_id,ambuild_run_id")
    assert len(csvText.splitlines()) == 1 + sum(1 for o in results.values() if o["picks"])


def test_tags_clear_when_nothing_is_picked(criteria):
    results, _ = review.review([rec(1)], criteria)
    tags = review.tagValues(results["r1"])
    assert tags["review.gates"] == "pass" and set(tags) == set(review.TAGS)


def test_a_review_against_a_stand_in_client(criteria):
    pytest.importorskip("mlflow")
    from test_mlflow_log import FakeClient

    class Client(FakeClient):
        def search_experiments(self, filter_string):
            import types
            return [types.SimpleNamespace(name=n, experiment_id=i) for n, i in self.experiments.items()]

        def search_runs(self, ids, filter_string=None, max_results=1000, page_token=None):
            import types
            page = [types.SimpleNamespace(info=types.SimpleNamespace(run_id=r["id"], status=r["status"]),
                                          data=types.SimpleNamespace(params=r["params"], tags=r["tags"],
                                                                     metrics={k: v for k, v, s in r["metrics"]}))
                    for r in self.runs.values() if r["experiment"] in ids]
            return type("Page", (list,), {"token": None})(page)

        def delete_tag(self, rid, k):
            self.runs[rid]["tags"].pop(k, None)

        def set_experiment_tag(self, eid, k, v):
            self.note = v

        def log_artifact(self, rid, path):
            import os
            self.runs[rid]["artifacts"].append((os.path.basename(path), open(path, encoding="utf-8").read()))

    client = Client()
    eid = client.create_experiment("ambuild/graphyne")
    for i in range(5):
        rid = client.create_run(eid, tags={"ambuild.run_id": "run-{0}".format(i)}).info.run_id
        client.runs[rid]["status"] = "FINISHED"
        client.runs[rid]["metrics"] = [(k, v, 0) for k, v in rec(i, surface_area_m2_g=1000.0 * (i + 1))["metrics"].items()]
    summary = review.run(client, criteria)
    assert summary["runs"] == 5 and summary["top_porosity"] == 3
    assert "ambuild/review" in client.experiments and client.note.startswith("# Ambuild run review")
    reviewRun = client.runs[summary["review_run"]]
    assert [n for n, _ in reviewRun["artifacts"]] == ["report.md", "picks.csv", "criteria.json"]
    assert reviewRun["status"] == "FINISHED" and ("runs", 5.0, 0) in reviewRun["metrics"]
    best = next(r for r in client.runs.values() if r["tags"].get("ambuild.run_id") == "run-4")
    assert "top_porosity" in best["tags"]["review.picks"]
    assert review.run(client, criteria)["tagged"] == 0  # nothing changed: no tag writes
