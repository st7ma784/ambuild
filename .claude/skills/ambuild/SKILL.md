---
name: ambuild
description: Work with Ambuild, which builds amorphous porous polymer networks (e.g. carbon frameworks) from building blocks and measures their pores with Poreblazer, through its web GUI's API. Use it to search recorded runs and their results (surface area, pore limiting diameter, density), read built structures, write and check recipes, and queue runs, parameter sweeps and goal-directed campaigns on the lab's Slurm cluster. Use it when the user asks about Ambuild runs, builds, pores, sweeps or campaigns, or wants a structure built.
---

# Ambuild

Ambuild builds amorphous polymer networks by placing rigid building blocks ("fragments",
e.g. benzene rings, alkyne linkers) in a periodic cell and bonding their end groups:
**seed** blocks, **grow** new ones onto free end groups, **zip** nearby end groups together,
optionally **optimise** or run **MD** with HOOMD-blue, and analyse the pores with
**Poreblazer**. Builds are random, but a seed makes a build exactly reproducible.

People (and you) drive it through the web GUI's HTTP API. The helper
`scripts/ambuild_api.py` (standard library Python, beside this file) wraps the API. Every
command prints JSON, including links to the web pages. Use it rather than writing HTTP
calls yourself.

```sh
python .claude/skills/ambuild/scripts/ambuild_api.py status     # is it up? database, storage, agents
```

The address is `AMBUILD_API_URL` (default `http://127.0.0.1:8080`). If the GUI runs on
another host, the user may need a tunnel, e.g. `ssh -L 8080:127.0.0.1:8080 scc-hdd-01`.
Below, `api` stands for `python .claude/skills/ambuild/scripts/ambuild_api.py`.

## Rules

1. **Never queue, cancel or stop work without the user's agreement.** Commands that do
   (`submit`, `sweep`, `campaign`, `propose`, `cancel`, `campaign-action`, `save`) only
   check and preview without `--yes`. Run them without it first, show the user the preview
   (what, how many runs, where, which seeds), and add `--yes` only after they agree. For a
   campaign they ask you to steer, their agreement to its budget covers its rounds. Say so
   when you start, and report each round.
2. **Start small.** Try one run or a few before a large sweep or campaign. A sweep can hold
   up to 1,000 runs, and each is a Slurm job. A build of a few hundred atoms with Poreblazer
   takes seconds to minutes on 2 CPUs; larger cells, optimisation, MD and finer Poreblazer
   grids take much longer.
3. **Builds are random.** One run proves little. Compare over several seeds (a sweep's
   `seeds`, a campaign's `replicates`), and report the spread, not just the best value.
4. **Say where results come from.** Give run ids and page links, and keep measured values
   separate from your interpretation.
5. Leave other people's work alone: every user sees every run, and there is no sign-in.

## Finding and reading results

```sh
api runs --q "Li-ion" --poreblazer --sort surface_area --dir desc --limit 10
api runs --min-pld 1.52 --status finished            # pores wider than 1.52 A
api run RUN_ID                                       # provenance, Poreblazer results, last steps, structures
api events RUN_ID --type run_finished                # why a run failed (its error); --type step for progress
api structure RUN_ID [--step N] [--xyz]              # atoms, elements, fragments, blocks; --xyz for the file
api queue --state running --state queued
api submission N
api agents                                           # which backends (local, slurm) have an agent online
```

**What the results mean** (they are the run summaries' columns; Poreblazer's come from its
last analysis of the run or of its child runs):

| metric | meaning |
| --- | --- |
| `pore_limiting_diameter_a` | PLD, Å: the largest sphere that can pass through the pore network. Compare it with the size of what must pass: a bare Li⁺ is about 1.52 Å across (a solvated one several times that), He 2.6 Å, N₂ about 3.6 Å. |
| `maximum_pore_diameter_a` | the largest sphere that fits anywhere (the biggest cavity), Å |
| `percolated_dimensions` | in how many directions (0–3) the pore network spans the cell. 0: closed cavities only. A passage needs PLD > the molecule *and* ≥ 1 dimension. |
| `surface_area_m2_g` (also `_a2`, `_m2_cm3`) | accessible surface area |
| `helium_volume_cm3_g`, `geometric_volume_cm3_g` | pore volume |
| `density` | g/cm³, from the last build step. Ambuild's cells are often sparse (0.2–0.9). |
| `num_particles`, `num_blocks` | atoms, and separate blocks. Fewer blocks means a more connected network. |

## Recipes

A recipe describes a build as JSON. Get a saved one with `api recipes` and
`api recipe ID`. If Ambuild is installed locally, `python -m ambuild.recipe examples`
lists the shipped examples and `example NAME` prints one. The full list of operations and
their arguments comes from `api format`.

```json
{
  "recipe_version": 1,
  "name": "Carbon network for Li-ion channels",
  "cell": {"box": [30, 30, 30]},
  "fragments": [
    {"type": "A", "car": "blocks/benzene_135.car", "csv": "blocks/benzene_135.csv"},
    {"type": "B", "car": "blocks/acetylene.car", "csv": "blocks/acetylene.csv"}
  ],
  "bond_types": ["A:a-B:a"],
  "stages": [
    {"op": "seed", "count": 10, "fragment_type": "A"},
    {"repeat": 6, "stages": [{"op": "grow", "count": 12}, {"op": "zip", "bond_margin": 1.0, "bond_angle_margin": 30}]},
    {"op": "poreblazer", "threads": 2}
  ],
  "seed": 7
}
```

- **Fragments**: each needs a `.car` file (coordinates, atom types) and a `.csv` defining its end groups. Local paths are uploaded by the helper and replaced by `sha256:` references. `type` is a short name, and `bond_types` say which end groups may bond (`A:a-B:a` means end group `a` of A with end group `a` of B).
- **Operations**: `seed`, `grow`, `join`, `zip`, `optimise`, `md`, `md_optimise`, `delete_blocks`, `cap`, `poreblazer`, and `{"repeat": n, "stages": [...]}`. A checkpoint (structure and pickle) is written after each top-level stage and each pass of a top-level repeat.
- **Parameters**: `"params": null` means the server's bundled force-field files. `optimise` and `md` need parameters for every atom type in the recipe. Alkyne (sp) carbon, type `c1`, has bond lengths only, so recipes with acetylene cannot be optimised yet.
- **Seed**: set `seed` for a reproducible build (the same recipe and seed give the same structure). With no seed, the random state is recorded, so the run can still be replayed.
- `resources` (`cpus`, `gpus`, `memory_mb`, `time`, e.g. "02:00:00") become Slurm requests.

```sh
api validate --recipe my.json            # structure, operations, and files uploaded
api paths --recipe my.json               # every setting with its JSON pointer
api submit --recipe my.json --seed 3     # preview; then with --yes after the user agrees
api submit --recipe-id 12 --backend local --yes
```

## Sweeps: one recipe over many settings

Parameters are JSON pointers into the recipe (from `api paths`), each with a list of
values. Every combination is run, once per seed. On Slurm a sweep is one array job.

```json
{"parameters": [
   {"name": "box", "path": "/cell/box", "all": true, "values": [22, 26, 30]},
   {"name": "grow", "path": "/stages/1/stages/0/count", "values": [6, 12, 18]}],
 "seeds": [1, 2, 3]}
```

`"all": true` sets every element of a list (a cubic box). A pointer may add an argument a
stage does not have yet (e.g. `/stages/1/stages/1/clash_check`). Instead of `values`,
`"rows": [{"box": 22, "grow": 6}, ...]` lists points explicitly. Use
`api sweep --recipe my.json --spec sweep.json` (a preview; then `--yes`), and
`api sweep-get ID` for the results per point.

## Campaigns: searching for a goal

A campaign searches parameter ranges for structures that meet constraints and optimise an
objective, in rounds. A controller proposes each round with Bayesian optimisation (TPE by
default), or **you** do (method `external`).

```json
{
  "parameters": [
    {"name": "box", "path": "/cell/box", "all": true, "type": "float", "low": 20, "high": 35},
    {"name": "grow", "path": "/stages/1/stages/0/count", "type": "int", "low": 4, "high": 20},
    {"name": "passes", "path": "/stages/1/repeat", "type": "int", "low": 3, "high": 10}
  ],
  "constraints": [{"metric": "pore_limiting_diameter_a", "min": 1.52},
                  {"metric": "percolated_dimensions", "min": 1}],
  "objective": {"maximise": "density"},
  "replicates": 3, "method": "tpe", "initial_points": 8, "batch_size": 6,
  "budget": {"runs": 120}, "stop": {"no_improvement_rounds": 4}
}
```

- `type` is `float`, `int` (with `low`/`high`, optional `"log": true`) or `choice` (`"choices": [...]`, any JSON values, e.g. different fragments).
- The objective is `{"maximise": metric}`, `{"minimise": metric}` or `{"target": {"metric", "value"}}`. With constraints only, the campaign stops at the first point that meets them.
- A point is scored by the mean over its `replicates` seeds, and a constraint holds when `feasible_fraction` (default 0.5) of them meet it. Methods: `tpe`, `gp`, `random`, `qmc`, `grid`, `external`.
- A campaign ends when the budget is spent, the goal is met (`stop.feasible_points`), or there is no improvement for `stop.no_improvement_rounds` rounds.

```sh
api campaign --recipe-id 12 --spec campaign.json      # preview; then --yes
api campaign-get ID [--last 10]                       # trials, best, what happens next
api propose ID '[{"box": 22.5, "grow": 14, "passes": 8}]' --by claude   # external: queue a round (preview; --yes)
api campaign-action ID pause|resume|stop
```

**Steering an `external` campaign yourself:**
1. Read `campaign-get`: the trials, their means, feasibility and the best point.
2. Propose a round that tests a clear hypothesis, e.g. "density rises with grow count until blocks stop fitting; try grow 16–20 at box 20–22". Keep points within the bounds, and propose as many as `next.propose` says.
3. Wait until the round's runs have finished (`next` asks for points again) before proposing the next round.
4. Explain each round's reasoning to the user, and stop when the goal is met or improvements stall.

Points record who proposed them, so your rounds can later be compared with TPE's.

## When things go wrong

- A run **failed**: `api run ID` shows the error, and `api events ID --type run_finished` gives details. Common causes: an unknown fragment type, missing force-field parameters (optimise or MD), or Poreblazer failing (its exit code is in the error).
- **Queued but nothing happens**: `api agents`. The chosen backend needs an agent online, and campaigns need the `campaigns` controller unless their method is `external`.
- An HTTP 422 or 409 from the helper comes with a list of errors, each naming where the problem is (e.g. `stages[1].stages[0].count: must be at least 1`). Fix the recipe or spec and retry.
- Structures: `api structure ID` summarises a checkpoint; the run page has a 3D viewer (`page` in the output).
