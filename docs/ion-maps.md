# Ion maps

Where an ion (Li⁺, Na⁺, K⁺) sits in a structure, and how hard it is for it to cross it.
An `ion_map` recipe stage runs [liminal](https://github.com/st7ma784/liminal) and records
each ion's figures with the run. They then appear:
- on the run page, and overlaid on the structure in the 3D viewer;
- in sweeps and campaigns, as metrics.

liminal is a separate project (the submodule `external/liminal`; private for now). Ambuild
runs it as an external program, as it does Poreblazer, and never imports it.

## What the figures mean

liminal computes the ion's energy on a grid over the cell. The grid's low points are the
**sites**. A site's **escape barrier** is how far above the site's energy the ion must go
before it can cross the whole cell. Its **crossing path** is the route it takes, and the
path's highest point is the **bottleneck**.

The energies are classical: Lennard-Jones, with UFF parameters for the framework and
Joung–Cheatham parameters for the ion. They aren't calibrated against DFT yet (liminal's
roadmap, step E), so use them to compare ions and structures, not as absolute values. kT
at room temperature is about 0.6 kcal/mol, so a barrier of a few kcal/mol is crossed
easily and one above about 20 is not. Each result records its tier, e.g. "classical …
uncalibrated".

| Metric (Li⁺; Na⁺ and K⁺ likewise, with `na_` and `k_`) | Meaning |
| --- | --- |
| `li_site_energy` | the lowest site's energy, kcal/mol (negative: the ion is attracted) |
| `li_escape_barrier` | the barrier to leave that site and cross the cell, kcal/mol |
| `li_lowest_barrier` | the lowest escape barrier of any site: how easily Li⁺ moves anywhere |
| `li_sites` | the number of sites |

The run page also shows each map's count of escaping sites and median barrier.

## A recipe stage

```json
{"op": "ion_map", "ions": ["Li+", "Na+", "K+"], "spacing": 0.5, "cutoff": 10.0, "max_energy": 30.0, "max_paths": 20}
```

| Argument | Default | |
| --- | --- | --- |
| `ions` | `["Li+"]` | the ions liminal has parameters for: `Li+`, `Na+`, `K+` |
| `spacing` | 0.5 | grid spacing, Å. 0.5 takes about 10 s per ion for a 30 Å cell; 1.0 is 8× faster and coarser |
| `cutoff` | 10.0 | interaction cut-off, Å |
| `max_energy` | 30.0 | barriers above this, kcal/mol, are left unresolved (a site that cannot escape below it) |
| `max_paths` | 20 | crossing paths for this many of the lowest sites |

The stage writes an `ion_map_<n>/` directory in the run holding:
- the structure it mapped: `structure.xyz` and `structure.topology.json` (`docs/export.md`);
- for each ion, `<ion>/map.json` and `<ion>/energy.cube`, plus liminal's log, `<ion>.log`.

An `ion_map_result` event per ion records the figures; the files are uploaded with the
run. The stage fails the build if liminal fails for any ion, or writes a results format
this Ambuild doesn't read (it reads "liminal-map" version 1).

**Example recipes:** `li_ion_carbon_ions` and `benzene_network_ions`. Each is its base
recipe followed by a map of Li⁺, Na⁺ and K⁺ (`python -m ambuild.recipe example NAME`).

**Example campaigns** (`ambuild/recipes/campaigns/`, `ambuild.campaign.examples()`), for
`li_ion_carbon_ions` and recipes of the same shape:

| Campaign | Aims at |
| --- | --- |
| `easiest_li_transport` | the lowest Li⁺ barrier, keeping pores Li⁺ can pass through (PLD ≥ 1.52 Å) and a pore network that spans the cell |
| `ion_sieve` | the lowest Li⁺ escape barrier while K⁺ stays trapped (K⁺ barrier ≥ 3 kcal/mol): a structure that passes Li⁺ and holds K⁺ back |

Paste a spec into the New campaign page's spec box. Whether a sieve is possible depends
on the structures a recipe can make. `li_ion_carbon`'s open, alkyne-linked network lets
every ion cross easily: in liminal's unoptimised test structure from it, the highest
escape barriers are about 0.4 kcal/mol for Li⁺, 0.9 for Na⁺ and 1.9 for K⁺ (K⁺ also binds
most strongly, its lowest site at −3.2 kcal/mol against Li⁺'s −0.7). So `ion_sieve` will
need tighter structures.

## In the viewer

When a checkpoint has ion maps, the viewer shows an **Ion map** row:
- **Surface:** the energy surface at a level you can change. It starts at the lowest site's
  escape energy, so it encloses the region that site's ion can reach just as it becomes
  free to cross the cell.
- **Sites:** green if they can escape, red if trapped; the lowest site is larger.
- **Paths:** the crossing paths, drawn as lines wrapped into the cell, with a sphere at
  each bottleneck. Spheres or tubes along every path were too heavy to draw together with
  the sites.

The address can set the view, so it can be shared as a link: `?ion=K%2B` picks the ion,
`?ion=` shows none, and `?layers=surface,sites` picks the layers.

## Setting it up

The stage needs liminal wherever builds run:
- **`LIMINAL_EXE`:** a command, e.g. `liminal` or `/opt/venv/bin/python -m liminal`. The
  default is `liminal` on the PATH.
- **Installing it:** with the energy map's extra, `pip install "external/liminal[map]"` from
  a checkout with the submodule (`git submodule update --init external/liminal`, which
  needs access to the repository).
- **On Slurm through slurmrestd:** add `LIMINAL_EXE` (or a PATH that has it) to
  `AMBUILD_SLURMREST_ENV` (`docs/deployment.md`).
- **The Compose demo:** add liminal to the agent's image:

  ```sh
  docker compose -f deploy/docker-compose.yml --profile web build agent
  docker build -f deploy/demo/liminal.Dockerfile -t ambuild-agent-liminal external/liminal
  docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.liminal.yml --profile web up -d
  ```

The published images don't include liminal while its repository is private.

## Tests

- **`tests/testIonMap.py`** uses `tests/fake_liminal.py`, a stand-in that writes liminal's
  documented results format, so it needs no liminal. It covers:
  - the figures, events and artifacts;
  - failures, other format versions, and a missing program;
  - the example recipes and campaigns.
  One further test runs the real liminal where it's installed.
- **The web tests** check the run summary's metrics (the latest map of each ion), the run
  page's table, and the viewer's per-frame ion maps.
