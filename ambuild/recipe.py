"""Recipes: a build described as data (JSON) rather than as a Python script.

A recipe names the cell, the building blocks, the bond types and a list of stages, each
an operation on the cell (seed, grow, zip, optimise, ...) or a repeated group of stages:

    {
      "recipe_version": 1,
      "name": "benzene network",
      "cell": {"box": [30, 30, 30]},
      "fragments": [{"type": "A", "car": "sha256:3f1c...", "csv": "sha256:9ab2...", "name": "benzene"}],
      "bond_types": ["A:a-A:a"],
      "stages": [
        {"op": "seed", "count": 10},
        {"repeat": 5, "stages": [{"op": "grow", "count": 5}, {"op": "zip"}]}
      ],
      "seed": 42
    }

Input files are referenced by content ("sha256:<hex>", found in the directories given
with --blobs, where each file is named by its sha256) or, on the command line only, by a
path relative to the recipe file. "params": null (or leaving it out) uses the parameter
files of this installation (ab_util.paramsDir()).

The runner records the run (run.json, events.jsonl, inputs/ with the recipe itself) and
checkpoints (Cell.dump: a pickle and an extended XYZ) after each top-level stage and each
pass of a top-level repeat. The same recipe and seed give the same structure.

    python -m ambuild.recipe run recipe.json --output DIR [--blobs DIR] [--seed N] [--run-id ID]
    python -m ambuild.recipe validate recipe.json
    python -m ambuild.recipe describe          # the operations and their arguments, as JSON
    python -m ambuild.recipe hash recipe.json
    python -m ambuild.recipe examples          # example recipes; "example NAME" prints one

This module imports only the standard library until a recipe is run, so services that
only validate recipes (the web GUI) need not install NumPy or HOOMD-blue.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import sys
import tempfile

RECIPE_VERSION = 1
MAX_REPEAT = 10000
MAX_DEPTH = 4  # nested repeats
PARAMS_FILES = ["angle_params.csv", "bond_params.csv", "dihedral_params.csv", "improper_params.csv",
                "pair_params.csv"]
REF = re.compile(r"sha256:[0-9a-f]{64}")
NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
BOND_TYPE = re.compile(r"[^:\s-]+:[^:\s-]+-[^:\s-]+:[^:\s-]+")


class RecipeError(ValueError):
    """An invalid recipe; errors is the list of problems"""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("Invalid recipe:\n  " + "\n  ".join(self.errors))


class Cancelled(Exception):
    """The run was stopped by a signal (e.g. the agent cancelling it)"""


def _arg(name, type, default=None, help="", kwarg=None, minimum=None, choices=None, required=False):
    return {"name": name, "type": type, "default": default, "help": help, "kwarg": kwarg,
            "minimum": minimum, "choices": choices, "required": required}


# Arguments shared by the HOOMD-blue operations
_HOOMD = [
    _arg("rigid_body", "boolean", True, "rigid-body (True) or all-atom (False)", "rigidBody"),
    _arg("do_dihedral", "boolean", False, "include dihedral terms", "doDihedral"),
    _arg("do_improper", "boolean", False, "include improper terms", "doImproper"),
    _arg("do_charges", "boolean", True, "include charges", "doCharges"),
    _arg("r_cut", "number", None, "van der Waals cut-off (Å); default: the cell's", "rCut", minimum=0),
]
_OPT = [
    _arg("dt", "number", None, "FIRE time step (default 0.005)", "dt", minimum=0),
    _arg("ftol", "number", None, "force tolerance (default 0.01)", "ftol", minimum=0),
    _arg("etol", "number", None, "energy tolerance (default 1e-5)", "Etol", minimum=0),
    _arg("stepwise", "boolean", False, "grow the time step from 1e-12 until optimised", "stepwise"),
]
_MD = [
    _arg("integrator", "string", "nvt", "", "integrator", choices=["nvt", "npt"]),
    _arg("temperature", "number", None, "kT (default 1.0)", "T", minimum=0),
    _arg("tau", "number", None, "thermostat coupling (default 0.5)", "tau", minimum=0),
    _arg("pressure", "number", None, "pressure, for npt (default 1)", "P", minimum=0),
    _arg("tau_p", "number", None, "barostat coupling, for npt (default 0.5)", "tauP", minimum=0),
]

# Each operation calls one Cell method. An argument's value is passed as the method's
# keyword `kwarg` (default: the same name); None means "the method's default" and is not
# passed. Defaults here are the methods' own, shown in the web GUI.
OPERATIONS = {
    "seed": {
        "method": "seed",
        "help": "Add blocks at random positions in the empty cell.",
        "args": [
            _arg("count", "integer", None, "blocks to add", "nblocks", minimum=1, required=True),
            _arg("fragment_type", "string", None, "fragment type (default: any)", "fragmentType"),
            _arg("max_tries", "integer", 500, "attempts per block", "maxTries", minimum=1),
            _arg("center", "boolean", False, "put the first block in the centre", "center"),
            _arg("point", "vector3", None, "seed around this point (needs radius)", "point"),
            _arg("radius", "number", None, "radius around point (Å)", "radius", minimum=0),
            _arg("zone", "vector6", None, "seed within this box: x0 x1 y0 y1 z0 z1", "zone"),
            _arg("random", "boolean", True, "rotate blocks randomly", "random"),
        ],
    },
    "grow": {
        "method": "growBlocks",
        "help": "Add blocks by bonding library fragments to free end groups in the cell.",
        "args": [
            _arg("count", "integer", None, "blocks to add", "toGrow", minimum=1, required=True),
            _arg("cell_end_groups", "strings", None, "end group types in the cell to grow from", "cellEndGroups"),
            _arg("library_end_groups", "strings", None, "end group types of the new blocks",
                 "libraryEndGroups"),
            _arg("dihedral", "number", None, "dihedral angle of the new bond (degrees)", "dihedral"),
            _arg("max_tries", "integer", 50, "attempts per block", "maxTries", minimum=1),
            _arg("random", "boolean", True, "choose end groups at random", "random"),
        ],
    },
    "join": {
        "method": "joinBlocks",
        "help": "Move blocks in the cell to bond them to each other.",
        "args": [
            _arg("count", "integer", None, "joins to make", "toJoin", minimum=1, required=True),
            _arg("cell_end_groups", "strings", None, "end group types to join", "cellEndGroups"),
            _arg("dihedral", "number", None, "dihedral angle of the new bond (degrees)", "dihedral"),
            _arg("max_tries", "integer", 100, "attempts per join", "maxTries", minimum=1),
        ],
    },
    "zip": {
        "method": "zipBlocks",
        "help": "Bond free end groups that are close enough, with looser margins; blocks do not move.",
        "args": [
            _arg("bond_margin", "number", 0.5, "bond length margin (Å)", "bondMargin", minimum=0),
            _arg("bond_angle_margin", "number", 15, "bond angle margin (degrees)", "bondAngleMargin", minimum=0),
            _arg("clash_check", "boolean", False, "reject bonds that pass through atoms", "clashCheck"),
            _arg("clash_dist", "number", 1.6, "clash distance from the bond axis (Å)", "clashDist", minimum=0),
            _arg("self_bond", "boolean", True, "allow a block to bond to itself", "selfBond"),
        ],
    },
    "optimise": {
        "method": "optimiseGeometry",
        "help": "Optimise the geometry with HOOMD-blue (FIRE).",
        "args": [_arg("cycles", "integer", 1000000, "optimisation cycles", "optCycles", minimum=1)] + _HOOMD + _OPT,
    },
    "md": {
        "method": "runMD",
        "help": "Run molecular dynamics with HOOMD-blue.",
        "args": [_arg("cycles", "integer", 100000, "MD cycles", "mdCycles", minimum=1)] + _HOOMD + _MD
        + [_arg("dt", "number", None, "time step (default 0.0005)", "dt", minimum=0)],
    },
    "md_optimise": {
        "method": "runMDAndOptimise",
        "help": "Molecular dynamics followed by a geometry optimisation.",
        "args": [
            _arg("md_cycles", "integer", 100000, "MD cycles", "mdCycles", minimum=1),
            _arg("opt_cycles", "integer", 1000000, "optimisation cycles", "optCycles", minimum=1),
        ] + _HOOMD + _MD,
    },
    "delete_blocks": {
        "method": "deleteBlocks",
        "help": "Remove small blocks (e.g. unbonded monomers) from the cell.",
        "args": [
            _arg("fragment_types", "strings", None, "fragment types to remove (default: any)", "fragmentTypes"),
            _arg("max_frags", "integer", 1, "remove only blocks with at most this many fragments", "maxFrags",
                 minimum=1),
            _arg("num_blocks", "integer", 0, "how many to remove (0: all that match)", "numBlocks", minimum=0),
        ],
    },
    "cap": {
        "method": "capBlocks",
        "help": "Cap free end groups with a cap block (a .car and .csv with one end group).",
        "args": [
            _arg("block", "block", None, "the cap block's files", "filename", required=True),
            _arg("end_group_type", "string", None, "end group type to cap (default: all)", "fragmentType"),
        ],
    },
    "poreblazer": {
        "method": "poreblazer",
        "help": "Run Poreblazer on the cell (POREBLAZER_EXE); results are recorded with the run.",
        "args": [
            _arg("settings", "object", None, "Poreblazer settings, e.g. {\"cubelet_size\": 0.3}"),
            _arg("threads", "integer", None, "OpenMP threads (default: all CPUs)", "threads", minimum=1),
            _arg("memory_limit_mb", "integer", None, "refuse to start above this memory estimate",
                 "memory_limit_mb", minimum=1),
        ],
    },
}

_FRAGMENT_KEYS = {"type", "car", "csv", "ambody", "name", "solvent", "catalyst", "mark_bonded"}
_CELL_KEYS = {"box": None, "atom_margin": 0.5, "bond_margin": 0.5, "bond_angle_margin": 15}
_TOP_KEYS = {"recipe_version", "name", "description", "cell", "fragments", "params", "bond_types", "max_bonds",
             "stages", "seed", "resources"}
_RESOURCE_KEYS = {"cpus", "gpus", "memory_mb", "time"}


# --- validation

def isRef(value):
    return isinstance(value, str) and REF.fullmatch(value) is not None


def _isInt(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _isNumber(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _checkFile(value, where, errors, allowPaths):
    if isRef(value):
        return
    if allowPaths and isinstance(value, str) and value and not value.startswith("sha256:"):
        return
    errors.append("{0}: must be a file reference \"sha256:<64 hex digits>\"{1}".format(
        where, " or a path" if allowPaths else ""))


def _checkBlock(value, where, errors, allowPaths):
    if not isinstance(value, dict):
        errors.append("{0}: must be an object with \"car\" and \"csv\" files".format(where))
        return
    for key in sorted(set(value) - {"car", "csv", "ambody", "name"}):
        errors.append("{0}.{1}: unknown field".format(where, key))
    for key in ("car", "csv"):
        if key not in value:
            errors.append("{0}.{1}: required".format(where, key))
        else:
            _checkFile(value[key], "{0}.{1}".format(where, key), errors, allowPaths)
    if value.get("ambody") is not None:
        _checkFile(value["ambody"], where + ".ambody", errors, allowPaths)
    if "name" in value and not (isinstance(value["name"], str) and NAME.fullmatch(value["name"])):
        errors.append("{0}.name: letters, digits, '_', '.' and '-' only".format(where))


def _checkValue(arg, value, where, errors, allowPaths):
    t = arg["type"]
    if value is None:
        if arg["required"]:
            errors.append("{0}: required".format(where))
        return
    ok = {
        "integer": _isInt(value),
        "number": _isNumber(value),
        "boolean": isinstance(value, bool),
        "string": isinstance(value, str) and value != "",
        "strings": (isinstance(value, str) and value != "")
        or (isinstance(value, list) and value and all(isinstance(s, str) and s for s in value)),
        "vector3": isinstance(value, list) and len(value) == 3 and all(_isNumber(x) for x in value),
        "vector6": isinstance(value, list) and len(value) == 6 and all(_isNumber(x) for x in value),
        "object": isinstance(value, dict),
        "block": True,  # checked below
    }[t]
    if not ok:
        expected = {"integer": "an integer", "number": "a number", "boolean": "true or false",
                    "string": "a non-empty string", "strings": "a string or a list of strings",
                    "vector3": "a list of 3 numbers", "vector6": "a list of 6 numbers",
                    "object": "an object"}[t]
        errors.append("{0}: must be {1}".format(where, expected))
        return
    if t == "block":
        _checkBlock(value, where, errors, allowPaths)
    if arg["minimum"] is not None and _isNumber(value) and value < arg["minimum"]:
        errors.append("{0}: must be at least {1}".format(where, arg["minimum"]))
    if arg["choices"] and value not in arg["choices"]:
        errors.append("{0}: must be one of {1}".format(where, ", ".join(arg["choices"])))


def _checkStages(stages, where, errors, allowPaths, depth, types=()):
    if not isinstance(stages, list) or not stages:
        errors.append("{0}: must be a non-empty list of stages".format(where))
        return
    for i, stage in enumerate(stages):
        at = "{0}[{1}]".format(where, i)
        if not isinstance(stage, dict):
            errors.append("{0}: must be an object".format(at))
        elif "repeat" in stage:
            for key in sorted(set(stage) - {"repeat", "stages"}):
                errors.append("{0}.{1}: unknown field (a repeat has \"repeat\" and \"stages\")".format(at, key))
            if not _isInt(stage["repeat"]) or not 1 <= stage["repeat"] <= MAX_REPEAT:
                errors.append("{0}.repeat: must be an integer from 1 to {1}".format(at, MAX_REPEAT))
            if depth >= MAX_DEPTH:
                errors.append("{0}: repeats nested more than {1} deep".format(at, MAX_DEPTH))
            else:
                _checkStages(stage.get("stages"), at + ".stages", errors, allowPaths, depth + 1, types)
        elif stage.get("op") in OPERATIONS:
            spec = OPERATIONS[stage["op"]]
            names = {a["name"] for a in spec["args"]}
            for key in sorted(set(stage) - names - {"op"}):
                errors.append("{0}.{1}: unknown argument of {2}".format(at, key, stage["op"]))
            for arg in spec["args"]:
                _checkValue(arg, stage.get(arg["name"]), "{0}.{1}".format(at, arg["name"]), errors, allowPaths)
            for key in ("fragment_type", "fragment_types"):  # typos would fail only mid-build
                value = stage.get(key)
                named = [value] if isinstance(value, str) else value if isinstance(value, list) else []
                for ftype in named:
                    if types and isinstance(ftype, str) and ftype not in types:
                        errors.append("{0}.{1}: {2} is not a fragment type of this recipe".format(at, key, ftype))
        else:
            errors.append("{0}.op: must be one of {1} (or the stage a \"repeat\")".format(
                at, ", ".join(sorted(OPERATIONS))))


def validate(recipe, allowPaths=False):
    """A list of the problems with recipe (empty if it is valid). allowPaths: accept file
    paths as well as sha256 references (the command line; the web GUI takes references only)"""
    errors = []
    if not isinstance(recipe, dict):
        return ["recipe: must be a JSON object"]
    for key in sorted(set(recipe) - _TOP_KEYS):
        errors.append("{0}: unknown field".format(key))
    if recipe.get("recipe_version") != RECIPE_VERSION:
        errors.append("recipe_version: must be {0}".format(RECIPE_VERSION))
    if not isinstance(recipe.get("name"), str) or not recipe.get("name", "").strip():
        errors.append("name: required")
    if "description" in recipe and not isinstance(recipe["description"], str):
        errors.append("description: must be a string")

    cell = recipe.get("cell")
    if not isinstance(cell, dict):
        errors.append("cell: required, an object with \"box\"")
    else:
        for key in sorted(set(cell) - set(_CELL_KEYS)):
            errors.append("cell.{0}: unknown field".format(key))
        box = cell.get("box")
        if not (isinstance(box, list) and len(box) == 3 and all(_isNumber(x) and x > 0 for x in box)):
            errors.append("cell.box: must be a list of 3 positive numbers (Å)")
        for key in ("atom_margin", "bond_margin", "bond_angle_margin"):
            if key in cell and not (_isNumber(cell[key]) and cell[key] >= 0):
                errors.append("cell.{0}: must be a number, at least 0".format(key))

    fragments = recipe.get("fragments")
    types = set()
    if not isinstance(fragments, list) or not fragments:
        errors.append("fragments: required, a non-empty list")
    else:
        for i, frag in enumerate(fragments):
            at = "fragments[{0}]".format(i)
            if not isinstance(frag, dict):
                errors.append(at + ": must be an object")
                continue
            for key in sorted(set(frag) - _FRAGMENT_KEYS):
                errors.append("{0}.{1}: unknown field".format(at, key))
            ftype = frag.get("type")
            if not (isinstance(ftype, str) and ftype and not re.search(r"[:\-\s]", ftype)):
                errors.append(at + ".type: required, without ':', '-' or spaces")
            elif ftype in types:
                errors.append("{0}.type: {1} is used twice".format(at, ftype))
            else:
                types.add(ftype)
            _checkBlock({k: frag[k] for k in ("car", "csv", "ambody", "name") if k in frag}, at, errors, allowPaths)
            for key in ("solvent", "catalyst", "mark_bonded"):
                if key in frag and not isinstance(frag[key], bool):
                    errors.append("{0}.{1}: must be true or false".format(at, key))

    params = recipe.get("params")
    if params is not None:
        if not isinstance(params, dict) or "bond_params.csv" not in params:
            errors.append("params: null (this installation's) or an object of files including bond_params.csv")
        else:
            for name, ref in sorted(params.items()):
                if name not in PARAMS_FILES:
                    errors.append("params.{0}: not a parameter file ({1})".format(name, ", ".join(PARAMS_FILES)))
                else:
                    _checkFile(ref, "params." + name, errors, allowPaths)

    bondTypes = recipe.get("bond_types", [])
    if not isinstance(bondTypes, list):
        errors.append("bond_types: must be a list")
        bondTypes = []
    for i, bt in enumerate(bondTypes):
        at = "bond_types[{0}]".format(i)
        if not (isinstance(bt, str) and BOND_TYPE.fullmatch(bt)):
            errors.append(at + ": must look like \"A:a-B:b\"")
        elif types and not {bt.split("-")[0].split(":")[0], bt.split("-")[1].split(":")[0]} <= types:
            errors.append("{0}: {1} names a fragment type not in fragments".format(at, bt))
    maxBonds = recipe.get("max_bonds", {})
    if not isinstance(maxBonds, dict):
        errors.append("max_bonds: must be an object of bond type to count")
    else:
        for bt, n in sorted(maxBonds.items()):
            if bt not in bondTypes:
                errors.append("max_bonds.{0}: not one of bond_types".format(bt))
            if not (_isInt(n) and n >= 0):
                errors.append("max_bonds.{0}: must be an integer, at least 0".format(bt))

    _checkStages(recipe.get("stages"), "stages", errors, allowPaths, 0, types)

    seed = recipe.get("seed")
    if seed is not None and not (_isInt(seed) and 0 <= seed < 2 ** 63):
        errors.append("seed: must be null or a non-negative integer")
    resources = recipe.get("resources", {})
    if not isinstance(resources, dict):
        errors.append("resources: must be an object")
    else:
        for key in sorted(set(resources) - _RESOURCE_KEYS):
            errors.append("resources.{0}: unknown field ({1})".format(key, ", ".join(sorted(_RESOURCE_KEYS))))
        for key in ("cpus", "gpus", "memory_mb"):
            if key in resources and not (_isInt(resources[key]) and resources[key] >= 0):
                errors.append("resources.{0}: must be an integer, at least 0".format(key))
        if "time" in resources and not (isinstance(resources["time"], str)
                                        and re.fullmatch(r"(\d+-)?\d{1,2}(:\d{2}){1,2}", resources["time"])):
            errors.append("resources.time: must be a Slurm time, e.g. \"02:00:00\"")
    return errors


def fileReferences(recipe):
    """(where, value) of every file the recipe references"""
    for i, frag in enumerate(recipe.get("fragments") or []):
        for key in ("car", "csv", "ambody"):
            if isinstance(frag, dict) and frag.get(key) is not None:
                yield "fragments[{0}].{1}".format(i, key), frag[key]
    for name, ref in sorted((recipe.get("params") or {}).items()):
        yield "params." + name, ref

    def stages(items, where):
        for i, stage in enumerate(items or []):
            at = "{0}[{1}]".format(where, i)
            if not isinstance(stage, dict):
                continue
            if "repeat" in stage:
                yield from stages(stage.get("stages"), at + ".stages")
            elif isinstance(stage.get("block"), dict):
                for key in ("car", "csv", "ambody"):
                    if stage["block"].get(key) is not None:
                        yield "{0}.block.{1}".format(at, key), stage["block"][key]

    yield from stages(recipe.get("stages"), "stages")


def references(recipe):
    """The sha256 digests (hex) of every file the recipe references by content, sorted"""
    return sorted({v.split(":", 1)[1] for _, v in fileReferences(recipe) if isRef(v)})


def canonical(recipe):
    """The recipe as canonical JSON text (sorted keys, no spaces)"""
    return json.dumps(recipe, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def recipeHash(recipe):
    return hashlib.sha256(canonical(recipe).encode("utf-8")).hexdigest()


def countSteps(stages):
    """How many operations the stages run, counting repeats"""
    total = 0
    for stage in stages or []:
        if "repeat" in stage:
            total += stage["repeat"] * countSteps(stage.get("stages"))
        else:
            total += 1
    return total


EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recipes")


def examples():
    """{name: description} of the example recipes shipped with Ambuild"""
    out = {}
    for fname in sorted(os.listdir(EXAMPLES_DIR)) if os.path.isdir(EXAMPLES_DIR) else []:
        if fname.endswith(".json"):
            with open(os.path.join(EXAMPLES_DIR, fname), encoding="utf-8") as f:
                out[fname[:-5]] = json.load(f).get("description", "")
    return out


def example(name, blocksDir=None):
    """An example recipe, with its files (named in it by file name alone) as paths in
    blocksDir (default: this installation's building blocks, ab_util.blocksDir())"""
    path = os.path.join(EXAMPLES_DIR, name + ".json")
    if not os.path.isfile(path):
        raise ValueError("No example recipe {0!r}; there are: {1}".format(name, ", ".join(examples())))
    with open(path, encoding="utf-8") as f:
        recipe = json.load(f)
    if blocksDir is None:
        from ambuild import ab_util

        blocksDir = ab_util.blocksDir()

    def place(block):
        for key in ("car", "csv", "ambody"):
            value = block.get(key)
            if isinstance(value, str) and not isRef(value) and os.path.basename(value) == value:
                block[key] = os.path.join(blocksDir, value)

    for frag in recipe.get("fragments", []):
        place(frag)
    for stage in _allStages(recipe.get("stages", [])):
        if isinstance(stage.get("block"), dict):
            place(stage["block"])
    return recipe


def describe():
    """The recipe format as JSON-friendly data, for forms and documentation"""
    return {
        "recipe_version": RECIPE_VERSION,
        "operations": {
            name: {"help": spec["help"],
                   "args": [{k: a[k] for k in ("name", "type", "default", "help", "minimum", "choices", "required")}
                            for a in spec["args"]]}
            for name, spec in OPERATIONS.items()
        },
        "cell": dict(_CELL_KEYS),
        "params_files": list(PARAMS_FILES),
        "max_repeat": MAX_REPEAT,
    }


# --- running

class Resolver:
    """Finds referenced files: sha256 references in blob directories (files named by their
    sha256, verified on use), paths relative to baseDir"""

    def __init__(self, blobDirs=(), baseDir=None):
        self.blobDirs = [os.path.abspath(d) for d in blobDirs]
        self.baseDir = baseDir

    def path(self, ref):
        if isRef(ref):
            digest = ref.split(":", 1)[1]
            for d in self.blobDirs:
                candidate = os.path.join(d, digest)
                if os.path.isfile(candidate):
                    if _sha256File(candidate) != digest:
                        raise RuntimeError("{0} does not match its sha256".format(candidate))
                    return candidate
            raise RuntimeError("No file for {0} in the blob directories {1}".format(ref, self.blobDirs or "(none)"))
        if self.baseDir is None:
            raise RuntimeError("File paths need a recipe file to be relative to: {0}".format(ref))
        path = ref if os.path.isabs(ref) else os.path.join(self.baseDir, ref)
        if not os.path.isfile(path):
            raise RuntimeError("No such file: {0}".format(path))
        return path


def _sha256File(path):
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _stageBlock(block, directory, stem, resolver):
    """Copy a block's files into directory as stem.car, stem.csv (and stem.ambody), as
    ab_fragment expects; returns the .car path"""
    os.makedirs(directory, exist_ok=True)
    for key in ("car", "csv", "ambody"):
        if block.get(key) is not None:
            shutil.copyfile(resolver.path(block[key]), os.path.join(directory, "{0}.{1}".format(stem, key)))
    return os.path.join(directory, stem + ".car")


def _kwargs(spec, stage, staging, resolver, index):
    kwargs = {}
    for arg in spec["args"]:
        value = stage.get(arg["name"], arg["default"])
        if value is None:
            continue
        if arg["type"] == "block":
            value = _stageBlock(value, os.path.join(staging, "cap{0}".format(index)), value.get("name", "cap"),
                                resolver)
        if arg["name"] == "settings":
            kwargs.update(value)
        else:
            kwargs[arg["kwarg"] or arg["name"]] = value
    return kwargs


class _Runner:
    def __init__(self, cell, staging, resolver, poreblazerExe):
        self.cell = cell
        self.staging = staging
        self.resolver = resolver
        self.poreblazerExe = poreblazerExe
        self.count = 0

    def stages(self, stages, top):
        for stage in stages:
            if "repeat" in stage:
                for _ in range(stage["repeat"]):
                    self.stages(stage["stages"], top=False)
                    if top:
                        self.cell.dump()
            else:
                self.operation(stage)
                if top:
                    self.cell.dump()

    def operation(self, stage):
        spec = OPERATIONS[stage["op"]]
        self.count += 1
        kwargs = _kwargs(spec, stage, self.staging, self.resolver, self.count)
        method = getattr(self.cell, spec["method"])
        if stage["op"] == "poreblazer":
            results = method(self.poreblazerExe, **kwargs)
            if results["returncode"] != 0:  # recorded as it is; a script carries on, a recipe stops
                raise RuntimeError("Poreblazer failed (exit code {0}); see {1}".format(
                    results["returncode"], results["directory"]))
            return results
        return method(**kwargs)


def _raiseCancelled(signum, frame):
    raise Cancelled("stopped by signal {0}".format(signum))


def run(recipe, outputDir, blobDirs=(), baseDir=None, runId=None, parentRunId=None, seed=None,
        poreblazerExe=None):
    """Build the recipe as a recorded run in outputDir; returns the run id.

    blobDirs: directories holding referenced files named by sha256; baseDir: the directory
    file paths are relative to (None: paths not allowed); seed overrides the recipe's.
    poreblazerExe: default POREBLAZER_EXE. A failed build raises, with the run recorded as
    failed.
    """
    errors = validate(recipe, allowPaths=baseDir is not None)
    if errors:
        raise RecipeError(errors)
    from ambuild import ab_cell, ab_run, ab_util

    if seed is None:
        seed = recipe.get("seed")
    poreblazerExe = poreblazerExe or os.environ.get("POREBLAZER_EXE")
    if any(s.get("op") == "poreblazer" for s in _allStages(recipe["stages"])) and not poreblazerExe:
        raise RuntimeError("The recipe runs Poreblazer: set POREBLAZER_EXE")
    outputDir = os.path.abspath(outputDir)
    ab_run.checkRunDirectory(outputDir)
    resolver = Resolver(blobDirs, baseDir)
    staging = tempfile.mkdtemp(prefix="ambuild-recipe-")
    try:
        if recipe.get("params"):
            paramsDir = os.path.join(staging, "params")
            os.makedirs(paramsDir)
            for name, ref in recipe["params"].items():
                shutil.copyfile(resolver.path(ref), os.path.join(paramsDir, name))
        else:
            paramsDir = ab_util.paramsDir()
        fragments = []
        for frag in recipe["fragments"]:
            car = _stageBlock(frag, os.path.join(staging, "fragments", frag["type"]), frag.get("name", frag["type"]),
                              resolver)
            fragments.append((frag, car))
        recipeFile = os.path.join(staging, "recipe.json")
        with open(recipeFile, "w", encoding="utf-8") as f:
            json.dump(recipe, f, indent=2, sort_keys=True)
            f.write("\n")

        cellSpec = recipe["cell"]
        cell = ab_cell.Cell(
            cellSpec["box"],
            paramsDir=paramsDir,
            atomMargin=cellSpec.get("atom_margin", _CELL_KEYS["atom_margin"]),
            bondMargin=cellSpec.get("bond_margin", _CELL_KEYS["bond_margin"]),
            bondAngleMargin=cellSpec.get("bond_angle_margin", _CELL_KEYS["bond_angle_margin"]),
            outputDir=outputDir,
            seed=seed,
        )
        with cell:
            cell.startRecording(runId=runId, parentRunId=parentRunId, recordScript=False)
            cell._runRecorder.addInput(recipeFile, "recipe", name=recipe["name"], recipe_sha256=recipeHash(recipe))
            for frag, car in fragments:
                cell.libraryAddFragment(car, fragmentType=frag["type"], solvent=frag.get("solvent", False),
                                        markBonded=frag.get("mark_bonded", False),
                                        catalyst=frag.get("catalyst", False))
            for bt in recipe.get("bond_types", []):
                cell.addBondType(bt)
            for bt, count in sorted(recipe.get("max_bonds", {}).items()):
                cell.setMaxBond(bt, count)
            _Runner(cell, staging, resolver, poreblazerExe).stages(recipe["stages"], top=True)
        return cell.runId
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _allStages(stages):
    for stage in stages:
        if "repeat" in stage:
            yield from _allStages(stage["stages"])
        else:
            yield stage


# --- command line

def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m ambuild.recipe", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="build a recipe as a recorded run")
    p.add_argument("recipe")
    p.add_argument("--output", required=True, help="the run directory (created; must not hold a run)")
    p.add_argument("--blobs", action="append", default=[], help="a directory of files named by sha256")
    p.add_argument("--seed", type=int, help="override the recipe's seed")
    p.add_argument("--run-id")
    p.add_argument("--parent-run-id")
    p = sub.add_parser("validate", help="check a recipe")
    p.add_argument("recipe")
    p.add_argument("--references-only", action="store_true", help="reject file paths, as the web GUI does")
    sub.add_parser("describe", help="print the operations and their arguments as JSON")
    sub.add_parser("examples", help="list the example recipes shipped with Ambuild")
    p = sub.add_parser("example", help="print an example recipe, with paths to this installation's building blocks")
    p.add_argument("name")
    p = sub.add_parser("hash", help="print the recipe's sha256")
    p.add_argument("recipe")
    args = parser.parse_args(argv)

    if args.command == "examples":
        for name, description in examples().items():
            print("{0}: {1}".format(name, description))
        return 0
    if args.command == "example":
        json.dump(example(args.name), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if args.command == "describe":
        json.dump(describe(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    recipe = _load(args.recipe)
    if args.command == "hash":
        print(recipeHash(recipe))
        return 0
    if args.command == "validate":
        errors = validate(recipe, allowPaths=not args.references_only)
        for e in errors:
            print(e, file=sys.stderr)
        if not errors:
            print("valid: {0} operations".format(countSteps(recipe["stages"])))
        return 1 if errors else 0
    signal.signal(signal.SIGTERM, _raiseCancelled)
    try:
        runId = run(recipe, args.output, blobDirs=args.blobs, baseDir=os.path.dirname(os.path.abspath(args.recipe)),
                    runId=args.run_id, parentRunId=args.parent_run_id, seed=args.seed)
    except RecipeError as e:
        print(e, file=sys.stderr)
        return 2
    print(runId)
    return 0


if __name__ == "__main__":
    sys.exit(main())
