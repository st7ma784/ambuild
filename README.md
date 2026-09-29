# Amorphous Builder (Ambuild)

Ambuild builds amorphous polymer networks, such as porous organic polymers and carbon
frameworks, from rigid building blocks, and measures the pores of what it builds.

It places **fragments** (e.g. benzene rings, alkyne linkers) in a periodic cell, then
repeatedly:
- **seeds** blocks at random positions;
- **grows** new ones onto free end groups;
- **zips** nearby end groups together;
- relaxes the structure by **geometry optimisation** or **molecular dynamics** with [HOOMD-blue](https://glotzerlab.engin.umich.edu/hoomd-blue/).

[Poreblazer](https://github.com/st7ma784/poreblazer) then gives the surface area, pore
sizes and pore volume. Builds are random, but a seed makes them exactly reproducible.

Ambuild is developed by [Abbie Trewin's](https://www.lancaster.ac.uk/sci-tech/about-us/people/abbie-trewin)
group at Lancaster University. Follow it on [Twitter](https://twitter.com/Ambuild2).

**What you can do with it**

- Build from a **Python script** (`ambuild.ab_cell.Cell`) or from a **recipe**, a JSON description of the build that can be validated, shared and submitted.
- **Record** every build as a self-contained run directory (provenance, every step, inputs), and **replay** any recorded run.
- **Analyse** pores with Poreblazer: surface area, pore limiting diameter, pore size distribution, percolation.
- **Run many builds** through a web GUI: submit to a Slurm cluster or a local worker, browse runs and their 3D structures, **sweep** parameters, and run **campaigns** that search settings for structures meeting a goal (Bayesian optimisation).
- Let an **AI assistant** (e.g. Claude Code) drive all of that through a skill file.

## Quick start

The Docker image has Ambuild, HOOMD-blue and Poreblazer. Build it and run the example
recipe, a benzene-alkyne carbon network whose pores should let lithium ions through:

```sh
docker build -t ambuild .
docker run --rm -v "$PWD:/work" -w /work --user "$(id -u):$(id -g)" ambuild sh -c \
  'python -m ambuild.recipe example li_ion_carbon > li.json && python -m ambuild.recipe run li.json --output runs/li'
```

`runs/li` then holds the run:
- `run.json`: status, versions, inputs and seed;
- `events.jsonl`: every step and the Poreblazer result;
- one `step_N.xyz` structure and one pickle per checkpoint;
- the log and step table.

Without Docker, see [Installation](#installation).

## Your first build

### As a Python script

```python
import os
from ambuild import ab_cell, ab_util, recipe

blocks = ab_util.blocksDir()                                   # the bundled building blocks
params = os.path.join(recipe.EXAMPLES_DIR, "params", "gaff_benzene_alkyne")

with ab_cell.Cell([30, 30, 30], paramsDir=params, outputDir="runs/first", recordRun=True,
                  seed=7, typedBondLengths=True) as cell:
    cell.libraryAddFragment(os.path.join(blocks, "benzene_135.car"), fragmentType="A")
    cell.libraryAddFragment(os.path.join(blocks, "acetylene.car"), fragmentType="B")
    cell.addBondType("A:a-B:a")          # end group a of A may bond to end group a of B
    cell.seed(10, fragmentType="A")
    for _ in range(6):
        cell.growBlocks(12, maxTries=50)
        cell.zipBlocks(bondMargin=1.0, bondAngleMargin=30)
        cell.optimiseGeometry(rigidBody=True, optCycles=20000)    # needs HOOMD-blue
        cell.dump()                      # a checkpoint: pickle and structure
    pores = cell.poreblazer(ab_util.poreblazerExe(), threads=2)
    print(pores["pore_limiting_diameter_A"], pores["surface_area_m2_g"])
```

The main operations are:
- **cell set-up:** `seed`, `growBlocks`, `joinBlocks` (move blocks to bond them) and `zipBlocks`;
- **editing:** `deleteBlocks` and `capBlocks`;
- **relaxing** (HOOMD-blue): `optimiseGeometry`, `runMD` and `runMDAndOptimise`;
- **output:** `poreblazer`, `dump`, and `writeXyz`/`writeCml`/`writeCar`.

`standard_inputs/` has more scripts.

### As a recipe

The same build as data:

```json
{
  "recipe_version": 1,
  "name": "benzene-alkyne network",
  "cell": {"box": [30, 30, 30], "typed_bond_lengths": true},
  "fragments": [
    {"type": "A", "car": "blocks/benzene_135.car", "csv": "blocks/benzene_135.csv"},
    {"type": "B", "car": "blocks/acetylene.car", "csv": "blocks/acetylene.csv"}
  ],
  "params": {"bond_params.csv": "params/bond_params.csv", "angle_params.csv": "params/angle_params.csv",
             "dihedral_params.csv": "params/dihedral_params.csv", "improper_params.csv": "params/improper_params.csv",
             "pair_params.csv": "params/pair_params.csv"},
  "bond_types": ["A:a-B:a"],
  "stages": [
    {"op": "seed", "count": 10, "fragment_type": "A"},
    {"repeat": 6, "stages": [{"op": "grow", "count": 12}, {"op": "zip", "bond_margin": 1.0, "bond_angle_margin": 30},
                              {"op": "optimise", "cycles": 20000}]},
    {"op": "poreblazer", "threads": 2}
  ],
  "seed": 7
}
```

```sh
python -m ambuild.recipe validate recipe.json     # every problem, with where it is
python -m ambuild.recipe run recipe.json --output runs/net
python -m ambuild.recipe describe                 # every operation and its arguments
python -m ambuild.recipe examples                 # the shipped examples; "example NAME" prints one
```

File paths are relative to the recipe file. `"params": null` uses the installation's
parameter files. A checkpoint is written after each top-level stage and each pass of a
top-level repeat, and the recipe itself is stored with the run's inputs.

## Building blocks and force fields

- **A building block** is a `.car` file (coordinates, atom types, elements) and a `.csv` of the same name listing its **end groups**. Each end group row gives:
  - the bonding atom;
  - its cap atom (removed when it bonds);
  - a dihedral reference atom;
  - its type (e.g. `a`).

  `bond_types` such as `A:a-B:a` say which end groups may bond. The bundled blocks are in `tests/blocks` (`ab_util.blocksDir()`, or `AMBUILD_BLOCKS_DIR`).
- **Parameter files** (a directory of `bond_params.csv`, `angle_params.csv`, `dihedral_params.csv`, `improper_params.csv` and `pair_params.csv`) give force-field terms by atom type, in HOOMD-blue's forms and units (kcal/mol, Å).
  - Building needs only bond lengths.
  - Optimisation and MD need every term for every atom type in the cell. HOOMD-blue lists any that are missing.
  - The bundled set (`tests/params`, `AMBUILD_PARAMS_DIR`) is a small one used by the tests.
- **`ambuild/recipes/params/gaff_benzene_alkyne`** holds complete GAFF 1.81 parameters for aromatic carbon (`ca`), a ring carbon that links to another block (`cp`, GAFF's biaryl carbon), aromatic hydrogen (`ha`) and sp (alkyne) carbon (`c1`). They are generated from AmberTools' `gaff-1.81.dat` by `scripts/gaff_params.py`, with their source recorded (its README gives the choices made). The blocks `benzene_135` (linked at 1,3,5), `benzene_14` (para) and `acetylene` use these types and measured geometries (C–C 1.397 Å, C–H 1.084 Å; C≡C 1.203 Å).
- **Typed bond lengths** (`Cell(typedBondLengths=True)`, recipe `"typed_bond_lengths": true`) join blocks at the parameter file's bond length for the two atom types, rather than a generic single bond (1.53 Å for C–C).
  - Use them when the parameter file types the linking atoms for the link. The GAFF set does: `cp-c1` 1.44 Å for an aryl–alkyne link, `cp-cp` 1.4854 Å for a biaryl one.
  - Don't use them with the legacy test blocks and parameters, whose `cp` is any aromatic carbon: there `cp-cp` is the ring bond (1.387 Å), and rings would be joined too close (a biaryl link is about 1.49 Å).
  - They are off by default, so existing scripts and recorded runs build as before.

With these, after the recipes' rigid-body optimisations:

- `li_ion_carbon` has its ring–alkyne junctions at 1.445 ± 0.006 Å, with straight alkynes (within 2° of 180°) and trigonal ring carbons (within 2.3° of 120°) (`tests/testLiIonCarbon.py`);
- `benzene_network` has its biaryl links at 1.48–1.51 Å, at 120 ± 1.4° to the rings, with linked rings twisted a median 36° (biphenyl: about 44° in the gas phase) (`tests/testBenzeneNetwork.py`).

## Recording and reproducing runs

Give a cell an output directory and `recordRun=True` (recipes always record):

```python
with ab_cell.Cell([30, 30, 30], paramsDir=params, outputDir="runs/my-build", recordRun=True, seed=42) as cell:
    ...
```

- **What's recorded:** `run.json` (run id, status, versions, host, Slurm job, cell settings, the seed, and inputs with their sha256), `events.jsonl` (every build step, file written and Poreblazer result), and `inputs/` (copies of the script or recipe, parameter files, building blocks and the random number generator's state). Leaving the `with` block marks the run `finished`, or `failed` if an exception escaped.
- **Reproducible:** every random choice uses Python's random number generator, so the same script or recipe and `seed` give the same structure on any machine and in any process.
- **Replay:** without a seed, the generator's recorded state lets you replay the run with `Cell(..., randomState="runs/my-build")`.
- **Checkpoints:** pickles (`cell.dump()`) keep the generator's state too, so `ab_util.cellFromPickle()` resumes a build exactly where it stopped.

See [docs/architecture.md](docs/architecture.md).

## Pore analysis

`cell.poreblazer(exe, threads=N, **settings)`, or the recipe operation `poreblazer`, runs
[Ambuild's Poreblazer fork](https://github.com/st7ma784/poreblazer), which is upstream 3.0.5 with its OpenMP code
fixed: the output is identical, and it runs faster on several threads. It reports:

| result | meaning |
| --- | --- |
| pore limiting diameter (Å) | the largest sphere that can pass through the pore network: compare with what must pass (a bare Li⁺ is about 1.52 Å across, He 2.6 Å, N₂ about 3.6 Å) |
| maximum pore diameter (Å) | the largest sphere that fits anywhere |
| percolated dimensions | in how many directions (0–3) the pore network spans the cell |
| surface area | accessible surface, in m²/g, m²/cm³ and Å² |
| helium and geometric volume | pore volume, cm³/g |
| pore size distribution | differential and cumulative |

`cubelet_size` trades accuracy for speed: 0.3 Å is several times faster than the default
0.2 Å ([docs/benchmarks.md](docs/benchmarks.md)). `percolation_labelling="exact"` uses exact
cluster labelling.

## Running many builds

The web GUI (`services/web`) and its agents turn Ambuild into a shared service:

- **Browse** every recorded run: filters by result, step charts, pore size distributions, a 3D structure viewer, and comparisons.
- **Submit** recipes to a Slurm cluster (one job each, through `deploy/slurm`), from a login node or through slurmrestd, or to a local or K3s worker.
- **Sweep** a recipe over a grid of settings, CSV rows or seeds. On Slurm a sweep runs as one array job, and its page plots any result against each setting.
- **Run campaigns:** give the settings to search and a goal, e.g. "the highest density whose pores still let Li⁺ through". A controller proposes each round with Bayesian optimisation (Optuna), or you or an AI agent do.

A demo runs everything on a laptop:

```sh
docker compose -f deploy/docker-compose.yml --profile web up -d --build    # http://127.0.0.1:8080
```

See [deploy/README.md](deploy/README.md) for Slurm, K3s and the Helm chart,
[docs/deployment.md](docs/deployment.md) for running each part on its own machine (from
the published images), and [docs/web-gui.md](docs/web-gui.md) for the design.

## Related projects

`external/liminal` is a git submodule: [liminal](https://github.com/st7ma784/liminal)
(private for now), a separate project for electronic-structure calculations of ions
getting into and out of porous materials. It isn't part of Ambuild, in the same way
Poreblazer and HOOMD-blue aren't.
- **How they connect:** liminal reads the boxes Ambuild exports ([docs/export.md](docs/export.md)).
  Neither imports the other's code.
- **Cloning:** you need it only to work on liminal: `git submodule update --init
  external/liminal`, with access to the repository. Ambuild's builds, images and CI never use it.

## Working with an AI assistant

`.claude/skills/ambuild/SKILL.md` teaches an AI assistant to use the web GUI:
- find runs by their results;
- read structures;
- write recipes;
- queue runs, sweeps and campaigns, always showing you a preview first.

Claude Code picks it up in this repository, and other agents can be given the file. Its helper,
`.claude/skills/ambuild/scripts/ambuild_api.py`, is a command-line client for the API that
people can use too (`--help`).

## Installation

- **To build structures**, you need Python 3.9+ and NumPy:
  ```sh
  pip install .                        # or pip install -e . to develop Ambuild
  ```
- **For optimisation and MD**, you need HOOMD-blue 4 or later (tested with 7.2). It is not on PyPI; install it from conda-forge:
  ```sh
  micromamba create -n ambuild -c conda-forge python=3.12 "hoomd=7.2=cpu*" numpy
  micromamba activate ambuild && pip install .
  ```
  Use `"hoomd=7.2=gpu*"` for CUDA. Running across MPI ranks (`AMBUILD_HOOMD_LAUNCHER`, `srun --ntasks=N`) needs an MPI build of HOOMD-blue from source.
- **For pore analysis**, you need Poreblazer. Build the fork's `ambuild` branch with gfortran:
  ```sh
  git clone -b ambuild https://github.com/st7ma784/poreblazer.git && make -C poreblazer/src
  export POREBLAZER_EXE=$PWD/poreblazer/src/poreblazer.exe
  ```
- **Docker:**
  - `docker build -t ambuild .` builds everything (about 590 MB). `--target ambuild` leaves out Poreblazer, and `--build-arg HOOMD_VARIANT=gpu` builds for CUDA.
  - `tests/docker/hoomd7.Dockerfile` is just the HOOMD-blue environment, for running this checkout (`misc/run_ambuild_docker.sh script.py`).
  - [docs/install.md](docs/install.md) covers installing Docker and NVIDIA support on a Linux host.

Scripts and recipes find their data through:

| Variable | Default | Purpose |
| --- | --- | --- |
| `AMBUILD_PARAMS_DIR` | `tests/params` in the checkout | parameter files (`ab_util.paramsDir()`, recipes' `"params": null`) |
| `AMBUILD_BLOCKS_DIR` | `tests/blocks` in the checkout | building blocks (`ab_util.blocksDir()`) |
| `POREBLAZER_EXE` | `poreblazer.exe` or `poreblazer` on the `PATH` | Poreblazer (`ab_util.poreblazerExe()`) |

## Running the tests

```sh
cd tests && PYTHONHASHSEED=0 python run_tests.py
```

Tests that need HOOMD-blue or Poreblazer are skipped without them, and
`tests/run_tests_docker.sh` runs the full suite in the image. `run_tests.py` reseeds the
random number generator before each test (`AMBUILD_TEST_SEED`), so runs are reproducible.
The web GUI, agents and campaign controller have their own tests under `services/`, run
through Docker Compose ([deploy/README.md](deploy/README.md)).

## Documentation

| | |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | how runs are recorded, uploaded and stored |
| [docs/web-gui.md](docs/web-gui.md) | the web GUI, agents, sweeps, campaigns and the agent skill, milestone by milestone |
| [deploy/README.md](deploy/README.md) | Docker Compose, Slurm, K3s and Helm |
| [docs/deployment.md](docs/deployment.md) | a server-room deployment: which machine runs what, the network, and which URLs and secrets go where |
| [docs/export.md](docs/export.md) | exporting boxes for DFT and other codes (spec): the files, formats and tests |
| [docs/ion-maps.md](docs/ion-maps.md) | ion maps: where Li⁺, Na⁺ and K⁺ sit and how easily they cross a structure (liminal), as a recipe stage, metrics and viewer overlays |
| [docs/carbon-linkers.md](docs/carbon-linkers.md) | carbon linkers that join themselves and each other: rings, polyyne alkynes, sp² nodes, allene, cyclopropenyl, propargyl |
| [docs/benchmarks.md](docs/benchmarks.md) | HOOMD-blue and Poreblazer performance |
| [docs/install.md](docs/install.md) | Docker and NVIDIA set-up on a Linux host |
| [docs/CHANGELOG.md](docs/CHANGELOG.md), [TODO.md](TODO.md) | what changed, and what is planned |

Ambuild is released under the GNU General Public License v3 (see [LICENSE](LICENSE)).
