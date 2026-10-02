"""ambuild-review: pick the runs worth looking at, from everything logged to MLflow
(docs/mlflow.md, Review).

Runs are compared within strata (by default recipe x cell size), so a 30 Å test build is
never ranked against a 60 Å production one. The criteria (review_criteria.json, or
--criteria FILE) say:

- **gates** a run must pass to count among the best (finished, pores percolating, a pore
  limiting diameter above a bare Li+'s 1.52 Å, no radical π domains);
- **scores:** porosity and conductance, each the weighted mean of the run's percentile ranks
  within its stratum, on the metrics it has, each in its better direction;
- what to pick:
  - **top_porosity, top_conductance, top_both:** the best k gated runs per stratum;
  - **pareto:** gated runs no other run beats on both scores (across strata, as the scores
    are percentiles within them);
  - **outlier:** robust z-scores (median and MAD within the stratum) beyond a threshold, on
    any listed metric; good_outlier when the unusual direction is the better one of a scored
    metric;
  - **stratified:** per stratum, the runs nearest fixed quantiles of each score, a small
    reference set spanning the range, for regression tests;
  - **edge cases:** named rules (porous but closed, fragmented, spans without a coherent
    path, conducts through radicals, ...).

Results go back to MLflow: tags on every run (review.picks, review.edge_cases,
review.outliers, review.porosity_score, review.conductance_score, review.stratum,
review.gates), so its UI can filter on them (tags.`review.picks` LIKE '%pareto%'); and a
run in the experiment "ambuild/review" with the report (report.md), the picks (picks.csv)
and the criteria, whose report is also set as that experiment's description.

The scoring is plain Python on run records ({"id", "experiment", "status", "params",
"metrics", "tags"}), so it can be tested without MLflow.
"""
import argparse
import csv
import io
import json
import logging
import math
import os
import statistics
import sys
import tempfile
import time

from ambuild_ingest.mlflow_log import EXPERIMENT_PREFIX, RUN_TAG, derivedMetrics

logger = logging.getLogger(__name__)

REVIEW_EXPERIMENT = "ambuild/review"
CRITERIA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "review_criteria.json")
TAGS = ("review.picks", "review.edge_cases", "review.outliers", "review.porosity_score", "review.conductance_score",
        "review.stratum", "review.gates")


def loadCriteria(path=None):
    with open(path or CRITERIA_FILE) as f:
        return json.load(f)


# --- scoring --------------------------------------------------------------------------------

def stratum(rec, criteria):
    parts = []
    for key in criteria.get("strata", ["experiment"]):
        if key == "experiment":
            parts.append(rec["experiment"][len(EXPERIMENT_PREFIX):] if rec["experiment"].startswith(EXPERIMENT_PREFIX)
                         else rec["experiment"])
        elif key.startswith("params."):
            value = rec["params"].get(key[len("params."):])
            parts.append("{0}={1}".format(key[len("params."):], value if value is not None else "?"))
    return " | ".join(parts)


def _condition(rec, cond):
    if "status" in cond:
        return rec["status"] == cond["status"]
    v = rec["metrics"].get(cond["metric"])
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return False
    return ("min" not in cond or v >= cond["min"]) and ("max" not in cond or v <= cond["max"])


def gateFailures(rec, gates):
    """The gates a run fails (a metric it lacks isn't a failure: not every recipe measures
    everything)"""
    failed = []
    for name, rule in gates.items():
        if name.startswith("_"):
            continue
        if name == "status":
            if rec["status"] != rule:
                failed.append("status")
            continue
        v = rec["metrics"].get(name)
        if v is None:
            continue
        if ("min" in rule and v < rule["min"]) or ("max" in rule and v > rule["max"]):
            failed.append(name)
    return failed


def percentiles(values):
    """{key: percentile in [0, 1]} for {key: value}, ties sharing their mean rank"""
    items = sorted(values.items(), key=lambda kv: kv[1])
    n = len(items)
    out, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        rank = (i + j) / 2.0
        for k in range(i, j + 1):
            out[items[k][0]] = rank / (n - 1) if n > 1 else 0.5
        i = j + 1
    return out


def scoreStratum(recs, spec):
    """{run id: score} for one score's spec ({metric: {direction, weight}}), or None for a
    run with none of its metrics"""
    ranks = {}
    for metric, rule in spec.items():
        values = {r["id"]: r["metrics"][metric] for r in recs if isinstance(r["metrics"].get(metric), (int, float))
                  and not math.isnan(r["metrics"][metric])}
        if len(values) < 2:
            continue
        pct = percentiles(values)
        if rule.get("direction", "max") == "min":
            pct = {k: 1.0 - v for k, v in pct.items()}
        ranks[metric] = (pct, rule.get("weight", 1.0))
    scores = {}
    for r in recs:
        parts = [(pct[r["id"]], w) for pct, w in ranks.values() if r["id"] in pct]
        scores[r["id"]] = sum(p * w for p, w in parts) / sum(w for _, w in parts) if parts else None
    return scores


def robustZ(values):
    """{key: z} by median and MAD (0.6745 (x - median) / MAD); {} when MAD is 0"""
    data = list(values.values())
    med = statistics.median(data)
    mad = statistics.median(abs(x - med) for x in data)
    if mad == 0:
        return {}
    return {k: 0.6745 * (v - med) / mad for k, v in values.items()}


def pareto(points):
    """Keys of points ({key: (a, b)}, both better higher) that nothing beats on both"""
    front = []
    for k, (a, b) in points.items():
        if not any((a2 >= a and b2 >= b and (a2 > a or b2 > b)) for k2, (a2, b2) in points.items() if k2 != k):
            front.append(k)
    return front


def review(records, criteria):
    """Scores, picks and reasons for every record: {id: {...}}, and the strata"""
    for r in records:
        for k, v in derivedMetrics(r["metrics"]).items():
            r["metrics"].setdefault(k, v)
    strata = {}
    for r in records:
        strata.setdefault(stratum(r, criteria), []).append(r)
    out = {r["id"]: {"stratum": stratum(r, criteria), "gates": gateFailures(r, criteria.get("gates", {})),
                     "picks": set(), "edge_cases": [], "outliers": [], "porosity": None, "conductance": None}
           for r in records}
    k = criteria.get("top_k", 3)
    oc = criteria.get("outliers", {})
    good = {m: rule.get("direction", "max") for spec in criteria["scores"].values() for m, rule in spec.items()}
    for name, recs in strata.items():
        scores = {s: scoreStratum(recs, spec) for s, spec in criteria["scores"].items()}
        for r in recs:
            o = out[r["id"]]
            o["porosity"], o["conductance"] = scores["porosity"][r["id"]], scores["conductance"][r["id"]]
            if o["porosity"] is not None and o["conductance"] is not None:
                o["both"] = (o["porosity"] + o["conductance"]) / 2.0
        gated = [r for r in recs if not out[r["id"]]["gates"]]
        for label, key in (("top_porosity", "porosity"), ("top_conductance", "conductance"), ("top_both", "both")):
            ranked = sorted((r for r in gated if out[r["id"]].get(key) is not None),
                            key=lambda r: -out[r["id"]][key])
            for r in ranked[:k]:
                out[r["id"]]["picks"].add(label)
        if len(recs) >= oc.get("min_stratum_size", 8):
            for metric in oc.get("metrics", []):
                values = {r["id"]: r["metrics"][metric] for r in recs
                          if isinstance(r["metrics"].get(metric), (int, float)) and not math.isnan(r["metrics"][metric])}
                if len(values) < oc.get("min_stratum_size", 8):
                    continue
                for rid, z in robustZ(values).items():
                    if abs(z) >= oc.get("z", 3.5):
                        side = "high" if z > 0 else "low"
                        out[rid]["outliers"].append("{0}_{1}".format(metric, side))
                        out[rid]["picks"].add("outlier")
                        if metric in good and ((z > 0) == (good[metric] == "max")) and not out[rid]["gates"]:
                            out[rid]["picks"].add("good_outlier")
        if len(recs) >= criteria.get("min_stratum_size", 3):
            for key in ("porosity", "conductance"):
                have = sorted((r for r in recs if out[r["id"]][key] is not None), key=lambda r: out[r["id"]][key])
                for q in criteria.get("stratified_quantiles", []):
                    if have:
                        target = q * (len(have) - 1)
                        out[have[int(round(target))]["id"]]["picks"].add("stratified")
    points = {rid: (o["porosity"], o["conductance"]) for rid, o in out.items()
              if not o["gates"] and o["porosity"] is not None and o["conductance"] is not None}
    for rid in pareto(points):
        out[rid]["picks"].add("pareto")
    for r in records:
        for name, conds in criteria.get("edge_cases", {}).items():
            if not name.startswith("_") and all(_condition(r, c) for c in conds):
                out[r["id"]]["edge_cases"].append(name)
                out[r["id"]]["picks"].add("edge_case")
    return out, strata


# --- the report -----------------------------------------------------------------------------

def _fmt(v, digits=3):
    if v is None:
        return "–"
    if isinstance(v, float):
        return "{0:.{1}g}".format(v, digits)
    return str(v)


def _link(r, mlflowUrl):
    label = r["tags"].get(RUN_TAG, r["id"])[:8]
    if r["tags"].get("ambuild.url"):
        label = "[{0}]({1})".format(label, r["tags"]["ambuild.url"])
    if mlflowUrl and r.get("experiment_id"):
        label += " ([mlflow]({0}/#/experiments/{1}/runs/{2}))".format(mlflowUrl.rstrip("/"), r["experiment_id"], r["id"])
    return label


KEY_METRICS = ["surface_area_m2_g", "pore_limiting_diameter_a", "void_fraction", "el_log_transmission", "el_log_hopping"]


def report(records, results, strata, criteria, mlflowUrl=""):
    """The review as markdown, and its picks as CSV text"""
    byId = {r["id"]: r for r in records}
    picked = [r for r in records if results[r["id"]]["picks"]]
    lines = ["# Ambuild run review", "",
             "{0} runs in {1} strata; {2} picked. Scores are percentiles within each stratum (0-1, higher is "
             "better); gates: {3}.".format(len(records), len(strata), len(picked),
                                           ", ".join(k for k in criteria.get("gates", {}) if not k.startswith("_"))), ""]

    def table(title, ids, extra=None):
        lines.extend(["## " + title, ""])
        if not ids:
            lines.extend(["None.", ""])
            return
        head = ["Run", "Stratum", "Porosity", "Conductance"] + KEY_METRICS + ([extra[0]] if extra else [])
        lines.append("| " + " | ".join(head) + " |")
        lines.append("|" + " --- |" * len(head))
        for rid in ids:
            r, o = byId[rid], results[rid]
            row = [_link(r, mlflowUrl), o["stratum"], _fmt(o["porosity"], 2), _fmt(o["conductance"], 2)]
            row += [_fmt(r["metrics"].get(m)) for m in KEY_METRICS]
            if extra:
                row.append(extra[1](rid))
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    def withPick(label, key=None):
        ids = [rid for rid, o in results.items() if label in o["picks"]]
        if key:
            ids.sort(key=lambda rid: -(results[rid].get(key) or 0))
        return ids

    table("Pareto front: porosity and conductance", withPick("pareto", "both"))
    table("Best porosity per stratum", withPick("top_porosity", "porosity"))
    table("Best conductance per stratum", withPick("top_conductance", "conductance"))
    table("Good outliers", withPick("good_outlier"), ("Unusual in", lambda rid: ", ".join(results[rid]["outliers"])))
    table("Other outliers", [rid for rid in withPick("outlier") if "good_outlier" not in results[rid]["picks"]],
          ("Unusual in", lambda rid: ", ".join(results[rid]["outliers"])))
    table("Edge cases", withPick("edge_case"), ("Cases", lambda rid: ", ".join(results[rid]["edge_cases"])))
    table("Stratified reference set (for regression tests)", withPick("stratified"))
    lines.extend(["## Strata", "", "| Stratum | Runs | Pass the gates | Median porosity metric | Median conductance metric |",
                  "| --- | --- | --- | --- | --- |"])
    for name, recs in sorted(strata.items(), key=lambda kv: -len(kv[1])):
        passing = sum(1 for r in recs if not results[r["id"]]["gates"])
        sa = [r["metrics"]["surface_area_m2_g"] for r in recs if isinstance(r["metrics"].get("surface_area_m2_g"), (int, float))]
        lt = [r["metrics"]["el_log_transmission"] for r in recs if isinstance(r["metrics"].get("el_log_transmission"), (int, float))]
        lines.append("| {0} | {1} | {2} | {3} | {4} |".format(
            name, len(recs), passing, _fmt(statistics.median(sa)) + " m²/g" if sa else "–",
            _fmt(statistics.median(lt)) + " (log10 T)" if lt else "–"))
    lines.append("")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["mlflow_run_id", "ambuild_run_id", "experiment", "stratum", "picks", "edge_cases", "outliers", "gates_failed",
                "porosity_score", "conductance_score"] + KEY_METRICS)
    for r in picked:
        o = results[r["id"]]
        w.writerow([r["id"], r["tags"].get(RUN_TAG, ""), r["experiment"], o["stratum"], ";".join(sorted(o["picks"])),
                    ";".join(o["edge_cases"]), ";".join(o["outliers"]), ";".join(o["gates"]),
                    _fmt(o["porosity"], 4), _fmt(o["conductance"], 4)] + [r["metrics"].get(m, "") for m in KEY_METRICS])
    return "\n".join(lines), buf.getvalue()


# --- MLflow ---------------------------------------------------------------------------------

def fetch(client):
    """Every run in the ambuild/ experiments but the review's, as records"""
    records = []
    for exp in client.search_experiments(filter_string="name LIKE '{0}%'".format(EXPERIMENT_PREFIX)):
        if exp.name == REVIEW_EXPERIMENT:
            continue
        token = None
        while True:
            page = client.search_runs([exp.experiment_id], max_results=1000, page_token=token)
            for run in page:
                records.append({"id": run.info.run_id, "experiment": exp.name, "experiment_id": exp.experiment_id,
                                "status": run.info.status, "params": dict(run.data.params),
                                "metrics": dict(run.data.metrics), "tags": dict(run.data.tags)})
            token = page.token
            if not token:
                break
    return records


def tagValues(o):
    return {"review.picks": ",".join(sorted(o["picks"])), "review.edge_cases": ",".join(o["edge_cases"]),
            "review.outliers": ",".join(o["outliers"]),
            "review.porosity_score": _fmt(o["porosity"], 3) if o["porosity"] is not None else "",
            "review.conductance_score": _fmt(o["conductance"], 3) if o["conductance"] is not None else "",
            "review.stratum": o["stratum"][:500], "review.gates": "pass" if not o["gates"] else "fail: " + ",".join(o["gates"])}


def run(client, criteria=None, mlflowUrl="", log=logger.info):
    """Review everything in MLflow, tag the runs, log the review; returns a summary"""
    from mlflow.entities import Metric, RunTag

    criteria = criteria or loadCriteria()
    started = time.time()
    records = fetch(client)
    results, strata = review(records, criteria)
    changed = 0
    for r in records:
        want = tagValues(results[r["id"]])
        diff = [RunTag(k, v) for k, v in want.items() if v and r["tags"].get(k) != v]
        for k, v in want.items():
            if not v and k in r["tags"]:
                client.delete_tag(r["id"], k)
                changed += 1
        if diff:
            client.log_batch(r["id"], tags=diff)
            changed += 1
    markdown, picksCsv = report(records, results, strata, criteria, mlflowUrl)
    counts = {label: sum(1 for o in results.values() if label in o["picks"])
              for label in ("pareto", "top_porosity", "top_conductance", "top_both", "good_outlier", "outlier",
                            "edge_case", "stratified")}
    found = client.get_experiment_by_name(REVIEW_EXPERIMENT)
    experimentId = found.experiment_id if found else client.create_experiment(REVIEW_EXPERIMENT)
    rv = client.create_run(experimentId, run_name="review {0}".format(time.strftime("%Y-%m-%d %H:%M", time.gmtime())),
                           tags={"review.kind": "ambuild-review"})
    stamp = int(time.time() * 1000)
    metrics = [Metric("runs", len(records), stamp, 0), Metric("strata", len(strata), stamp, 0),
               Metric("pass_gates", sum(1 for o in results.values() if not o["gates"]), stamp, 0),
               Metric("seconds", time.time() - started, stamp, 0)]
    metrics += [Metric("picked_" + k, v, stamp, 0) for k, v in counts.items()]
    client.log_batch(rv.info.run_id, metrics=metrics)
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in (("report.md", markdown), ("picks.csv", picksCsv), ("criteria.json", json.dumps(criteria, indent=1))):
            path = os.path.join(tmp, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            client.log_artifact(rv.info.run_id, path)
    client.set_terminated(rv.info.run_id)
    client.set_experiment_tag(experimentId, "mlflow.note.content", markdown[:60000])
    summary = dict(runs=len(records), strata=len(strata), tagged=changed, review_run=rv.info.run_id, **counts)
    log("review: {0}".format(summary))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ambuild-review", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--criteria", help="a criteria JSON file (default: the packaged review_criteria.json)")
    parser.add_argument("--report", help="also write the report (markdown) here")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from ambuild_ingest import mlflow_log

    uri = mlflow_log.trackingUri()
    if not uri or not mlflow_log.reachable(uri):
        raise SystemExit("ambuild-review needs MLFLOW_TRACKING_URI and a reachable MLflow server")
    from mlflow.tracking import MlflowClient
    import contextlib

    client = MlflowClient(tracking_uri=uri)
    criteria = loadCriteria(args.criteria)
    with contextlib.redirect_stdout(io.StringIO()):
        summary = run(client, criteria, os.environ.get("AMBUILD_MLFLOW_URL", ""))
    print(json.dumps(summary))
    if args.report:
        records = fetch(client)
        results, strata = review(records, criteria)
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(report(records, results, strata, criteria, os.environ.get("AMBUILD_MLFLOW_URL", ""))[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
