# Ambuild Roadmap

This is the working plan for making Ambuild portable, testable, and usable as a
service. Items are ordered so that deployment work rests on a reproducible
runtime rather than on the current developer machines.

## 1. Portability and packaging

- [x] Replace `/opt/ambuild.git`, `/opt/paramsDir`, Dropbox paths, and absolute
  interpreter paths in `standard_inputs/` and `ambuild/`.
  - The scripts call `ab_util.paramsDir()`, `blocksDir()` and `poreblazerExe()`:
    `AMBUILD_PARAMS_DIR`, `AMBUILD_BLOCKS_DIR` and `POREBLAZER_EXE` when set, else
    the checkout's `tests/params` and `tests/blocks` and Poreblazer on the `PATH`,
    else an error naming the variable. `ab_util.py` still inserts the repo root
    into `sys.path` for legacy pickles.
- [x] Resolve repository data with `pathlib` where data is part of the
  checkout; pass user data, parameter directories, and external executables as
  command-line arguments or environment variables.
- [x] Stop modifying `sys.path` in example scripts; make the package importable
  through installation.
- [x] Add a modern `pyproject.toml`, supported Python versions, and pinned test
  dependency metadata. Keep `setup.py` only if compatibility requires it.
- [ ] Document optional dependencies separately: NumPy, HOOMD-Blue, and
  Poreblazer should not be required for the basic package or CPU test suite.
  - After the HOOMD 7 port (§5): review each dependency and image. NumPy is the
    only hard dependency; HOOMD-blue comes from conda-forge only (not PyPI;
    the broken `hoomd` pip extra is removed); Poreblazer is compiled from
    source. The 2020 glotzerlab images (7.5–13 GB) are replaced by
    `tests/docker/hoomd7.Dockerfile` (~580 MB: micromamba environment copied
    into debian-slim).
- [ ] Rewrite the README as a proper summary and how-to: what Ambuild is and
  what it builds; installing it (pip, the runtime image, HOOMD-blue and
  Poreblazer); a first build as a script and as a recipe; recording runs and
  reproducing them from a seed; Poreblazer analysis; the web GUI (demo, New
  run, queue, agents) and running on Slurm; where the detailed docs are
  (docs/, deploy/README.md). It has grown section by section and now reads as a
  changelog of features rather than a guide.

## 2. Reproducible tests and CI/CD

- [x] Replace the manual/basic GitHub Actions workflow with a pull-request and
  push workflow using current `checkout` and `setup-python` actions.
- [x] Install Ambuild with `pip install .` in CI instead of relying on the
  checkout being on `PYTHONPATH`.
- [x] Run the CPU/unit test suite on a small Python version matrix.
  - `testCell.testWriteCml` and `testCatalysis.testUnbonding` are marked
    `expectedFailure` (stale reference CML; unimplemented `catalyst=` API).
- [x] Add a root `Dockerfile` only when the image is the supported reproducible
  runtime; pin its Python, HOOMD/CUDA, and system dependencies.
  - Root `Dockerfile`: Python 3.12, HOOMD-blue 7.2.0 (CPU or GPU) and Poreblazer
    at a pinned commit, ~590 MB; CI runs the full suite inside it.
- [ ] Publish the runtime image (e.g. GHCR, with the `ambuild-ingest` image, §7)
  on version tags, and offer it to Slurm users through Apptainer/Singularity.
- [ ] Make randomised tests deterministic individually (seed per test or
  inject an RNG) rather than relying on the suite-wide seed in `run_tests.py`.
- [x] Move HOOMD integration tests into a separate, explicitly optional job.
  Validate whether a self-hosted GPU runner is required before making this a
  required check.
  - `test-hoomd7.yml` runs the full suite, with the HOOMD parity test, on
    HOOMD-blue 7 (CPU). The multi-rank MPI test went with HOOMD 2: there is no
    MPI build of HOOMD-blue 4+ to run it on (§7). A GPU runner has not been
    evaluated.
- [ ] Add release automation to build sdist/wheels and publish on version tags.
- [ ] Add dependency and container scanning, test artifacts, and a documented
  release/versioning policy.

## 3. Service suitable for Rancher

The web GUI and its API are planned in [docs/web-gui.md](docs/web-gui.md): milestones
0–10, from the status page and run browser to submission through a Slurm agent, sweeps,
goal-directed campaigns (Bayesian search for structures meeting a target), an
agent skill so AI assistants can run and discuss experiments,
the checkpoint cache, a Fleet-deployed Helm chart and, last, accounts. The items below
are covered there.

- [ ] Define the first web API around a small set of jobs: submit a build,
  validate inputs, inspect status/logs, and retrieve generated artifacts.
- [ ] Keep the scientific engine separate from HTTP concerns so the same
  package remains usable from Python and the command line.
- [x] Containerise the API and worker separately; keep long-running molecular
  builds out of the request process.
  - `ambuild-web` and the `ambuild-agent` image (Dockerfile target), which runs
    submissions in its own container or pod.
- [ ] Add a job queue, persistent artifact storage, resource limits, timeout/
  cancellation handling, and an explicit execution status model.
  - Done in web GUI milestone 3: the queue (`submissions`, claimed by agents as
    leases), content-addressed inputs in object storage, cancelling, and the
    submission states. Milestone 4: per-run CPUs, GPUs, memory and time limits on
    Slurm, from the recipe's "resources" (none yet for the local backend).
- [ ] Provide health/readiness endpoints, structured logs, metrics, and an
  authentication/authorization boundary before exposing the service.
- [ ] Deploy first as a Rancher-managed Kubernetes workload with CPU workers.
  Add GPU worker pools only for workloads proven to benefit from them.
- [ ] If the Rancher-managed cluster exposes Slurm and MPI, design a separate
  distributed worker path for workloads that scale across nodes; do not assume
  that Kubernetes placement alone provides MPI semantics.
  - Slurm path in place (`deploy/slurm`, §7): builds, uploads and Poreblazer
    arrays as Slurm jobs, and HOOMD across a job's MPI tasks. Still to decide:
    how the web API hands jobs to Slurm (§8 dispatcher).
  - Web GUI milestone 4: the Slurm agent on a login node claims queued recipes and
    submits them with `submit_build.sh --recipe`, one build and upload job each;
    packing many small builds per job is still the dispatcher's job (§8).
- [ ] Define how jobs request CPU, GPU, memory, node count, and MPI ranks, and
  map those requirements to Slurm partitions or Kubernetes node pools.

## 4. Poreblazer investigation

- [x] Record the exact Poreblazer version, source revision, compiler flags, and
  input/output contract used by Ambuild.
  - Tested: v3.0.5, commit `a753c72` (2018-02-28), gfortran 14.2 with the
    upstream Makefile, which compiles with `-O2 -unshared` (its `OFLAGS`).
    Output contract: `ab_poreblazer.parse_output()`.
  - Each run wrote a ~13 MB `nitrogen_network.grd` (20 Å cell). Resolved:
    Poreblazer's visualisation output now defaults to none; ask for it with
    `Cell.poreblazer(exe, visualisation="grd")`.
- [x] Profile representative workloads before changing Fortran code. Measure
  wall time, CPU time, memory, trial counts, and scaling with atom count and
  number of pores.
  - `benchmarks/profile_poreblazer.py`, `docs/benchmarks.md` ("where the time
    goes"): the lattice step (cubes × atoms) and the pore size distribution take
    ~90%; memory grows with grid volume (549 MB at 40 Å).
- [x] Identify hotspots in the Poreblazer source and classify them as serial,
  OpenMP-parallel, MPI-parallel, or suitable for accelerator work.
  - Both hotspots are independent per grid cube or per sample (OpenMP), with
    algorithmic gains available (cell lists; a spatial index for the PSD).
    Table in `docs/benchmarks.md`.
- [ ] Confirm whether the target Poreblazer version supports MPI or can be
  cleanly extended to do so, then benchmark strong and weak scaling on the
  available Slurm cluster.
  - No MPI support. OpenMP and the algorithmic changes come first; many cells
    already run in parallel as Slurm array tasks.
- [ ] Try low-risk improvements first: compiler optimization flags, I/O
  reduction, better batching, OpenMP where independence is proven, and a
  modern Fortran compiler/runtime.
  - Compiler flags ruled out (`docs/benchmarks.md`): upstream `-O2` is the
    fastest build; `-O3 -march=native` is 3–14% slower and `-O0` ~1.8x
    slower, with identical results. `-flto` gives nothing, and enabling the
    existing OpenMP directives is slower (147 s vs 81 s on 4 threads) and racy.
  - Rerun the flags comparison on scc-hdd-02 (older Westmere CPU, no AVX) once
    that host's storage fault is fixed.
- [x] Fork Poreblazer and parallelise its two hotspots properly: private loop
  temporaries and per-thread cube lists in `lattice_calculations`, per-thread
  random streams for the 10,000 PSD samples; check results against
  `tests/test_data/poreblazer` and the profile cells.
  - [st7ma784/poreblazer](https://github.com/st7ma784/poreblazer) `ambuild`
    branch: output identical to upstream at every thread count; 3.1–5.4x faster
    on 8 threads for the 30–40 Å cells (`docs/benchmarks.md`). The images build
    it; `Cell.poreblazer(exe, threads=N)` and the Slurm tasks set the threads.
  - [x] Parallelise the nitrogen lattice's percolation analysis, then the floor
    for small cells (~2.5 s at 30 Å). Measured: the percolation analysis itself
    takes 0.1–0.4 s; the rest was writing `nitrogen_network.grd`, which Ambuild
    no longer asks for by default. The grid is now written a plane at a time
    (~30% faster), the percolation relabelling uses a lookup table, and the PSD
    sort runs in parallel. The labelling itself stays serial (see below).
  - [x] Decide whether the fork fixes upstream's cluster labelling, which splits
    connected clusters (54 of 60 random lattices). Decision: opt-in, default
    unchanged. `percolation_labelling="exact"` (fork `24d884e`); the setting and the
    labelling the executable reports are returned with every result.
  - [ ] Parallelise the exact labelling (slab-wise union-find, then merge across
    slab boundaries; the numbering stays thread-independent).
  - [ ] Decide when, if ever, exact labelling becomes Ambuild's default, and offer
    the defect and fix upstream.
  - [ ] Offer the race fix upstream (richardjgowers/poreblazer is unchanged since
    2018, so it may not be picked up).
  - [x] Test the fork in CI against upstream and publish its image (fork
    `.github/workflows/ci.yml`, `ghcr.io/st7ma784/poreblazer`); Ambuild's images copy
    the executable from a pinned `sha-` tag.
  - [ ] Let the Update Poreblazer workflow (run by hand; nothing is scheduled) open
    pull requests: repository setting "Allow GitHub Actions to create and approve
    pull requests".
  - [x] Extend the cell list and the PSD's distance pruning to non-orthorhombic cells
    (fork `d70fa08`: hexagonal MOF-180 52 s to 10 s on 1 thread, 45 s to 3.5 s on 4;
    upstream 59 s), still byte-identical to upstream.
- [x] Algorithmic changes in the same fork: a cell list so each grid cube checks
  only atoms within the cutoff (`3ce6695`: lattice step 3.4x faster at 40 Å,
  output bit-identical to upstream in 24 of 24 runs, `docs/benchmarks.md`).
  - [x] A spatial index for the PSD search (round 4, fork `ac451fb`: block search,
    29 s to 1.5 s on the 50 Å cell), with a one-pass spanning test, a parallel
    surface area and vectorised lattice distances; all bit-identical.
  - [ ] An expanding search in the cell list for cubes in pores wider than twice
    the cutoff, which now check every atom (costly only for large, dense-walled
    cells with very large pores).
  - [x] Memory: about 67 bytes per grid cube (1 GB at 50 Å, ~8 GB at 100 Å). Now
    about 34 (fork `8ed0c70`), with `ab_poreblazer.memory_estimate_mb()` sizing
    Slurm requests and refusing cells that would not fit.
  - [ ] Recompute the helium Lennard-Jones energy for the cubes that percolate
    instead of storing it for every cube (8 more bytes per cube, for some lattice
    time); worth it for cells of ~100 Å or more.
- [ ] Let recipes and the dispatcher (§8) choose `cubelet_size` (default stays
  0.2 Å) and Poreblazer's threads; memory requests can come from
  `ab_poreblazer.memory_estimate_mb()`.
- [ ] Compare a CPU-optimized build against any GPU prototype; retain a GPU
  path only if it improves the target workloads after data-transfer overhead.
- [x] Wrap Poreblazer configuration and executable discovery so it is not tied
  to `/opt/poreblazer/src/poreblazer.exe`.
  - Callers pass the executable to `Cell.poreblazer(exe)`, the scripts, tests
    and runtime image use `POREBLAZER_EXE`, and `Cell.poreblazer(exe,
    **settings)` sets every `defaults.dat` value (`ab_poreblazer.DEFAULT_SETTINGS`).
    `UFF.atoms` is still a constant.
- [x] Add a small deterministic benchmark/regression fixture before adopting
  performance changes (`tests/test_data/poreblazer`,
  `benchmarks/bench_poreblazer.py`).

## 5. Engine streamlining

- [x] Give each run an explicit output context (run directory, run id) instead
  of writing `ambuild.csv`, `step_N.pkl` and `poreblazer_N/` into the cwd.
  The CSV handle in `ab_analyse.Analyse` is never closed, and
  `Cell.poreblazer()` uses `os.chdir`, so two runs cannot share a process.
- [ ] Route logging per cell (a `logging.LoggerAdapter` or per-run handler)
  instead of reconfiguring the root logger in `Cell.setupLogging()`.
- [ ] Make the catalysis tests pass under HOOMD: `testCatalysis.testCat1Paf2`
  and `testCat2Paf2` need the missing `hc-cp-cp` angle parameter for some
  layouts, and `testCell.testCat2Paf2` fails with "Error computing cell list"
  for others. All three are skipped.
- [x] Fix `Cell.writeCar()`, which indexed `CellData` like a dict: it writes the
  blocks' atoms with their labels, types, symbols and charges (or a `CellData`'s),
  and honours `skipDummy`.
- [x] In-process HOOMD-blue 2 parses the build script's command line in
  `hoomd.context.initialize()`, so scripts that take their own arguments fail
  ("no such option"). Resolved by dropping HOOMD 2; HOOMD-blue 4+ does not.
- [x] Parse Poreblazer output (surface area, pore volume, pore limiting and
  largest cavity diameters, pore size distribution) into a result dict.
  Today Ambuild runs it and leaves the files unread.
- [x] Pass `directory=` to `ab_util.run_command` rather than calling
  `os.chdir` in `Cell.poreblazer()`.
- [x] Port the MD engine to HOOMD-blue 7 (conda-forge 7.2.0; HOOMD 2 is
  end-of-life and pinned to a 2020 container). In order:
  - [x] Define the MD engine interface the rest of Ambuild uses:
    `optimiseGeometry`, `runMD`, `snapshotResult`, `updateCell` (the
    launcher's `applyResult` already works from plain arrays), and drop
    `hoomd1.py` (`ambuild/ab_mdengine.py`).
  - [x] `hoomd4.py` (HOOMD-blue 4.0 and later) behind that interface: `hoomd.Simulation` from a
    snapshot, `md.constrain.Rigid` for rigid bodies, `md.minimize.FIRE`,
    `md.methods.ConstantVolume`/`ConstantPressure` with thermostats, LJ pairs,
    harmonic bonds and angles, periodic dihedrals, charges, walls, and logging
    through `hoomd.logging` writers. Select it by HOOMD version, as today.
  - [x] Parity tests: the same cell gives the same static energy under HOOMD 2
    and 7, and the HOOMD suite passes on both (`testHoomdParity`: within 3e-6).
  - [x] Container and CI job with HOOMD 7 from conda-forge (micromamba)
    (`tests/docker/hoomd7.Dockerfile`, `test-hoomd7.yml`).
  - [ ] Re-test rigid bodies under MPI, which HOOMD 2 cannot decompose (§7).
    conda-forge publishes no MPI build of HOOMD-blue, so this needs HOOMD-blue
    built from source (or Spack); decide whether MPI is worth it first
    (`docs/benchmarks.md`).
  - [x] Then remove `hoomd2.py`, the 2020 images and `test-hoomd2.yml`.
    HOOMD 3+ no longer parses the command line, which also fixes the in-process
    argument problem below.
- [ ] Split `ab_cell.py` (~3,100 lines) along existing seams: building
  (seed/grow/join/zip), simulation adapters, I/O (`write*`, pickles), analysis.
- [ ] Add a versioned, non-pickle serialisation of a cell (JSON or HDF5) for
  stored results. Keep pickles only as trusted, same-version restart files.
- [ ] Profile the per-call `cellData()` -> snapshot -> `updateCell()` round
  trip before optimising it.
  - A one-step in-process calculation costs 0.5–1.1 s for 72–4,608 atoms
    (`docs/benchmarks.md`), mostly this round trip and context set-up.

## 6. Run recording and results database

- [x] Turn `Analyse.stop()`, which every build step already goes through, into
  an event emitter with pluggable sinks: step completed, artifact written
  (`dump`, `write*`), Poreblazer result, run finished or failed.
- [x] Keep the current CSV as the default sink (`ab_analyse.CsvSink`); add a
  JSON Lines sink that appends `events.jsonl` in the run directory
  (`ab_run.JsonlSink`).
- [x] Record provenance in `run.json`: Ambuild version and git SHA, input
  script, parameter files, building blocks with hashes, Python/NumPy/HOOMD
  versions (`Cell(recordRun=True)`, `ambuild/ab_run.py`). Poreblazer's version
  is in each `pore_result` event.
  - The seed and the random number generator's state are recorded
    (`run.json` `"random"`, below). Not yet recorded: requested resources (for the
    service to add).
- [x] Make builds reproducible from a seed: `Cell` keeps blocks and end groups
  in sets ordered by memory address, so the same seeds give different
  structures. Needed before a recorded seed means anything.
  - Block ids are serial numbers given by the cell (`Cell.addBlock`); random choices
    from sets of blocks or type names choose from a sorted list; a split block keeps
    its fragment order. `Cell(seed=...)` seeds the generator; a recorded run saves its
    state and `Cell(randomState=<run directory>)` replays it. `tests/testReproducible.py`
    builds in processes with different hash salts and memory layouts, with a block
    split and a HOOMD-blue optimisation, and requires identical structures.
- [x] Emit `step` events for `joinBlocks` and `zipBlocks` (`join` and `zip`, on
  every return path), and record `fragment_types` as a dict in step events (the
  CSV keeps its `defaultdict` repr; the ingest service stores the dict as JSON).
- [x] Resume recording when a cell is restored from a pickle, as a new run
  that links to its parent run id (`Cell.startRecording()`).
- [ ] Decide what `run.json` exposes through the API: it records the host name
  and absolute input paths.
- [x] Make the engine never talk to the network. The service worker (or a
  Slurm epilogue) uploads the run directory and events after, or alongside,
  the job. Offline HPC nodes then behave the same as Kubernetes workers.
- [x] Schema (PostgreSQL): `runs`, `events`, `steps`, `files` (object URI,
  sha256, kind, step), `pore_results` (`services/ingest/.../schema.sql`).
  - Still to add: an owner column once the API has authentication, and
    schema migrations (the schema is created with `IF NOT EXISTS` only).
- [x] Store structure files and pickles in object storage (S3/MinIO), not in
  the database; the database holds URIs and checksums.
- [x] Make ingestion idempotent: client-generated run UUID, unique
  `(run_id, step)`, so retried uploads cannot duplicate rows.
- [ ] Never unpickle user-uploaded files in the API; accept inputs as scripts,
  `.car` files and parameters only.

## 7. Deployment follow-ups

- [ ] Helm chart mirroring `deploy/docker-compose.yml` (PostgreSQL, SeaweedFS
  or an existing S3, the on-demand upload scan Job), replacing `deploy/k8s/`.
- [ ] Publish the `ambuild-ingest` image (e.g. GHCR) on version tags; the K3s
  manifests reference `ghcr.io/st7ma784/ambuild-ingest:0.1.0`, which is not
  built yet.
- [x] Run HOOMD with several MPI ranks under `srun`: Ambuild stays one process
  and launches each HOOMD calculation across the job's tasks
  (`ab_hoomdlauncher`, `AMBUILD_HOOMD_LAUNCHER`).
  - [x] Benchmark (`docs/benchmarks.md`): a worker costs 0.8–1.6 s per
    calculation; MPI divides MD well (5x on 8 ranks at 4,600 atoms) but only
    wins overall above ~4,000 atoms for 2000-step runs. Keep in-process as the
    default.
  - [ ] Check `srun` + MPI HOOMD on the real cluster (PMI type, GPUs per task);
    tested here with `mpirun` on 1, 2 and 4 ranks in the glotzerlab OpenMPI
    image (identical static energies) and with `srun` launching plain
    processes in the Slurm test cluster.
  - [ ] Rigid bodies under MPI: HOOMD-blue 2 domain decomposition failed for
    Ambuild's bonded rigid bodies ("Error during communication", "Error in
    bond calculation"), so rigid-body calculations, Ambuild's default, run on
    one process. Untested with the HOOMD 4+ engine.
  - [ ] If MPI is wanted: build an MPI image of HOOMD-blue 4+ from source (or
    Spack) for clusters and for the launcher's multi-rank CI test, which was
    removed with HOOMD 2. conda-forge publishes no MPI build.
- [ ] Try the Slurm scripts on the real cluster: partitions, GPU `--gres`,
  module loads and the shared filesystem path.
- [ ] Retention for object storage and the `.ambuild-uploaded` markers.
  (Poreblazer no longer writes its grid unless asked.)

## 8. Scaling many runs

Queued builds from a web interface are a throughput problem: most builds are
small, so scale across runs and checkpoints before scaling one build over MPI.
In order:

- [x] Recipes: a declarative build description (e.g. seed 10, then 20 x (grow
  5, zip, optimise)) that the web API accepts instead of Python scripts, and a
  runner that executes it step by step with a checkpoint after each step.
  - `ambuild/recipe.py` (`python -m ambuild.recipe run|validate|describe|hash`):
    checkpoints after each top-level stage and each pass of a top-level repeat;
    the same recipe and seed give the same structure from the command line or
    the web GUI (checked end to end in CI).
- [ ] Checkpoint/resume: the runner resumes a recipe from its last checkpoint
  as a child run; on SIGTERM it dumps and exits so Slurm `--requeue` (and
  preemptible partitions) resume rather than restart.
  - Done: pickles save the random number generator's state and
    `ab_util.cellFromPickle()` restores it, so a build resumed from a checkpoint
    ends as the uninterrupted build would (`tests/testReproducible.py`).
- [ ] Stage-chained Slurm submission: split long recipes into jobs of N steps
  or a time budget, each resuming from the previous stage's checkpoint, so
  jobs stay short and stages can target CPU or GPU partitions.
- [ ] Checkpoint cache: key each stage's checkpoint by a hash of the input
  sha256s, parameters, Ambuild version and recipe prefix; before running,
  start from the longest matching cached prefix. Builds are stochastic, so a
  hit is a previous sample, not the same answer: ensembles and sweeps opt out
  or include the replicate in the key until builds are reproducible (§6).
- [x] Goal-directed campaigns (web GUI milestone 6): search recipe parameters
  with Bayesian optimisation (Optuna TPE or GP, ask-and-tell, replicates per
  point for noise) for structures meeting constraints on their results, e.g. a
  pore limiting diameter above an ion's size, each round queued on Slurm.
  Showcase: the densest `li_ion_carbon` network (benzene + alkyne linkers)
  whose pores still admit Li+ (PLD >= 1.52 A, percolating).
- [x] AI assistants working with Ambuild (web GUI milestone 7): an agent skill,
  `.claude/skills/ambuild/SKILL.md`, and its API helper, rather than a hosted chat
  page. Claude Code (or any agent, e.g. Jev) can search and explain runs and
  queue runs, sweeps and campaign rounds after previewing them with the user.
- [x] Force-field parameters for sp (alkyne) carbon, type `c1`: a complete GAFF
  1.81 set for `ca`, `ha` and `c1` (`ambuild/recipes/params/gaff_benzene_alkyne`,
  generated by `scripts/gaff_params.py` from AmberTools' gaff-1.81.dat), used by
  `li_ion_carbon`, whose optimised junctions are within 0.006 A and 2.3 degrees
  of GAFF's geometry (`tests/testLiIonCarbon.py`).
- [ ] The older test blocks `tests/blocks/benzene*.car` have ring bonds of
  about 1.53 A (a C-C single bond, not benzene's 1.397 A): check them, or use
  `benzene_135`, before relying on them for accurate structures.
- [ ] Typed bond lengths for biaryl links: GAFF types ring carbons that join
  rings as `cp`/`cq` (GAFF 1.81: cp-cp 1.4854 A for the link); blocks typed that way would let
  `typed_bond_lengths` place benzene-benzene networks accurately too.
- [x] Sweeps and ensembles: one recipe over many seeds or parameters as an
  array job, each run tagged with a `sweep_id` in `run.json` and the database.
  - Web GUI milestone 5 (`ambuild/sweep.py`): grids of JSON-pointer parameters,
    CSV rows and seed lists; a Slurm agent submits a sweep's runs as one array
    job (`deploy/slurm/submit_array.sh`). Runs are linked to their sweep in the
    database (`submissions.sweep_id`), not in `run.json`.
- [ ] Dispatcher behind the web API: turns queued recipes into jobs, packing
  small builds several per node (`srun --multi-prog` or a task-farm worker)
  and sharing GPUs between small HOOMD runs (MPS).
- [ ] Use the MPI HOOMD launcher only for large cells: all-atom MD on ~5,000
  atoms or more, or long runs on ~2,000 or more (`docs/benchmarks.md`). The
  dispatcher could set `AMBUILD_HOOMD_LAUNCHER` per job from the recipe.

## GPU clarification

The current Docker/NVIDIA setup is primarily explained by HOOMD-Blue. Ambuild
uses HOOMD for molecular-dynamics and geometry-optimisation operations, and
HOOMD can use CUDA-enabled GPU execution when the installed HOOMD build,
container runtime, drivers, and hardware support it. A Rancher deployment
should therefore model CPU Poreblazer workers and GPU HOOMD workers as
different capabilities until measurements show otherwise. MPI-capable workers
should be modelled as a third capability, backed by Slurm when available.