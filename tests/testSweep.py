"""Sweeps (ambuild.sweep): JSON pointers, grids, rows from CSV, seeds, and validation."""
import copy
import unittest

import context  # noqa: F401 (puts the checkout on the path)
from ambuild import sweep

REF = "sha256:" + "a" * 64


def recipe():
    return {
        "recipe_version": 1, "name": "base", "cell": {"box": [20, 20, 20]},
        "fragments": [{"type": "A", "car": REF, "csv": REF}], "bond_types": ["A:a-A:a"],
        "stages": [{"op": "seed", "count": 4},
                   {"repeat": 3, "stages": [{"op": "grow", "count": 2}, {"op": "zip"}]}],
        "seed": 9,
    }


GRID = {"parameters": [
    {"name": "box", "path": "/cell/box", "all": True, "values": [20, 25, 30]},
    {"name": "grow", "path": "/stages/1/stages/0/count", "values": [2, 5, 10]},
]}


class Pointers(unittest.TestCase):
    def testGetAndSet(self):
        r = recipe()
        self.assertEqual(sweep.getPointer(r, "/stages/1/stages/0/count"), 2)
        sweep.setPointer(r, "/cell/box", 30, all=True)
        self.assertEqual(r["cell"]["box"], [30, 30, 30])
        sweep.setPointer(r, "/cell/box/1", 12)
        self.assertEqual(r["cell"]["box"], [30, 12, 30])
        sweep.setPointer(r, "/stages/1/stages/1/bond_margin", 1.5)  # a new argument
        self.assertEqual(r["stages"][1]["stages"][1]["bond_margin"], 1.5)
        for bad in ("cell/box", "/stages/9/count", "/cell/box/x", "/seed/x"):
            with self.assertRaises(ValueError):
                sweep.setPointer(copy.deepcopy(r), bad, 1)
        self.assertEqual(sweep.pointerTokens("/a~1b/c~0d"), ["a/b", "c~d"])

    def testRecipePaths(self):
        paths = dict(sweep.recipePaths(recipe()))
        self.assertEqual(paths["/cell/box"], [20, 20, 20])
        self.assertEqual(paths["/stages/1/repeat"], 3)
        self.assertNotIn("/name", paths)


class Expansion(unittest.TestCase):
    def testGridTimesSeeds(self):
        runs = sweep.expand(recipe(), dict(GRID, seeds=[1, 2]))
        self.assertEqual(len(runs), 18)
        self.assertEqual(runs[0]["point"], {"box": 20, "grow": 2})
        self.assertEqual([r["seed"] for r in runs[:2]], [1, 2])
        last = runs[-1]
        self.assertEqual(last["recipe"]["cell"]["box"], [30, 30, 30])
        self.assertEqual(last["recipe"]["stages"][1]["stages"][0]["count"], 10)
        self.assertEqual([r["index"] for r in runs], list(range(18)))

    def testWithoutSeedsUsesTheRecipes(self):
        runs = sweep.expand(recipe(), GRID)
        self.assertEqual(len(runs), 9)
        self.assertEqual({r["seed"] for r in runs}, {9})

    def testSeedsOnly(self):
        runs = sweep.expand(recipe(), {"seeds": sweep.parseSeeds("1, 3-5")})
        self.assertEqual([(r["point"], r["seed"]) for r in runs], [({}, 1), ({}, 3), ({}, 4), ({}, 5)])

    def testRowsFromCsv(self):
        rows = sweep.parseCsv("box,grow,seed\n20,2,7\n32.5, 4 ,8\n")
        self.assertEqual(rows, [{"box": 20, "grow": 2, "seed": 7}, {"box": 32.5, "grow": 4, "seed": 8}])
        spec = {"parameters": [{"name": p["name"], "path": p["path"], "all": p.get("all", False)}
                               for p in GRID["parameters"]], "rows": rows}
        runs = sweep.expand(recipe(), spec)
        self.assertEqual([(r["point"], r["seed"]) for r in runs],
                         [({"box": 20, "grow": 2}, 7), ({"box": 32.5, "grow": 4}, 8)])

    def testProblems(self):
        cases = [
            ({"parameters": [{"name": "box", "path": "/stages/9/count", "values": [1]}]}, "parameters[0].path"),
            ({"parameters": [{"name": "box", "path": "/cell/nope", "values": [1]}]}, "box=1: cell.nope: unknown field"),
            ({"parameters": [{"name": "seed", "path": "/cell/box", "values": [1]}]}, "parameters[0].name"),
            ({"parameters": [{"name": "b", "path": "/cell/box", "values": []}]}, "parameters[0].values"),
            ({"parameters": [{"name": "b", "path": "/cell/box"}], "rows": [{"c": 1}]}, "rows[0]: no value for b"),
            ({"seeds": [-1]}, "seeds:"),
            ({}, "sweep: give parameters"),
            ({"colour": 1, "seeds": [1]}, "colour: unknown field"),
        ]
        for spec, expected in cases:
            with self.assertRaises(sweep.SweepError) as ctx:
                sweep.expand(recipe(), spec)
            self.assertTrue(any(e.startswith(expected) for e in ctx.exception.errors), (expected, ctx.exception.errors))

    def testInvalidPointsAreNamed(self):
        spec = {"parameters": [{"name": "grow", "path": "/stages/1/stages/0/count", "values": [2, 0]}]}
        with self.assertRaises(sweep.SweepError) as ctx:
            sweep.expand(recipe(), spec)
        self.assertEqual(ctx.exception.errors, ["grow=0: stages[1].stages[0].count: must be at least 1"])

    def testSizeLimit(self):
        spec = {"parameters": [{"name": "g", "path": "/stages/0/count", "values": list(range(1, 101))}],
                "seeds": list(range(11))}
        with self.assertRaises(sweep.SweepError) as ctx:
            sweep.expand(recipe(), spec)
        self.assertIn("more than a sweep may have", ctx.exception.errors[0])

    def testParseSeeds(self):
        self.assertEqual(sweep.parseSeeds("5,1-3 9"), [5, 1, 2, 3, 9])
        for bad in ("x", "3-1", "1-5000"):
            with self.assertRaises(ValueError):
                sweep.parseSeeds(bad)


if __name__ == "__main__":
    unittest.main()
