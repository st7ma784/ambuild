# Ambuild Roadmap

This is the working plan for making Ambuild portable, testable, and usable as a
service. Items are ordered so that deployment work rests on a reproducible
runtime rather than on the current developer machines.

## 1. Portability and packaging

- [ ] Replace `/opt/ambuild.git`, `/opt/paramsDir`, Dropbox paths, and absolute
  interpreter paths in `standard_inputs/` and `ambuild/`.
  - Partly done: example scripts read `AMBUILD_PARAMS_DIR`, `AMBUILD_BLOCKS_DIR`
    and `POREBLAZER_EXE` (defaults still `/opt/...`).
    `ab_util.py` still inserts the repo root into `sys.path` for legacy pickles.
- [ ] Resolve repository data with `pathlib` where data is part of the
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

- [ ] Define the first web API around a small set of jobs: submit a build,
  validate inputs, inspect status/logs, and retrieve generated artifacts.
- [ ] Keep the scientific engine separate from HTTP concerns so the same
  package remains usable from Python and the command line.
- [ ] Containerise the API and worker separately; keep long-running molecular
  builds out of the request process.
- [ ] Add a job queue, persistent artifact storage, resource limits, timeout/
  cancellation handling, and an explicit execution status model.
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
- [ ] Define how jobs request CPU, GPU, memory, node count, and MPI ranks, and
  map those requirements to Slurm partitions or Kubernetes node pools.

## 4. Poreblazer investigation

- [ ] Record the exact Poreblazer version, source revision, compiler flags, and
  input/output contract used by Ambuild.
  - Tested: v3.0.5, commit `a753c72` (2018-02-28), gfortran 14.2 with the
    upstream Makefile, which compiles with `-O2 -unshared` (its `OFLAGS`).
    Output contract: `ab_poreblazer.parse_output()`.
  - Each run writes a ~13 MB `nitrogen_network.grd` (20 Å cell); decide
    whether run storage keeps, compresses or drops it.
- [ ] Profile representative workloads before changing Fortran code. Measure
  wall time, CPU time, memory, trial counts, and scaling with atom count and
  number of pores.
- [ ] Identify hotspots in the Poreblazer source and classify them as serial,
  OpenMP-parallel, MPI-parallel, or suitable for accelerator work.
- [ ] Confirm whether the target Poreblazer version supports MPI or can be
  cleanly extended to do so, then benchmark strong and weak scaling on the
  available Slurm cluster.
- [ ] Try low-risk improvements first: compiler optimization flags, I/O
  reduction, better batching, OpenMP where independence is proven, and a
  modern Fortran compiler/runtime.
  - Compiler flags ruled out (`docs/benchmarks.md`): upstream `-O2` is the
    fastest build; `-O3 -march=native` is 3–14% slower and `-O0` ~1.8x
    slower, with identical results. Runtime grows ~5–10x per 10 Å of cell
    edge, so OpenMP over the grid or splitting the work is the lever.
- [ ] Compare a CPU-optimized build against any GPU prototype; retain a GPU
  path only if it improves the target workloads after data-transfer overhead.
- [ ] Wrap Poreblazer configuration and executable discovery so it is not tied
  to `/opt/poreblazer/src/poreblazer.exe`.
  - Partly done: callers pass the executable to `Cell.poreblazer(exe)` and the
    scripts and tests read `POREBLAZER_EXE`. `defaults.dat` and `UFF.atoms`
    are still constants in `ab_poreblazer.py`.
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
- [ ] Fix `Cell.writeCar()`, which indexes `CellData` like a dict.
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
  - Not yet recorded: a random seed (meaningless until builds are
    reproducible, below) and requested resources (for the service to add).
- [ ] Make builds reproducible from a seed: `Cell` keeps blocks and end groups
  in sets ordered by memory address, so the same seeds give different
  structures. Needed before a recorded seed means anything.
- [ ] Emit `step` events for `joinBlocks` and `zipBlocks`, which write no CSV
  row today, and record `fragment_types` as a dict rather than a
  `defaultdict` repr (in the JSON sink; the CSV format stays as it is).
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
  or an existing S3, uploader CronJob), replacing `deploy/k8s/`.
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
- [ ] Retention for object storage and the `.ambuild-uploaded` markers;
  decide whether Poreblazer grids are ever kept.

## 8. Scaling many runs

Queued builds from a web interface are a throughput problem: most builds are
small, so scale across runs and checkpoints before scaling one build over MPI.
In order:

- [ ] Recipes: a declarative build description (e.g. seed 10, then 20 x (grow
  5, zip, optimise)) that the web API accepts instead of Python scripts, and a
  runner that executes it step by step with a checkpoint after each step.
- [ ] Checkpoint/resume: the runner resumes a recipe from its last checkpoint
  as a child run; on SIGTERM it dumps and exits so Slurm `--requeue` (and
  preemptible partitions) resume rather than restart.
- [ ] Stage-chained Slurm submission: split long recipes into jobs of N steps
  or a time budget, each resuming from the previous stage's checkpoint, so
  jobs stay short and stages can target CPU or GPU partitions.
- [ ] Checkpoint cache: key each stage's checkpoint by a hash of the input
  sha256s, parameters, Ambuild version and recipe prefix; before running,
  start from the longest matching cached prefix. Builds are stochastic, so a
  hit is a previous sample, not the same answer: ensembles and sweeps opt out
  or include the replicate in the key until builds are reproducible (§6).
- [ ] Sweeps and ensembles: one recipe over many seeds or parameters as an
  array job, each run tagged with a `sweep_id` in `run.json` and the database.
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