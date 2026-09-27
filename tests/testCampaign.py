"""Campaigns (ambuild.campaign): specs, scoring trials from replicate runs, and when to
stop or propose more points."""
import unittest

import context  # noqa: F401 (puts the checkout on the path)
from ambuild import campaign

REF = "sha256:" + "a" * 64


def recipe():
    return {
        "recipe_version": 1, "name": "base", "cell": {"box": [20, 20, 20]},
        "fragments": [{"type": "A", "car": REF, "csv": REF}], "bond_types": ["A:a-A:a"],
        "stages": [{"op": "seed", "count": 4},
                   {"repeat": 3, "stages": [{"op": "grow", "count": 2}, {"op": "zip"}]}],
        "seed": 9,
    }


def spec(**changes):
    s = {
        "parameters": [
            {"name": "box", "path": "/cell/box", "all": True, "type": "float", "low": 20, "high": 40},
            {"name": "grow", "path": "/stages/1/stages/0/count", "type": "int", "low": 1, "high": 6},
        ],
        "constraints": [{"metric": "pore_limiting_diameter_a", "min": 1.52}],
        "objective": {"maximise": "density"},
        "replicates": 2, "initial_points": 4, "batch_size": 2, "budget": {"runs": 20},
    }
    s.update(changes)
    return s


def run(state="finished", **results):
    return {"state": state, "results": results}


def trial(number, rnd, sc):
    return {"number": number, "round": rnd, "score": sc}


class Specs(unittest.TestCase):
    def testValidSpec(self):
        self.assertEqual(campaign.validate(spec(), recipe()), [])
        s = campaign.normalise(spec())
        self.assertEqual((s["method"], s["feasible_fraction"], campaign.seeds(s)), ("tpe", 0.5, [1, 2]))
        self.assertEqual(campaign.direction(s), "maximize")

    def testConstraintsOnlyStopAtTheFirstFeasiblePoint(self):
        self.assertEqual(campaign.normalise({k: v for k, v in spec().items() if k != "objective"})["stop"],
                         {"feasible_points": 1})

    def testProblems(self):
        cases = [
            (spec(method="magic"), "method:"),
            (spec(parameters=[]), "parameters: a non-empty list"),
            (spec(parameters=[{"name": "b", "path": "/cell/box", "type": "float", "low": 5, "high": 1}]),
             "parameters[0]: low and high"),
            (spec(parameters=[{"name": "b", "path": "/nope/x", "type": "int", "low": 1, "high": 2}]),
             "parameters[0].path"),
            (spec(parameters=[{"name": "b", "path": "/cell/box", "all": True, "type": "float", "low": 20,
                               "high": 30}], method="grid"), "parameters[0].values: the grid method"),
            (spec(constraints=[{"metric": "colour", "min": 1}]), "constraints[0].metric"),
            (spec(constraints=[{"metric": "density"}]), "constraints[0]: give a number"),
            (spec(objective={"maximise": "colour"}), "objective: the metric"),
            (spec(objective=None, constraints=[]), "campaign: give an objective"),
            (spec(replicates=0), "replicates:"),
            (spec(budget={"runs": 0}), "budget.runs"),
            (spec(stop={"forever": 1}), "stop.forever"),
            (spec(parameters=[{"name": "grow", "path": "/stages/1/stages/0/count", "type": "int", "low": 0,
                               "high": 3}]), "grow=0: stages[1].stages[0].count: must be at least 1"),
        ]
        for s, expected in cases:
            if s.get("objective") is None:
                s.pop("objective", None)
            errors = campaign.validate(s, recipe())
            self.assertTrue(any(e.startswith(expected) for e in errors), (expected, errors))

    def testPoints(self):
        s = campaign.normalise(spec())
        self.assertEqual(campaign.validatePoint(s, {"box": 25.5, "grow": 3}), [])
        self.assertEqual(campaign.validatePoint(s, {"box": 50, "grow": 2.5, "x": 1}),
                         ["x: not a parameter", "box: must be a number from 20 to 40",
                          "grow: must be an integer from 1 to 6"])
        body = campaign.pointRecipe(recipe(), s, {"box": 25.5, "grow": 3})
        self.assertEqual((body["cell"]["box"], body["stages"][1]["stages"][0]["count"]), ([25.5] * 3, 3))


class Scoring(unittest.TestCase):
    def setUp(self):
        self.s = campaign.normalise(spec())

    def testRunningUntilEveryReplicateEnds(self):
        self.assertEqual(campaign.score(self.s, [run(density=0.1), run("running")])["state"], "running")

    def testMeansAndFeasibility(self):
        sc = campaign.score(self.s, [run(density=0.1, pore_limiting_diameter_a=2.0),
                                     run(density=0.2, pore_limiting_diameter_a=1.0)])
        self.assertEqual(sc["state"], "complete")
        self.assertAlmostEqual(sc["value"], 0.15)
        self.assertEqual(sc["constraints"], [{"metric": "pore_limiting_diameter_a", "fraction": 0.5, "satisfied": True}])
        self.assertTrue(sc["feasible"])
        self.assertEqual(sc["violations"], [0.0])
        strict = dict(self.s, feasible_fraction=1.0)
        self.assertFalse(campaign.score(strict, [run(density=0.1, pore_limiting_diameter_a=2.0),
                                                 run(density=0.2, pore_limiting_diameter_a=1.0)])["feasible"])

    def testFailedRunsAndMissingResults(self):
        sc = campaign.score(self.s, [run("failed"), run("cancelled")])
        self.assertEqual((sc["state"], sc["feasible"]), ("failed", False))
        sc = campaign.score(self.s, [run("failed"), run(density=0.3, pore_limiting_diameter_a=3)])
        self.assertEqual((sc["state"], sc["value"], sc["finished"]), ("complete", 0.3, 1))
        self.assertEqual(campaign.score(self.s, [run(pore_limiting_diameter_a=3)])["state"], "failed")

    def testTarget(self):
        s = campaign.normalise(spec(objective={"target": {"metric": "density", "value": 0.1}}))
        self.assertEqual(campaign.direction(s), "minimize")
        sc = campaign.score(s, [run(density=0.13, pore_limiting_diameter_a=2)])
        self.assertAlmostEqual(sc["value"], 0.03)


class Deciding(unittest.TestCase):
    def setUp(self):
        self.s = campaign.normalise(spec(stop={"no_improvement_rounds": 2}))

    def good(self, value, feasible=True):
        return {"state": "complete", "value": value, "feasible": feasible}

    def testFirstRoundThenBatches(self):
        self.assertEqual(campaign.decide(self.s, [], 0), {"stop": None, "propose": 4})
        trials = [trial(i, 1, self.good(0.1 * i)) for i in range(4)]
        self.assertEqual(campaign.decide(self.s, trials, 8), {"stop": None, "propose": 2})
        self.assertEqual(campaign.decide(self.s, trials + [trial(4, 2, {"state": "running"})], 10)["propose"], 0)

    def testBudget(self):
        trials = [trial(i, 1, self.good(0.1)) for i in range(4)]
        self.assertEqual(campaign.decide(self.s, trials, 18), {"stop": None, "propose": 1})
        self.assertEqual(campaign.decide(self.s, trials, 20)["stop"], "budget spent (20 runs)")

    def testBestAndNoImprovement(self):
        trials = [trial(0, 1, self.good(0.5)), trial(1, 1, self.good(0.9, feasible=False)),
                  trial(2, 2, self.good(0.4)), trial(3, 3, self.good(0.45))]
        self.assertEqual(campaign.best(self.s, trials)["number"], 0)
        self.assertEqual(campaign.decide(self.s, trials, 8)["stop"], "no improvement in 2 rounds")
        trials.append(trial(4, 3, self.good(0.6)))
        self.assertIsNone(campaign.decide(self.s, trials, 10)["stop"])

    def testGoalMet(self):
        s = campaign.normalise({k: v for k, v in spec().items() if k != "objective"})
        trials = [trial(0, 1, self.good(None, feasible=False)), trial(1, 1, self.good(None))]
        self.assertEqual(campaign.decide(s, trials, 4)["stop"], "goal met: 1 feasible point")
        self.assertEqual(campaign.best(s, trials)["number"], 1)


if __name__ == "__main__":
    unittest.main()
