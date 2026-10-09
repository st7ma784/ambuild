# xTB checks on built structures (spec)

Status: built, X0 to X5. The milestones are at the end, with what was left open: the
memory estimate beyond 944 atoms, an absolute threshold for review, and the fan-out for
sweeps and the `slurmrest` agent.

Users already take Ambuild's cells to xTB by hand to check them: are the bonds the force
field made sensible, and do the pore figures survive a relaxation at a better level of
theory? This spec makes that a recipe stage, so the check is recorded with the run, shows
in the web GUI and MLflow, and can gate or flag runs in review.

It lives in Ambuild, as quality control on the build itself. Energies for ion binding stay
with liminal (its L2 tier), which also plans to use `tblite`.

## Goals

- **One stage, recorded like the others:** `{"op": "xtb"}` writes its files to
  `xtb_<n>/` in the run directory, emits an `xtb_result` event and gives `xtb_*` metrics for
  sweeps, campaigns and review.
- **Ambuild never imports xTB.** The stage runs a worker as a subprocess on the checkpoint's
  exported structure (`docs/export.md`) and reads one JSON results file, as the `conduction`
  and `poreblazer` stages do. A crash or a memory kill in xTB can't take the build with it.
- **A failed or slow check never costs the build.** On Slurm it runs after the build, as
  child runs, like the Poreblazer fan-out.
- **Every number names its method** (GFN-FF, GFN1-xTB, GFN2-xTB) and program version.

## Non-goals

- Replacing HOOMD-blue: xTB doesn't drive the build's optimisation or MD.
- Ion binding energies, voltages and barriers (liminal).
- Variable-cell relaxation: the cell stays as built.

## Which program

[xtb-python](https://github.com/grimme-lab/xtb-python) is "no longer in active development"
and its README recommends `tblite`, so it isn't used.

| Method | Program | Why |
| --- | --- | --- |
| `gfn1`, `gfn2` | `tblite` (its Python API; ASE's LBFGS with its calculator to relax) | periodic, maintained, and its GFN1 agrees with CP2K's (below) |
| `gfnff` | the `xtb` binary, 6.7 or later (its own optimiser to relax) | a second opinion from a force field. It was meant as the cheap tier for large cells, but on periodic cells it costs as much as GFN1 (Scaling, below) |

### Measured (2026-10-08)

On liminal's fixture `li_ion_carbon/step_7.xyz`: 472 atoms (C and H), a 30 Å periodic
cell, built without its optimisation stage, so the junctions are unrelaxed. Run in a
container with 8 threads and a 4 GB memory limit, conda-forge packages.

| Calculation | Result | Time |
| --- | --- | --- |
| `tblite` GFN1-xTB, single point | converged; largest force 5.3 eV/Å | 17 s |
| `tblite` GFN2-xTB, single point | converged; largest force 5.2 eV/Å | 21 s |
| `tblite` GFN1-xTB, 15 LBFGS steps (ASE) | energy −11.0 eV, largest force 2.7 eV/Å, RMSD 0.15 Å | 201 s (13 s a step) |
| `xtb` 6.7.1 GFN-FF, single point | exit 0 | 7.5 s |
| `xtb` 6.7.1 GFN1-xTB, single point | exit 0, but see below | 168 s |
| `xtb` 6.4.1 (what conda-forge installs beside `tblite-python`) | GFN-FF and GFN2 fail at once; GFN1 segfaults after the SCF | - |

What that shows:

- **Periodic GFN1 and GFN2 work in `tblite`,** at a cost that suits a per-checkpoint check
  for cells of this size.
- **Both methods agree the unrelaxed cell is strained** (about 5 eV/Å), which is the
  answer the check exists to give.
- **The `xtb` binary needs its own environment.** Installed beside `tblite-python`, conda
  resolves it to a 2022 version that fails.

Settled since:

- **`tblite`'s periodic GFN1 is right, and the `xtb` binary's is the outlier.** The binary
  gave −729.16 Eh for this cell against `tblite`'s −735.99 Eh, with a gap of 0.0003 eV.
  CP2K 2024.3's GFN1-xTB gives −735.986 Eh (below). The stage uses `tblite` for tight
  binding and never the binary.
- **Periodic GFN-FF optimisation works, with one trap.** `xtb --gfnff --opt` relaxes a
  periodic cell, but it relaxes the lattice too: a 12 Å cell holding one benzene shrank to
  11.93 Å. The worker passes `--nocellopt`, and refuses a result whose cell changed.

### Against CP2K (2026-10-09)

The same single point from `tblite` 0.7.0 and from CP2K 2024.3 (its own implementation of
GFN1-xTB), with `benchmarks/compare_xtb_cp2k.py`:

| Cell | Atoms | Largest force, `tblite` | CP2K | Largest difference on any atom | Energy difference |
| --- | --- | --- | --- | --- | --- |
| benzene with one C–H pushed out 0.2 Å, 10 Å cell | 12 | 6.5878 eV/Å | 6.5880 eV/Å | 0.0006 eV/Å | 0.12 meV per atom |
| `li_ion_carbon` without its optimisation stages, 30 Å cell | 472 | 5.3055 eV/Å | 5.3047 eV/Å | 0.0025 eV/Å | 0.38 meV per atom |

- **The forces are the method's, not one program's:** the two agree to 0.05% of the
  largest force, and name the same atom as the most strained.
- **The energies differ by under 0.4 meV per atom,** which is expected: CP2K runs at zero
  electronic temperature, `tblite` at 300 K.
- **CP2K took 6.6 minutes for the 472-atom cell,** `tblite` 19 s, on the same 8 threads.
  CP2K's settings were not tuned, so that is no verdict on CP2K, but `tblite` is the
  right tool for a check per checkpoint.

### Scaling (2026-10-09)

The worker's single point on the same cell and its 2×1×1 and 2×2×1 supercells, 8 threads,
in a container limited to 4 GB:

| Method | 472 atoms | 944 atoms | 1,888 atoms |
| --- | --- | --- | --- |
| GFN1-xTB (`tblite`) | 19 s, 0.54 GB | 109 s, 1.9 GB | killed: out of memory at 3.4 GB |
| GFN2-xTB (`tblite`) | 28 s, 0.58 GB | 122 s, 2.1 GB | not run |
| GFN-FF (`xtb` 6.7.1) | 14 s, 0.49 GB | 71 s, 1.4 GB | killed: out of memory at 3.3 GB |

- **Doubling the atoms costs 5 to 6 times the time and 3 to 4 times the memory.** At that
  rate a 1,900-atom cell needs roughly 10 minutes and 6 to 7 GB for one single point, and a
  50-step relaxation most of a working day. That figure is extrapolated: it didn't fit in
  the test machine's memory.
- **Periodic GFN-FF is no cheaper than GFN1,** in time or memory. It isn't the way to
  reach larger cells, so its atom limit is the same.
- **The `xtb` binary needs an unlimited stack.** With the usual 8 MB it segfaults at 944
  atoms; the worker raises the limit for it.
- **Supercells agree with the cell:** the energy per atom, the largest force and the gap
  are the same to 4 or more figures, which checks the periodic treatment.
- **Larger cells need another route:** more memory on the cluster for cells up to a few
  thousand atoms, and beyond that clusters cut from the cell (liminal's L1) or an ML
  potential, not a whole-cell calculation.

## The stage

    {"op": "xtb"}

| Setting | Default | Meaning |
| --- | --- | --- |
| `method` | `gfn1` | `gfnff`, `gfn1` or `gfn2` |
| `mode` | `single_point` | `single_point`: energy and forces at the built geometry. `relax`: also a fixed-cell relaxation |
| `max_steps` | 50 | `relax`: the most optimiser steps; stopping at the cap is recorded, not an error |
| `fmax` | 0.05 | `relax`: stop when the largest force is below this (eV/Å) |
| `poreblazer` | false | `relax`: run Poreblazer (`POREBLAZER_EXE`) on the built and on the relaxed structure, with the settings of the recipe's last `poreblazer` stage (else the defaults), and record both and the change |
| `max_atoms` | 2000 | refuse a larger cell before starting, as Poreblazer's `memory_limit_mb` does. A cell near the limit needs about 7 GB |
| `threads` | all CPUs | `OMP_NUM_THREADS` for the worker |
| `memory_limit_mb` | none | the memory the worker may use: a cell whose estimate (Scaling) is larger is refused before starting |
| `charge` | from the structure | the cell's net charge (e), a whole number |

- **The worker:** `python -m ambuild.xtb_worker STRUCTURE --out xtb.json ...`. It uses
  `tblite`'s own interface for single points and ASE's LBFGS with `tblite`'s calculator to
  relax, or calls the `xtb` binary for `gfnff`. `ambuild.xtb`, which the rest of Ambuild
  imports, needs only the standard library.
- **Relaxing with `gfnff`** uses `xtb`'s own optimiser, with the cell held fixed.
  - `max_steps` is its cycle cap. `fmax` picks its level (`tight` below 0.03 eV/Å, `normal`
    below 0.1, else `crude`), since its levels converge on the gradient's norm, not on the
    largest force.
  - Whether `fmax` was reached is measured afterwards, by a single point on the geometry
    it wrote, which is also where the relaxed energy and forces come from.
  - `xtb` may wrap atoms back into the cell; the worker moves each to the image nearest
    where it started, so displacements and `relaxed.xyz` are as for `tblite`.
- **The Poreblazer comparison** (`poreblazer`) runs in `xtb_<n>/poreblazer_built` and
  `poreblazer_relaxed`, from the stage's two structure files: the same atoms, settings and
  random seed, so the difference is the relaxation's.
  - The two runs are the check's. They are not recorded as Poreblazer results of the run,
    so the run's surface area stays that of the cell as built.
  - A relaxation that stopped at its step cap gives a lower bound on the change.
  - **Run with the real programs** on `li_ion_carbon` as shipped (488 atoms, GFN1-xTB, 15
    steps, stopped at the cap with the largest force down from 1.64 to 0.59 eV/Å): the
    surface area went from 7,201.8 to 7,216.5 m²/g (+0.2%) and the pore limiting diameter
    from 14.47 to 14.48 Å. The built figures equal the recipe's own Poreblazer stage's.
- **Finding it:** `XTB_WORKER` names the command (for example an interpreter in another
  environment); the default is this interpreter, if it can import `tblite` (or, for
  `gfnff`, if there is an `xtb` binary: `XTB_EXE`, or `xtb` on the PATH). A recipe with
  the stage won't start without what its method needs.
- **Files:** `xtb_<n>/structure.xyz` and `structure.topology.json` (as the conduction stage
  writes them), `xtb.json`, `xtb.log`, and for `relax` the relaxed cell as `relaxed.xyz`,
  in the export's format with the same atom order and header, plus `relaxed_by`.
- **`relaxed.xyz` isn't wrapped.** An atom that relaxes through a face stays just outside
  [0, L), so the topology's bond images still hold for the relaxed positions.
- **Charge:** the sum of the atoms' charges, which must be within 0.01 of a whole number.
  A cell whose charges sum to anything but zero is refused unless `charge` is given: a
  charged periodic cell is a choice someone should make on purpose.
- **Failure:** a calculation that doesn't converge is recorded (`converged: false`, with
  the reason in `error`) and doesn't stop the recipe; if the single point converged and the
  relaxation didn't, the single point's figures are kept. A worker that can't run does stop it.

### `xtb.json` ("ambuild-xtb" version 1)

    {"format": "ambuild-xtb", "version": 1,
     "method": "GFN1-xTB", "program": "tblite", "program_version": "…",
     "structure_sha256": "…", "atoms": 472, "charge": 0, "mode": "relax",
     "converged": true, "error": null, "seconds": 218.1,
     "energy_eV": -20027.3736, "fmax_eV_A": 5.305, "frms_eV_A": 0.41, "gap_eV": 1.9,
     "worst_atoms": [[131, 5.305], …],
     "relax": {"steps": 15, "max_steps": 15, "fmax_threshold_eV_A": 0.05,
               "reached_fmax": false, "energy_eV": -20038.3997, "fmax_eV_A": 2.745,
               "frms_eV_A": 0.33, "rmsd_A": 0.154, "max_displacement_A": 0.61, "bonds": 540,
               "max_bond_change_A": 0.12, "worst_bonds": [[5, 131, 1.54, 1.42], …],
               "structure": "relaxed.xyz"}}

The values are illustrative. `worst_atoms` (`[atom, force]`) and `worst_bonds` (`[i, j, length before, length after]`),
the ten largest of each, are for the viewer, which marks them. GFN-FF has no orbitals, so
its `gap_eV` is null. With `--forces` the worker also writes every atom's force
(`forces_eV_A`), for comparing programs; the stage doesn't ask for it.

## Metrics

From the run's latest result:

| Metric | Meaning | Reads as |
| --- | --- | --- |
| `xtb_fmax` | the largest force on any atom at the built geometry (eV/Å) | high: a strained or unphysical bond, usually at a junction |
| `xtb_frms` | the RMS force (eV/Å) | high: the whole cell is far from this method's minimum |
| `xtb_energy_per_atom` | eV per atom | comparable only between cells of the same composition |
| `xtb_gap` | the HOMO–LUMO gap (eV), tight binding only | set beside liminal's Hückel `el_gap` |
| `xtb_relax_rmsd` | RMS displacement over the relaxation (Å) | how far the force field's geometry was from xTB's |
| `xtb_relax_max_bond_change` | the largest change in a bonded length, from the topology's bonds and their periodic images (Å) | |
| `xtb_relax_energy_drop` | energy released per atom (eV) | |
| `xtb_relax_reached_fmax` | 1 if it stopped below `fmax`, 0 at the step cap | |
| `xtb_d_surface_area` | relaxed minus built accessible surface area (m²/g), with `poreblazer` | the check on the headline result |
| `xtb_d_pld` | relaxed minus built pore limiting diameter (Å), with `poreblazer` | |

Metrics a result doesn't have are None. Sweeps can plot them and campaigns can aim at or
constrain them (`ambuild.campaign.METRICS`). A run's figures are those of its latest check
with figures, its own or a child run's. A capped relaxation's figures are lower bounds on
the drift, so `xtb_relax_reached_fmax` goes with them everywhere they're shown.

## Running it

- **In a recipe:** the stage runs in place, after the optimisation and Poreblazer stages
  it checks. Suits small cells and the single point.
- **On Slurm:** `submit_build.sh --xtb` adds a fan-out after the build
  (`ambuild_xtb_fanout.sbatch`): an array task per pickle checked, each a child run in
  `<run-id>/xtb_runs/step_N/` with `parent_run_id` set, then one upload. `deploy/README.md`
  lists its `AMBUILD_XTB_*` variables.
  - **Only the last pickle is checked by default** (`AMBUILD_XTB_STEPS=all` for every one):
    unlike Poreblazer, a check costs minutes, and hours to relax.
  - **Memory** is requested from the atom count, 150 MB + 550 MB × (atoms / 472)^1.85 with
    20% to spare. That curve bounds what was measured at 472 and 944 atoms and is
    extrapolated beyond, so it is the first thing to check on real cells.
  - **A task's CPUs** (`AMBUILD_XTB_CPUS`, default 4) are the worker's threads, and its
    memory is the stage's `memory_limit_mb`.
  - **`AMBUILD_XTB_POREBLAZER=1`** (with `AMBUILD_XTB_MODE=relax` and `POREBLAZER_EXE`)
    adds the Poreblazer comparison.
- **From the web GUI:** a Slurm agent with `AMBUILD_AGENT_XTB=1` submits every build with
  `--xtb`; the `AMBUILD_XTB_*` variables in its environment set the check. This is the
  `slurm` backend only: the `slurmrest` backend and sweeps submitted as one array job
  don't add the fan-out yet. A recipe's own `xtb` stage works with every backend.
- **The worker's environment** on a cluster: `deploy/slurm/xtb-worker/install.sh PREFIX`
  makes two conda environments on the shared filesystem (`tblite` and ASE; the `xtb`
  binary on its own, as beside `tblite-python` conda resolves it to a version that
  fails), checks the worker on a benzene ring with each method, and prints the
  `XTB_WORKER` and `XTB_EXE` lines for the jobs' environment. The worker's code is the
  clone's, read through `PYTHONPATH`, so it is always the version the builds run.
  `tests/docker/xtb.Dockerfile` is the same environment as an image, for the tests.

## Recording and review

- **Event:** `xtb_result`, with the figures above, the files, the directory and the exit
  code. `xtb.json` and `relaxed.xyz` are artifacts.
- **Ingest:** no table of its own. The uploader already stores every event, and the web
  GUI reads `xtb_result` events as it reads ion maps and conduction results, so there is
  no schema change. (The first draft of this spec gave it an `xtb_results` table.)
- **MLflow:** the `xtb_*` metrics of the run's latest check with figures. A fan-out's
  check is logged with its child run and on the build's MLflow run too, at the step of the
  checkpoint checked, so review sees the build with its check. `--mlflow-backfill` does
  the same for runs already in the database.
- **Web GUI:** an **xTB check** table on the run page, listing the run's checks and its
  child runs', each linking to its files, and a table of the pores before and after
  relaxing where Poreblazer was compared.
- **Viewer:** a frame with a check gets an **xTB check** row of controls.
  - The ten atoms under the largest forces are marked with translucent spheres, the
    larger the force, and the ten bonds that moved most with thick cylinders.
  - **Relaxed positions** swaps the frame's atoms for `relaxed.xyz` (`?xtb=relaxed` in
    the address does the same), so the two can be flicked between.
  - A check is shown on the frame of its step, the run's own or a child run's. Frames of
    runs recorded before the export format don't take checks, as their atoms aren't in
    the export's order.
  - `relaxed.xyz` is downloaded from the check's row; it is in the export's format, so
    `ambuild.export`'s converters read it.
- **Review:** two edge cases in `review_criteria.json`, both relative to the run's stratum
  (recipe × cell size, at least 8 checked runs): `strained_at_xtb` (`xtb_fmax` above 95% of
  the stratum's) and `moves_on_relaxing` (the largest bond change likewise). `xtb_fmax`
  also joins the outlier metrics. Nothing is gated.
  - **No force is called too large in advance,** as there are no recorded results to set a
    threshold from. The cost: every stratum with spread flags its most strained build, even
    if all of them are fine. Once a few hundred checks are recorded, add a floor (`min`)
    to the rule, and consider a gate.

## Tests

| # | Checks |
| --- | --- |
| 1 | `ambuild.xtb` with a fake worker (as `tests/fake_liminal.py`): the stage writes its directory, records the event and artifacts, and returns the summary; a wrong `format` or `version` is refused. |
| 2 | Recipe: a recipe with the stage won't start without a worker; a failed worker stops it; a non-converged result doesn't. |
| 3 | Charge: a fractional net charge is refused; a cell with ions is refused without `charge`. |
| 4 | `max_atoms`: a larger cell is refused before the worker starts. |
| 5 | Worker (CI's `xtb` job, `tests/docker/xtb.Dockerfile`): in a benzene ring in a 12 Å cell with one carbon pushed out by 0.2 Å, that carbon is the first of `worst_atoms`, and relaxation brings the forces under 0.05 eV/Å and the ring back to regular. A ring across the cell's corner gives the energy and forces of one in the middle. GFN-FF gives a single point through the `xtb` binary. |
| 6 | Worker: `relaxed.xyz` keeps the atom order, lattice and header keys, and validates against the topology. |
| 7 | Bond changes across the periodic boundary use the topology's `image`: a bond through a face reports its true length. |
| 8 | `li_ion_carbon` as shipped has a lower `xtb_fmax` than the same recipe without its optimisation stages (1.6 against 5.3 eV/Å, GFN1-xTB). |
| 9 | Slurm (`deploy/slurm/test`, with a stand-in worker): `--xtb` gives a child run per pickle with `parent_run_id`, its `xtb_result` event carrying the settings from the environment, the task's threads and the memory estimate, and its files uploaded. |
| 10 | Web GUI and MLflow (`services/web/tests`, `services/ingest/tests`): a run's summary takes its latest check, a child run's included; the run page lists its own and its child runs' checks with links to each one's files; the metrics are offered to sweeps and campaigns; the last check with figures reaches MLflow. |
| 11 | GFN-FF relaxation (CI's `xtb` job): a stretched ring lying across a cell face comes back regular, the cell is unchanged, and no atom is reported a cell away. A coord file in bohr or Å is read, and wrapped atoms are unwrapped. |
| 12 | The Poreblazer comparison, with stand-ins for both programs: both structures reach Poreblazer whole and inside the cell, with the settings of the recipe's last `poreblazer` stage; the change gives `xtb_d_surface_area` and `xtb_d_pld`; the run still has one Poreblazer result. It is refused without `relax` or without `POREBLAZER_EXE`. |
| 13 | Viewer and agent (`services/web/tests`, `services/agent/tests`): each frame's spec carries the checks of its step with their worst atoms, worst bonds and the relaxed file's address, a child run's included; the run page has the controls and the pores table; an agent with `AMBUILD_AGENT_XTB` submits with `--xtb`. |

## Milestones

| | Delivers | Done when |
| --- | --- | --- |
| X0 | The open questions: `tblite` GFN1 against CP2K on one cell; periodic GFN-FF optimisation; time and memory at 500, 1,000 and 2,000 atoms | ✅ `tblite` agrees with CP2K; GFN-FF optimisation works with the cell held; the figures are in `docs/benchmarks.md`. **Left open:** time and memory are measured to 944 atoms only, as 1,888 didn't fit in the test machine's 4 GB, so the memory estimate above that is extrapolated |
| X1 | `ambuild/xtb.py`, the worker, the recipe stage, single point | ✅ tests 1–5 pass (`tests/testXtb.py`) |
| X2 | `relax`, the bond figures, `relaxed.xyz`, the Poreblazer comparison | ✅ tests 6, 7, 8, 11 and 12 pass. The Poreblazer comparison's test uses stand-ins for both programs; it was also run once by hand with the real ones (below) |
| X3 | Slurm fan-out and the worker image | ✅ test 9 passes; `deploy/slurm/xtb-worker/install.sh` makes the worker's environment; the `slurm` agent submits with `--xtb`. **Left open:** the fan-out from the `slurmrest` agent and from sweeps' array jobs |
| X4 | Ingest, MLflow, the run page | ✅ tests 10 and 13 pass. The viewer's drawing code is checked by its inputs and markup, not in a browser |
| X5 | The review edge case, with thresholds from recorded results | ✅ `ambuild-review` reports `strained_at_xtb` and `moves_on_relaxing`, relative to each stratum. The absolute floor waits for recorded results |
