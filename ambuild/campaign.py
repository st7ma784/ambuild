"""Campaigns: sweeps that aim at a goal.

A campaign names parameters to search (JSON pointers into a recipe, as in
ambuild.sweep, with bounds or choices), constraints and an objective over the results
of the runs, and a budget. A controller (services/campaigns) proposes points in rounds
(Bayesian optimisation with Optuna, or an outside decision-maker for method
"external"); each point is run once per replicate seed, and scored from its runs:

    {
      "parameters": [
        {"name": "box", "path": "/cell/box", "all": true, "type": "float", "low": 20, "high": 40},
        {"name": "grow", "path": "/stages/1/stages/0/count", "type": "int", "low": 2, "high": 20}
      ],
      "constraints": [{"metric": "pore_limiting_diameter_a", "min": 1.52},
                      {"metric": "percolated_dimensions", "min": 1}],
      "objective": {"maximise": "density"},
      "replicates": 3, "method": "tpe", "initial_points": 8, "batch_size": 6,
      "budget": {"runs": 150}, "stop": {"feasible_points": 3, "no_improvement_rounds": 4}
    }

A point's objective is the mean over its finished replicates; a constraint holds when
at least feasible_fraction of its replicates meet it. Standard library only, like
ambuild.recipe and ambuild.sweep.
"""
import copy
import json
import os
import statistics

from ambuild import recipe as ab_recipe
from ambuild import ionmap as ab_ionmap
from ambuild import sweep as ab_sweep

# Results a campaign can aim at: the run summaries' columns (Poreblazer results, the last
# build step's, and the ion maps', e.g. li_escape_barrier: ambuild.ionmap)
METRICS = [
    "surface_area_m2_g", "surface_area_a2", "surface_area_m2_cm3", "helium_volume_a3", "helium_volume_cm3_g",
    "geometric_volume_a3", "geometric_volume_cm3_g", "pore_limiting_diameter_a", "maximum_pore_diameter_a",
    "percolated_dimensions", "system_volume_a3", "system_mass_g_mol", "system_density_g_cm3",
    "density", "num_particles", "num_blocks",
] + ab_ionmap.METRICS
METHODS = ["tpe", "gp", "qmc", "random", "grid", "external"]
DEFAULTS = {"replicates": 3, "method": "tpe", "initial_points": 8, "batch_size": 6, "feasible_fraction": 0.5,
            "budget": {"runs": 150}, "stop": {}, "sampler_seed": 0}
_KEYS = set(DEFAULTS) | {"parameters", "constraints", "objective", "seeds"}
MAX_RUNS = 5000


class CampaignError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("Invalid campaign:\n  " + "\n  ".join(self.errors))


def _isInt(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _isNumber(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recipes", "campaigns")


def examples():
    """{name: spec} of the example campaigns shipped with Ambuild (recipes/campaigns), for
    the example recipes of the same shape (li_ion_carbon and its variants)"""
    out = {}
    for fname in sorted(os.listdir(EXAMPLES_DIR)) if os.path.isdir(EXAMPLES_DIR) else []:
        if fname.endswith(".json"):
            with open(os.path.join(EXAMPLES_DIR, fname), encoding="utf-8") as f:
                out[fname[:-5]] = json.load(f)
    return out


def normalise(spec):
    """The spec with defaults filled in"""
    out = copy.deepcopy(DEFAULTS)
    out.update(copy.deepcopy(spec))
    out["budget"] = dict(DEFAULTS["budget"], **(spec.get("budget") or {}))
    out["stop"] = dict(spec.get("stop") or {})
    out.setdefault("constraints", [])
    if "feasible_points" not in out["stop"] and not out.get("objective"):
        out["stop"]["feasible_points"] = 1  # constraints only: the goal is one point that meets them
    return out


def objectiveMetric(spec):
    obj = spec.get("objective") or {}
    if "target" in obj:
        return obj["target"].get("metric")
    return obj.get("maximise") or obj.get("minimise")


def direction(spec):
    """Optuna's direction for the campaign's value"""
    return "maximize" if "maximise" in (spec.get("objective") or {}) else "minimize"


def seeds(spec):
    return list(spec.get("seeds") or range(1, spec["replicates"] + 1))


def _checkParameter(p, at, recipe, method, errors):
    if not isinstance(p, dict):
        errors.append(at + ": must be an object")
        return
    for key in sorted(set(p) - {"name", "path", "all", "type", "low", "high", "log", "choices", "values"}):
        errors.append("{0}.{1}: unknown field".format(at, key))
    kind = p.get("type")
    if kind not in ("float", "int", "choice"):
        errors.append(at + ".type: float, int or choice")
        return
    if kind == "choice":
        if not (isinstance(p.get("choices"), list) and p["choices"]):
            errors.append(at + ".choices: a non-empty list of values")
    else:
        low, high = p.get("low"), p.get("high")
        check = _isInt if kind == "int" else _isNumber
        if not (check(low) and check(high) and low < high):
            errors.append("{0}: low and high must be {1}s, low < high".format(at, "integer" if kind == "int" else "number"))
        elif p.get("log") and low <= 0:
            errors.append(at + ".log: needs low > 0")
    if "log" in p and not isinstance(p["log"], bool):
        errors.append(at + ".log: must be true or false")
    if method == "grid" and kind == "float" and not (isinstance(p.get("values"), list) and p["values"]):
        errors.append(at + ".values: the grid method needs a list of values for a float parameter")
    try:
        ab_sweep.setPointer(copy.deepcopy(recipe), p.get("path"), 0, all=bool(p.get("all")))
    except ValueError as exc:
        errors.append("{0}.path: {1}".format(at, exc))


def corners(spec):
    """Points at the parameters' extremes (low values, high values), to check as recipes"""
    lows, highs = {}, {}
    for p in spec["parameters"]:
        if p["type"] == "choice":
            lows[p["name"]], highs[p["name"]] = p["choices"][0], p["choices"][-1]
        else:
            lows[p["name"]], highs[p["name"]] = p["low"], p["high"]
    return [lows, highs]


def pointRecipe(recipe, spec, params):
    """The recipe for a point: the parameters' values applied"""
    body = copy.deepcopy(recipe)
    byName = {p["name"]: p for p in spec["parameters"]}
    for name, value in params.items():
        p = byName[name]
        ab_sweep.setPointer(body, p["path"], value, all=bool(p.get("all")))
    return body


def validate(spec, recipe):
    """Problems with a campaign spec for recipe (the spec as given, before normalise)"""
    if not isinstance(spec, dict):
        return ["campaign: must be an object"]
    errors = ["{0}: unknown field of a campaign".format(k) for k in sorted(set(spec) - _KEYS)]
    s = normalise(spec)
    params = s.get("parameters")
    if not (isinstance(params, list) and params):
        errors.append("parameters: a non-empty list")
        params = []
    names = []
    for i, p in enumerate(params):
        at = "parameters[{0}]".format(i)
        name = p.get("name") if isinstance(p, dict) else None
        if not (isinstance(name, str) and ab_sweep._NAME.fullmatch(name)):
            errors.append(at + ".name: a short name (letters, digits, _ . -)")
        elif name in names:
            errors.append("{0}.name: {1} is used twice".format(at, name))
        names.append(name)
        _checkParameter(p, at, recipe, s.get("method"), errors)
    if s.get("method") not in METHODS:
        errors.append("method: one of {0}".format(", ".join(METHODS)))
    constraints = s.get("constraints")
    if not isinstance(constraints, list):
        errors.append("constraints: a list")
        constraints = []
    for i, c in enumerate(constraints):
        at = "constraints[{0}]".format(i)
        if not isinstance(c, dict) or c.get("metric") not in METRICS:
            errors.append(at + ".metric: one of {0}".format(", ".join(METRICS)))
            continue
        if not any(k in c for k in ("min", "max")) or not all(_isNumber(c[k]) for k in ("min", "max") if k in c):
            errors.append(at + ": give a number for min, max or both")
        for key in sorted(set(c) - {"metric", "min", "max"}):
            errors.append("{0}.{1}: unknown field".format(at, key))
    obj = s.get("objective")
    if obj is not None:
        if not isinstance(obj, dict) or len(obj) != 1 or not set(obj) <= {"maximise", "minimise", "target"}:
            errors.append('objective: {"maximise": metric}, {"minimise": metric} or {"target": {"metric", "value"}}')
        elif "target" in obj and not (isinstance(obj["target"], dict) and _isNumber(obj["target"].get("value"))):
            errors.append("objective.target: an object with metric and value")
        elif objectiveMetric(s) not in METRICS:
            errors.append("objective: the metric must be one of {0}".format(", ".join(METRICS)))
    if obj is None and not constraints:
        errors.append("campaign: give an objective, constraints or both")
    for key, low, high in (("replicates", 1, 20), ("initial_points", 1, 500), ("batch_size", 1, 500)):
        if not (_isInt(s.get(key)) and low <= s[key] <= high):
            errors.append("{0}: an integer from {1} to {2}".format(key, low, high))
    if "seeds" in spec and not (isinstance(spec["seeds"], list) and spec["seeds"] and
                                all(_isInt(x) and x >= 0 for x in spec["seeds"])):
        errors.append("seeds: a list of non-negative integers (default 1 to replicates)")
    if not (_isNumber(s.get("feasible_fraction")) and 0 < s["feasible_fraction"] <= 1):
        errors.append("feasible_fraction: a number above 0 and at most 1")
    runs = s["budget"].get("runs")
    if not (_isInt(runs) and 1 <= runs <= MAX_RUNS):
        errors.append("budget.runs: an integer from 1 to {0}".format(MAX_RUNS))
    for key in s["stop"]:
        if key not in ("feasible_points", "no_improvement_rounds", "max_rounds"):
            errors.append("stop.{0}: unknown (feasible_points, no_improvement_rounds, max_rounds)".format(key))
        elif not (_isInt(s["stop"][key]) and s["stop"][key] >= 1):
            errors.append("stop.{0}: a positive integer".format(key))
    if not _isInt(s.get("sampler_seed")):
        errors.append("sampler_seed: an integer")
    if not errors:
        for point in corners(s):
            for problem in ab_recipe.validate(pointRecipe(recipe, s, point)):
                errors.append("{0}: {1}".format(ab_sweep.label(point), problem))
    return errors


def validatePoint(spec, params):
    """Problems with a proposed point (from a controller or an outside decision-maker)"""
    errors = []
    byName = {p["name"]: p for p in spec["parameters"]}
    if not isinstance(params, dict):
        return ["a point is an object of parameter values"]
    for name in sorted(set(byName) - set(params)):
        errors.append("{0}: missing".format(name))
    for name in sorted(set(params) - set(byName)):
        errors.append("{0}: not a parameter".format(name))
    for name, value in params.items():
        p = byName.get(name)
        if p is None:
            continue
        if p["type"] == "choice":
            if value not in p["choices"]:
                errors.append("{0}: not one of the choices".format(name))
        elif not (_isInt(value) if p["type"] == "int" else _isNumber(value)) or not p["low"] <= value <= p["high"]:
            errors.append("{0}: must be {1} from {2} to {3}".format(
                name, "an integer" if p["type"] == "int" else "a number", p["low"], p["high"]))
    return errors


# --- scoring

def _mean(values):
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def score(spec, replicates):
    """A trial's score from its replicate runs, each {"state", "results": {metric: value}}:
    {"state": "running" | "complete" | "failed", "value", "means", "constraints",
    "violations" (<= 0 where met, for Optuna), "feasible"}"""
    if not replicates or any(r["state"] not in ("finished", "failed", "cancelled") for r in replicates):
        return {"state": "running"}
    done = [r for r in replicates if r["state"] == "finished"]
    metric = objectiveMetric(spec)
    used = {c["metric"] for c in spec["constraints"]} | ({metric} if metric else set())
    means = {m: _mean([r["results"].get(m) for r in done]) for m in sorted(used)}
    out = {"state": "complete", "means": means, "finished": len(done), "replicates": len(replicates)}
    if not done:
        return dict(out, state="failed", feasible=False, value=None, constraints=[], violations=[])
    value = None
    if metric:
        value = means.get(metric)
        if value is not None and "target" in spec["objective"]:
            value = abs(value - spec["objective"]["target"]["value"])
        if value is None:
            return dict(out, state="failed", feasible=False, value=None, constraints=[], violations=[])
    constraints, violations = [], []
    for c in spec["constraints"]:
        met = 0
        for r in done:
            v = r["results"].get(c["metric"])
            if v is not None and ("min" not in c or v >= c["min"]) and ("max" not in c or v <= c["max"]):
                met += 1
        fraction = met / len(done)
        constraints.append({"metric": c["metric"], "fraction": fraction,
                            "satisfied": fraction >= spec["feasible_fraction"]})
        violations.append(spec["feasible_fraction"] - fraction)
    return dict(out, value=value, constraints=constraints, violations=violations,
                feasible=all(c["satisfied"] for c in constraints))


def isBetter(spec, a, b):
    """Is value a better than value b?"""
    if b is None:
        return a is not None
    if a is None:
        return False
    return a > b if direction(spec) == "maximize" else a < b


def best(spec, trials):
    """The best feasible complete trial (the first feasible one when there is no objective)"""
    found = None
    for t in sorted(trials, key=lambda t: t["number"]):
        sc = t["score"]
        if sc.get("state") != "complete" or not sc.get("feasible"):
            continue
        if found is None or (objectiveMetric(spec) and isBetter(spec, sc["value"], found["score"]["value"])):
            found = t
    return found


def decide(spec, trials, runsUsed):
    """What the controller should do now: {"stop": reason or None, "propose": points}. A new
    round starts when every trial of the last one has been scored."""
    if any(t["score"].get("state") == "running" for t in trials):
        return {"stop": None, "propose": 0}
    rounds = max([t["round"] for t in trials] or [0])
    feasible = [t for t in trials if t["score"].get("feasible")]
    stop = spec["stop"]
    if stop.get("feasible_points") and len(feasible) >= stop["feasible_points"]:
        return {"stop": "goal met: {0} feasible point{1}".format(len(feasible), "" if len(feasible) == 1 else "s"),
                "propose": 0}
    if stop.get("max_rounds") and rounds >= stop["max_rounds"]:
        return {"stop": "{0} rounds run (the limit)".format(rounds), "propose": 0}
    k = stop.get("no_improvement_rounds")
    if k and objectiveMetric(spec) and rounds > k:
        bestBefore = best(spec, [t for t in trials if t["round"] <= rounds - k])
        bestNow = best(spec, trials)
        if bestBefore is not None and bestNow is bestBefore:
            return {"stop": "no improvement in {0} rounds".format(k), "propose": 0}
    points = spec["initial_points"] if rounds == 0 else spec["batch_size"]
    points = min(points, (spec["budget"]["runs"] - runsUsed) // len(seeds(spec)))
    if points <= 0:
        return {"stop": "budget spent ({0} runs)".format(runsUsed), "propose": 0}
    return {"stop": None, "propose": points}
