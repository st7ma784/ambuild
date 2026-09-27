"""Sweeps: one recipe run at many points and seeds.

A sweep names parameters, each a JSON pointer (RFC 6901) into the recipe, and either a
list of values for each (the grid is every combination) or explicit rows of values (e.g.
from a CSV file); every point is run once per seed:

    {
      "parameters": [
        {"name": "box", "path": "/cell/box", "all": true, "values": [20, 25, 30]},
        {"name": "grow", "path": "/stages/1/stages/0/count", "values": [2, 5, 10]}
      ],
      "seeds": [1, 2, 3]
    }

"all": true sets every element of a list (a cubic box). A pointer may name an argument a
stage does not give yet (it is added). Every point is checked as a recipe.

This module imports only the standard library, like ambuild.recipe.
"""
import copy
import csv
import io
import itertools
import json
import re

from ambuild import recipe as ab_recipe

MAX_RUNS = 1000
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")


class SweepError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("Invalid sweep:\n  " + "\n  ".join(self.errors))


# --- JSON pointers

def pointerTokens(path):
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("a JSON pointer starts with /: {0!r}".format(path))
    return [t.replace("~1", "/").replace("~0", "~") for t in path[1:].split("/")]


def _step(node, token, path):
    if isinstance(node, list):
        if not token.isdigit() or int(token) >= len(node):
            raise ValueError("{0}: no element {1}".format(path, token))
        return node[int(token)]
    if isinstance(node, dict):
        if token not in node:
            raise ValueError("{0}: no field {1}".format(path, token))
        return node[token]
    raise ValueError("{0}: {1} is inside a value, not a list or object".format(path, token))


def getPointer(doc, path):
    node = doc
    for token in pointerTokens(path):
        node = _step(node, token, path)
    return node


def setPointer(doc, path, value, all=False):
    """Set the value at path in doc (in place). A missing last field of an object is
    added; all: set every element of the list at path"""
    tokens = pointerTokens(path)
    parent = doc
    for token in tokens[:-1]:
        parent = _step(parent, token, path)
    last = tokens[-1]
    if all:
        target = _step(parent, last, path)
        if not isinstance(target, list):
            raise ValueError("{0}: \"all\" needs a list here".format(path))
        target[:] = [copy.deepcopy(value) for _ in target]
    elif isinstance(parent, list):
        _step(parent, last, path)
        parent[int(last)] = copy.deepcopy(value)
    elif isinstance(parent, dict):
        parent[last] = copy.deepcopy(value)
    else:
        raise ValueError("{0}: cannot set a value inside a value".format(path))


def recipePaths(recipe, prefix=""):
    """(pointer, value) of the recipe's settings a sweep could vary: numbers, strings,
    booleans and lists of numbers"""
    out = []
    if isinstance(recipe, dict):
        items = recipe.items()
    elif isinstance(recipe, list):
        if recipe and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in recipe):
            return [(prefix, recipe)]
        items = enumerate(recipe)
    else:
        return [(prefix, recipe)]
    for key, value in items:
        if prefix == "" and key in ("recipe_version", "name", "description"):
            continue
        token = str(key).replace("~", "~0").replace("/", "~1")
        out.extend(recipePaths(value, prefix + "/" + token))
    return out


# --- specs

def parseSeeds(text):
    """Seeds from text such as "1, 2, 5-8" (ranges inclusive)"""
    seeds = []
    for part in re.split(r"[,\s]+", (text or "").strip()):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise ValueError("not a seed or range of seeds: {0!r}".format(part))
        low, high = int(match.group(1)), int(match.group(2) or match.group(1))
        if high < low or high - low >= MAX_RUNS:
            raise ValueError("bad range of seeds: {0!r}".format(part))
        seeds.extend(range(low, high + 1))
    return seeds


def _value(text):
    """A CSV cell as a JSON value: numbers, true/false, JSON lists and objects, else text"""
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        return text


def parseCsv(text):
    """Rows (dicts) from CSV text with a header of parameter names (and optionally seed)"""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("the CSV file has no header row")
    rows = []
    for line in reader:
        rows.append({k.strip(): _value(v or "") for k, v in line.items() if k is not None})
    return rows


def validateSpec(spec, recipe):
    """Problems with a sweep's parameters, rows and seeds against its recipe"""
    errors = []
    if not isinstance(spec, dict):
        return ["sweep: must be an object"]
    for key in sorted(set(spec) - {"parameters", "rows", "seeds"}):
        errors.append("{0}: unknown field of a sweep".format(key))
    parameters = spec.get("parameters", [])
    rows, seeds = spec.get("rows"), spec.get("seeds")
    if not isinstance(parameters, list):
        return errors + ["parameters: must be a list"]
    names = []
    for i, p in enumerate(parameters):
        at = "parameters[{0}]".format(i)
        if not isinstance(p, dict):
            errors.append(at + ": must be an object")
            continue
        for key in sorted(set(p) - {"name", "path", "all", "values"}):
            errors.append("{0}.{1}: unknown field".format(at, key))
        name = p.get("name")
        if not (isinstance(name, str) and _NAME.fullmatch(name)) or name == "seed":
            errors.append(at + ".name: a short name (letters, digits, _ . -), not \"seed\"")
        elif name in names:
            errors.append("{0}.name: {1} is used twice".format(at, name))
        names.append(name)
        try:
            copyOf = copy.deepcopy(recipe)
            setPointer(copyOf, p.get("path"), 0, all=bool(p.get("all")))
        except ValueError as exc:
            errors.append("{0}.path: {1}".format(at, exc))
        if "all" in p and not isinstance(p["all"], bool):
            errors.append(at + ".all: must be true or false")
        if rows is None:
            values = p.get("values")
            if not (isinstance(values, list) and values):
                errors.append(at + ".values: a non-empty list (or give rows)")
        elif "values" in p:
            errors.append(at + ".values: give values or rows, not both")
    if rows is not None:
        if not isinstance(rows, list) or not rows:
            errors.append("rows: a non-empty list of objects")
        else:
            for i, row in enumerate(rows):
                if not isinstance(row, dict):
                    errors.append("rows[{0}]: must be an object".format(i))
                    continue
                missing = [n for n in names if n not in row]
                extra = sorted(set(row) - set(names) - {"seed"})
                if missing:
                    errors.append("rows[{0}]: no value for {1}".format(i, ", ".join(missing)))
                if extra:
                    errors.append("rows[{0}]: {1} not a parameter".format(i, ", ".join(extra)))
    if seeds is not None and not (isinstance(seeds, list) and seeds and
                                  all(isinstance(s, int) and not isinstance(s, bool) and 0 <= s < 2 ** 63
                                      for s in seeds)):
        errors.append("seeds: a non-empty list of non-negative integers")
    if not errors and not parameters and seeds is None:
        errors.append("sweep: give parameters, seeds or both")
    return errors


def points(spec):
    """The sweep's points (dicts of parameter values; a row may carry its own seed)"""
    parameters = spec.get("parameters", [])
    if spec.get("rows") is not None:
        return [dict(row) for row in spec["rows"]]
    names = [p["name"] for p in parameters]
    return [dict(zip(names, combo)) for combo in itertools.product(*[p["values"] for p in parameters])]


def label(point):
    return ", ".join("{0}={1}".format(k, json.dumps(v)) for k, v in point.items() if k != "seed") or "base recipe"


def expand(recipe, spec, allowPaths=False):
    """[{"index", "point", "seed", "recipe"}] for every run of the sweep; raises SweepError
    for a bad sweep, too many runs, or points that are not valid recipes"""
    errors = validateSpec(spec, recipe)
    if errors:
        raise SweepError(errors)
    parameters = {p["name"]: p for p in spec.get("parameters", [])}
    seeds = spec.get("seeds")
    pts = points(spec)
    total = len(pts) * (len(seeds) if seeds else 1)
    if total > MAX_RUNS:
        raise SweepError(["{0} runs is more than a sweep may have ({1})".format(total, MAX_RUNS)])
    runs, errors = [], []
    for point in pts:
        body = copy.deepcopy(recipe)
        for name, value in point.items():
            if name != "seed":
                setPointer(body, parameters[name]["path"], value, all=bool(parameters[name].get("all")))
        problems = ab_recipe.validate(body, allowPaths=allowPaths)
        if problems:
            errors.extend("{0}: {1}".format(label(point), p) for p in problems)
            continue
        for seed in (seeds or [point.get("seed", recipe.get("seed"))]):
            runs.append({"index": len(runs), "point": {k: v for k, v in point.items() if k != "seed"},
                         "seed": seed, "recipe": body})
    if errors:
        raise SweepError(errors[:50])
    return runs
