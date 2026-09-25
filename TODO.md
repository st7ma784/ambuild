# Ambuild Roadmap

This is the working plan for making Ambuild portable, testable, and usable as a
service. Items are ordered so that deployment work rests on a reproducible
runtime rather than on the current developer machines.

## 1. Portability and packaging

- [ ] Replace `/opt/ambuild.git`, `/opt/paramsDir`, Dropbox paths, and absolute
  interpreter paths in `standard_inputs/` and `ambuild/`.
  - Partly done: example scripts read `AMBUILD_PARAMS_DIR`, `AMBUILD_BLOCKS_DIR`
    and `POREBLAZER_EXE` (defaults still `/opt/...`); `hoomd2.py` shebang fixed.
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

## 2. Reproducible tests and CI/CD

- [x] Replace the manual/basic GitHub Actions workflow with a pull-request and
  push workflow using current `checkout` and `setup-python` actions.
- [x] Install Ambuild with `pip install .` in CI instead of relying on the
  checkout being on `PYTHONPATH`.
- [x] Run the CPU/unit test suite on a small Python version matrix.
  - `testCell.testWriteCml` and `testCatalysis.testUnbonding` are marked
    `expectedFailure` (stale reference CML; unimplemented `catalyst=` API).
- [ ] Add a root `Dockerfile` only when the image is the supported reproducible
  runtime; pin its Python, HOOMD/CUDA, and system dependencies.
- [ ] Make randomised tests deterministic individually (seed per test or
  inject an RNG) rather than relying on the suite-wide seed in `run_tests.py`.
- [ ] Move HOOMD integration tests into a separate, explicitly optional job.
  Validate whether a self-hosted GPU runner is required before making this a
  required check.
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
- [ ] Define how jobs request CPU, GPU, memory, node count, and MPI ranks, and
  map those requirements to Slurm partitions or Kubernetes node pools.

## 4. Poreblazer investigation

- [ ] Record the exact Poreblazer version, source revision, compiler flags, and
  input/output contract used by Ambuild.
  - Tested: v3.0.5, commit `a753c72` (2018-02-28), gfortran 14.2 with the
    upstream Makefile, which sets no optimisation flags (`-O0`). Output
    contract: `ab_poreblazer.parse_output()`.
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
- [ ] Compare a CPU-optimized build against any GPU prototype; retain a GPU
  path only if it improves the target workloads after data-transfer overhead.
- [ ] Wrap Poreblazer configuration and executable discovery so it is not tied
  to `/opt/poreblazer/src/poreblazer.exe`.
- [ ] Add a small deterministic benchmark/regression fixture before adopting
  performance changes.

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
- [x] Parse Poreblazer output (surface area, pore volume, pore limiting and
  largest cavity diameters, pore size distribution) into a result dict.
  Today Ambuild runs it and leaves the files unread.
- [x] Pass `directory=` to `ab_util.run_command` rather than calling
  `os.chdir` in `Cell.poreblazer()`.
- [ ] Define an MD engine interface (`optimiseGeometry`, `runMD`,
  `fragMaxEnergy`, `updateCell`) and drop `hoomd1.py`. That makes a HOOMD 4
  (or OpenMM) backend an addition rather than a rewrite; HOOMD 2 is end-of-life
  and pinned to a 2020 container.
- [ ] Split `ab_cell.py` (~3,100 lines) along existing seams: building
  (seed/grow/join/zip), simulation adapters, I/O (`write*`, pickles), analysis.
- [ ] Add a versioned, non-pickle serialisation of a cell (JSON or HDF5) for
  stored results. Keep pickles only as trusted, same-version restart files.
- [ ] Profile the per-call `cellData()` -> snapshot -> `updateCell()` round
  trip before optimising it.

## 6. Run recording and results database

- [x] Turn `Analyse.stop()`, which every build step already goes through, into
  an event emitter with pluggable sinks: step completed, artifact written
  (`dump`, `write*`), Poreblazer result, run finished or failed.
- [ ] Keep the current CSV as the default sink (done: `ab_analyse.CsvSink`);
  add a JSON Lines sink that
  appends `run.json` + `events.jsonl` in the run directory.
- [ ] Make the engine never talk to the network. The service worker (or a
  Slurm epilogue) uploads the run directory and events after, or alongside,
  the job. Offline HPC nodes then behave the same as Kubernetes workers.
- [ ] Emit `step` events for `joinBlocks` and `zipBlocks`, which write no CSV
  row today, and record `fragment_types` as a dict rather than a
  `defaultdict` repr (in the JSON sink; the CSV format stays as it is).
- [ ] Make builds reproducible from a seed: `Cell` keeps blocks and end groups
  in sets ordered by memory address, so the same seeds give different
  structures. Needed before a recorded seed means anything.
- [ ] Record provenance in `run.json`: Ambuild version and git SHA, input
  script, parameter-directory hash, input block hashes, HOOMD and Poreblazer
  versions, random seed, requested resources.
- [ ] Schema (PostgreSQL): `runs` (uuid, owner, status, provenance, timings),
  `steps` (run_id, step, type, density, energy, counts, JSONB extras),
  `artifacts` (run_id, step, kind, object-store URI, sha256, size),
  `pore_results` (run_id, step, scalar metrics, PSD as JSONB).
- [ ] Store structure files and pickles in object storage (S3/MinIO), not in
  the database; the database holds URIs and checksums.
- [ ] Make ingestion idempotent: client-generated run UUID, unique
  `(run_id, step)`, so retried uploads cannot duplicate rows.
- [ ] Never unpickle user-uploaded files in the API; accept inputs as scripts,
  `.car` files and parameters only.

## GPU clarification

The current Docker/NVIDIA setup is primarily explained by HOOMD-Blue. Ambuild
uses HOOMD for molecular-dynamics and geometry-optimisation operations, and
HOOMD can use CUDA-enabled GPU execution when the installed HOOMD build,
container runtime, drivers, and hardware support it. A Rancher deployment
should therefore model CPU Poreblazer workers and GPU HOOMD workers as
different capabilities until measurements show otherwise. MPI-capable workers
should be modelled as a third capability, backed by Slurm when available.