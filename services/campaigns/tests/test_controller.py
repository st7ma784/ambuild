"""The controller's proposals (no web API needed): methods, encoding, and a simulated
campaign against a known function, scored and decided as the web GUI does."""
import json
import math

from ambuild import campaign as ab_campaign

from ambuild_campaigns import controller


def spec(**changes):
    s = {
        "parameters": [
            {"name": "box", "path": "/cell/box", "all": True, "type": "float", "low": 20, "high": 40,
             "values": [20, 24, 28, 32, 36, 40]},
            {"name": "grow", "path": "/stages/1/stages/0/count", "type": "int", "low": 1, "high": 6},
        ],
        "constraints": [{"metric": "density", "min": 0.09, "max": 0.11}],
        "replicates": 1, "initial_points": 6, "batch_size": 4, "budget": {"runs": 36}, "sampler_seed": 3,
    }
    s.update(changes)
    return ab_campaign.normalise(s)


def density(point):
    """A stand-in for a build's density, fitted to the Slurm test's 3x3 sweep (box 20-30 A,
    grow 1-3 per pass): more material and a smaller box make it denser"""
    return (0.04 + 0.031 * point["grow"]) * (20.0 / point["box"]) ** 3


def simulate(s, limit=200):
    """Run a campaign to its end: propose, 'build', score, decide. Returns (trials, reason)"""
    trials = []
    for _ in range(limit):
        decision = ab_campaign.decide(s, trials, len(trials) * len(ab_campaign.seeds(s)))
        if decision["stop"]:
            return trials, decision["stop"]
        points = controller.propose(s, trials, decision["propose"])
        if not points:
            return trials, "grid done"
        rnd = max([t["round"] for t in trials] or [0]) + 1
        for point in points:
            assert ab_campaign.validatePoint(s, point) == [], point
            results = {"density": density(point)}
            trials.append({"number": len(trials), "round": rnd, "params": point,
                           "score": ab_campaign.score(s, [{"state": "finished", "results": results}])})
    raise AssertionError("did not finish")


def test_campaigns_meet_a_narrow_goal_in_fewer_runs_than_the_grid():
    """A sweep over the grid runs all 36 points; a campaign stops when the goal is met.
    Here the goal is a density band well away from the grid's corners."""
    goal = {"constraints": [{"metric": "density", "min": 0.065, "max": 0.075}]}
    for method in ("tpe", "random", "qmc"):
        trials, reason = simulate(spec(method=method, **goal))
        assert reason == "goal met: 1 feasible point", (method, reason)
        assert len(trials) < 36, (method, len(trials))
        best = ab_campaign.best(spec(**goal), trials)
        assert 0.065 <= density(best["params"]) <= 0.075


def test_proposals_are_reproducible_and_new():
    s = spec(method="tpe")
    first = controller.propose(s, [], 6)
    assert first == controller.propose(s, [], 6)
    assert len({json.dumps(p, sort_keys=True) for p in first}) == 6
    trials = [{"number": i, "round": 1, "params": p,
               "score": ab_campaign.score(s, [{"state": "finished", "results": {"density": density(p)}}])}
              for i, p in enumerate(first)]
    second = controller.propose(s, trials, 4)
    assert all(ab_campaign.validatePoint(s, p) == [] for p in second)


def test_objective_with_constraints_uses_constraint_values():
    s = spec(method="tpe", objective={"maximise": "density"},
             constraints=[{"metric": "density", "max": 0.1}])
    trials = []
    for i, p in enumerate(controller.propose(s, [], 6)):
        sc = ab_campaign.score(s, [{"state": "finished", "results": {"density": density(p)}}])
        trials.append({"number": i, "round": 1, "params": p, "score": sc})
    trials.append({"number": 6, "round": 1, "params": {"box": 30.0, "grow": 2},
                   "score": ab_campaign.score(s, [{"state": "failed", "results": {}}])})
    assert len(controller.propose(s, trials, 3)) == 3


def test_grid_and_qmc():
    s = spec(method="grid")
    first = controller.gridPoints(s, [], 4)
    assert first[0] == {"box": 20, "grow": 1} and first[3] == {"box": 20, "grow": 4}
    trials = [{"params": p} for p in first]
    assert controller.gridPoints(s, trials, 100)[0] == {"box": 20, "grow": 5}
    assert len(controller.gridPoints(s, trials, 100)) == 32
    q = spec(method="qmc")
    a = controller.qmcPoints(q, [], 4)
    b = controller.qmcPoints(q, [{}] * 4, 4)
    assert a == controller.qmcPoints(q, [], 4)
    assert not {json.dumps(p, sort_keys=True) for p in a} & {json.dumps(p, sort_keys=True) for p in b}
    assert all(20 <= p["box"] <= 40 and 1 <= p["grow"] <= 6 and isinstance(p["grow"], int) for p in a + b)


def test_choices_are_proposed_as_their_values():
    s = spec(method="random", parameters=[
        {"name": "linker", "path": "/fragments/0", "type": "choice", "choices": [{"type": "A"}, {"type": "B"}]},
        {"name": "zip", "path": "/stages/1/stages/1/bond_margin", "type": "float", "low": 0.5, "high": 1.5,
         "log": True}])
    points = controller.propose(s, [], 8)
    assert {json.dumps(p["linker"]) for p in points} <= {'{"type": "A"}', '{"type": "B"}'}
    assert all(0.5 <= p["zip"] <= 1.5 for p in points)
    assert controller.encode(s, points[0])["linker"] in (0, 1)


def test_violation_for_constraints_only():
    s = spec()
    assert controller.violation(s, {"means": {"density": 0.1}}) == 0
    assert math.isclose(controller.violation(s, {"means": {"density": 0.12}}), 0.01 / 0.11)
    assert controller.violation(s, {"means": {}}) == 1.0
