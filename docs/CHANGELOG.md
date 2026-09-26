# Changelog

All notable changes to Ambuild are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

The roadmap in [`../TODO.md`](../TODO.md) lists planned work; the increments
being delivered from it are described in [architecture.md](architecture.md).
Entries move from *Unreleased* into a version section when it is tagged.

## [Unreleased]

Everything since 2.0.1: run output directories, Poreblazer results, build
events and run recording, uploads to PostgreSQL and object storage from
Slurm or K3s, HOOMD-blue across MPI tasks, benchmarks, and HOOMD-blue 4+ (see
[architecture.md § Delivery plan](architecture.md#delivery-plan)).

### Added
- Ambuild's Poreblazer fork, [st7ma784/poreblazer](https://github.com/st7ma784/poreblazer)
  (`ambuild` branch): upstream 3.0.5 with its OpenMP races fixed and both
  hotspots parallelised, built with `-fopenmp`. Output is identical to upstream
  at every thread count; dense and larger cells run 3–5x faster on 8 threads.
  The runtime, Poreblazer test and Slurm test images build it.
  Commit `3ce6695` adds a cell list to the lattice step (each grid cube checks
  only atoms within the cutoff; 3.4x faster at 40 Å), a parallel PSD sort, a
  faster percolation relabelling and a faster `nitrogen_network.grd` writer,
  still bit-identical to upstream: 7x faster than upstream on 8 threads for
  the dense 30–40 Å cells.
- Poreblazer fork round 4 (commit `ac451fb`), still bit-identical to upstream:
  a block search for the pore size distribution, a one-pass spanning test, a
  parallel surface area with a cell list, and vectorised lattice distances. A 50 Å,
  1,800-atom cell drops from 67 s to 12 s on 8 threads (368 s to 54 s on 1).
- Opt-in exact percolation labelling: `Cell.poreblazer(exe,
  percolation_labelling="exact")` (fork commit `24d884e`). Poreblazer 3.0.5's
  cluster labelling can split one connected cluster into several, which affects
  the pore limiting diameter and the PSD (`benchmarks/percolation_study/`).
  The default stays Poreblazer's labelling, so results still match upstream. The
  labelling used is returned as `percolation_labelling` (from the fork's log), and
  Ambuild raises if exact labelling was asked for but not confirmed.
- `Cell.poreblazer(exe, threads=N)` sets `OMP_NUM_THREADS` for the run; Slurm
  Poreblazer array tasks request 2 CPUs each (`AMBUILD_ARRAY_CPUS`) and use
  them as threads.
- `benchmarks/compare_poreblazer.py`: two Poreblazer builds on identical
  structures, comparing results, output files (including the hash of
  `nitrogen_network.grd`) and speed; `--visualisation` picks the grid output.
- `Cell.poreblazer(exe, **settings)` sets any value of Poreblazer's
  `defaults.dat` (`ab_poreblazer.DEFAULT_SETTINGS`), e.g. `cubelet_size=0.3`,
  which is 3.6x faster than the 0.2 Å default with small changes in pore
  diameters. The settings used are returned with the results, so they reach
  the `pore_result` event. `ab_poreblazer.defaults_dat()` writes the file.
- `benchmarks/profile_poreblazer.py`: Poreblazer's time per step, CPU time,
  memory and a gprof profile across cell size, atom count and grid spacing;
  findings in `docs/benchmarks.md`.
- Root `Dockerfile`: the runtime image, with Python 3.12, HOOMD-blue 7 and NumPy
  from conda-forge and Ambuild installed, in debian-slim, running as a non-root
  user. Targets `ambuild` (585 MB) and `ambuild-poreblazer` (591 MB, the default,
  with Poreblazer built at the pinned commit with upstream flags);
  `HOOMD_VARIANT=gpu` for CUDA. `AMBUILD_PARAMS_DIR`, `AMBUILD_BLOCKS_DIR` and
  `POREBLAZER_EXE` point at the bundled parameters, example blocks and
  Poreblazer. CI builds it and runs the full suite inside it.
- HOOMD-blue 4.0 and later (tested with 7.2.0 from conda-forge):
  `ambuild/hoomd4.py` ports the HOOMD 2 engine to the `hoomd.Simulation` API
  with the same force field (LJ pairs, harmonic bonds and angles, periodic
  dihedrals equal to HOOMD 2's harmonic ones, LJ walls, rigid bodies via
  `md.constrain.Rigid`). Ambuild picks the engine from the installed HOOMD.
  `testHoomdParity` checks that saved cells give HOOMD 2's energies: all-atom,
  dihedrals, rigid bodies and walls agree to within 3e-6 (HOOMD 2 was built in
  single precision).
- `ambuild/ab_mdengine.py`: the MD engine interface, `MdEngineBase` (shared
  force-field checks and type set-up) and `engineClass()`, used by `Cell` and
  the MPI worker.
- `tests/docker/hoomd7.Dockerfile` (HOOMD-blue 7.2.0 from conda-forge with
  micromamba) and a `test-hoomd7.yml` CI workflow running the full suite on it.
- README: installing HOOMD-blue from conda-forge.
- `benchmarks/` and `docs/benchmarks.md`: HOOMD-blue in-process vs worker vs
  MPI ranks, and Poreblazer compiler flags, with the raw results. In short:
  keep HOOMD in-process below ~4,000 atoms, and upstream Poreblazer `-O2` is
  already its fastest build.
- HOOMD-blue across MPI tasks: with `AMBUILD_HOOMD_LAUNCHER` set (e.g.
  `srun --ntasks=4` or `mpirun -n 4`), each optimisation or MD run executes in
  `python -m ambuild.hoomd_worker` under that launcher and the result is read
  back, while Ambuild stays a single process (`ambuild/ab_hoomdlauncher.py`).
  Unset, HOOMD runs in-process as before; empty, in one separate process.
  `deploy/slurm/ambuild_build.sbatch` sets the launcher for jobs with more
  than one task (`AMBUILD_SRUN_MPI` adds `--mpi=`). Checked with HOOMD-blue
  2.9.3 (OpenMPI 4) on 1, 2 and 4 ranks: the potential energy of a
  configuration is identical across rank counts. Only all-atom calculations
  (`rigidBody=False`) are decomposed: HOOMD-blue 2 domain decomposition fails
  for Ambuild's bonded rigid bodies, so rigid-body calculations run in one
  worker process and log a warning.
- `services/ingest`: `ambuild-upload`, a separate package (`psycopg`, `boto3`;
  no NumPy) that loads run directories into PostgreSQL (`runs`, `events`,
  `steps`, `files`, `pore_results`) and S3-compatible object storage.
  Uploads are idempotent; `--finalise` records runs whose process died as
  `incomplete`; `--recursive` includes child runs; `--scan DIR
  [--stale-after SECONDS]` finds runs not yet uploaded; `--init` creates the
  tables and bucket. Poreblazer's `*.grd` grids are not uploaded by default.
- `deploy/`: docker-compose stack (PostgreSQL 16, SeaweedFS, uploader);
  Slurm scripts that submit a build with an `afterany` upload job and an
  optional Poreblazer array fan-out of child runs; a single-node Slurm test
  image; K3s manifests for a fallback uploader CronJob. CI workflow
  `test-ingest.yml` runs the ingest and Slurm end-to-end tests.
- `Cell.startRecording(runId=None, parentRunId=None)` records a cell, e.g. one
  restored from a pickle, as a new run; `parent_run_id` defaults to the run
  the pickle came from.
- `run.json` records `parent_run_id` and, under Slurm, the job's `SLURM_*`
  variables (`scheduler`).
- `Cell(outputDir=...)`: all files a cell writes (log, CSV, pickles,
  `writeXyz`/`writeCml`/`writeCar`, HOOMD 2 logs and dumps, Poreblazer runs)
  go into that directory, which is created if needed. Relative filenames are
  resolved against it; absolute paths are unchanged. The default is the
  current working directory, as before. Restoring a pickle keeps its
  `outputDir`.
- `Cell.close()` closes the cell's CSV and log files.
- `ab_poreblazer.parse_output()` reads Poreblazer's results: system volume,
  mass and density; helium and geometric pore volumes; accessible surface
  area; pore limiting and maximum pore diameters; percolated dimensions;
  version; and the differential and cumulative pore size distributions.
  Checked against Poreblazer v3.0.5.
- Build events: `Analyse` sends each event to a list of sinks, and
  `Cell.addEventSink(sink)` adds one (any object with `handle(event)` and
  `close()`). Events are dicts with `type`, `step`, `timestamp` and `data`:
  - `step`: the row written to the CSV, at the end of every step;
  - `artifact`: path, kind (`pickle`, `xyz`, `cml`, `car`), size and sha256
    of each file written by `dump`/`writePickle` and `write*`;
  - `pore_result`: the results returned by `Cell.poreblazer()`.
  The CSV is written by `ab_analyse.CsvSink`, always the first sink; its
  output is unchanged. Sinks are not pickled.
- Run recording: `Cell(outputDir=..., recordRun=True, runId=None)` makes the
  output directory a self-contained record of the build (`ab_run`):
  - `run.json`: run id (a new UUID unless `runId` is given), status
    (`running`, `finished`, `failed`), start and finish times, error,
    Ambuild version and git commit, Python/platform/host/NumPy/HOOMD
    versions, command line and cell parameters, and the list of inputs;
  - `events.jsonl`: every event, one JSON object per line, including new
    `run_started`, `input` and `run_finished` events;
  - `inputs/`: copies of the script, parameter files and building blocks
    (with their end-group CSVs), each with its sha256.
- `Cell` is a context manager: leaving a `with` block closes it and marks a
  recorded run `failed` if an exception escaped. `Cell.close(error=...)` does
  the same explicitly.
- `ab_util.cellFromPickle(..., outputDir=...)` restores a cell with its
  output in a new directory, so a run directory can be moved and restarted.
- `artifact` events include `relpath`, the path relative to `outputDir`.
- `tests/docker/poreblazer.Dockerfile` builds Poreblazer at a pinned commit;
  `testPoreblazer.testRealPoreblazer` runs it when `POREBLAZER_EXE` is set.

### Changed
- Poreblazer no longer writes `nitrogen_network.grd` (~13 MB for a 20 Å cell,
  growing with volume) unless asked: `visualisation` defaults to `"none"`;
  pass `visualisation="grd"` (or `"xyz"`, `"both"`) to keep it.
- `tests/run_tests_docker.sh` and the `misc/` Docker scripts use the HOOMD-blue 7
  image instead of the 2020 glotzerlab images, pass GPUs only when asked
  (`AMBUILD_GPU=1`, with the image built using `HOOMD_VARIANT=gpu`), and run as
  the calling user, or as the container's root user under rootless Docker.
- `tests/docker/hoomd7.Dockerfile` builds the micromamba environment in one stage
  and copies it, pruned, into debian-slim: ~580 MB, against 7.5 and 13.3 GB for
  the glotzerlab images it replaces.
- `Cell.runMDAndOptimise()` runs `runMD()` then `optimiseGeometry()`, so it works
  with every engine (it needed HOOMD 1); it records two steps instead of one.
- `Hoomd2` shares `MdEngineBase` with `Hoomd4`.
- `Hoomd2.updateCell()` is split into `snapshotResult()` (collective under MPI)
  and `ab_hoomdlauncher.applyResult()`, which needs no HOOMD import.
- `deploy/slurm/ambuild_build.sbatch` runs the build script directly instead
  of with `srun`, so a job with several tasks still runs one Ambuild process.
- `misc/run_ambuild_docker.sh` mounts the package at `/opt/ambuild` with
  `PYTHONPATH` set; it previously mounted it where Python could not import it.
- `Cell.poreblazer()` returns the parsed results, the run directory and the
  return code (previously `None`), and logs a one-line summary.
- `Cell.poreblazer()` no longer changes the process working directory; the
  Poreblazer helpers take a `directory` argument.
- The CSV step log is flushed after every row.
- `tests/run_tests_docker.sh` pins `glotzerlab/software:2020.11.18-cuda10`
  (HOOMD 2.9.3, Python 3.6); the untagged image has moved past HOOMD 2.
- `tests/run_tests.py` reseeds `random` before every test
  (`AMBUILD_TEST_SEED`, default 1) and CI sets `PYTHONHASHSEED=0`, so a test's
  random numbers do not depend on the tests that ran before it. Previously
  `testSubunit`, `testGrowPolymerRandom`, `testDeleteBlocksType` and
  `testCell.testCat2Paf2` failed intermittently.
- Distance and dihedral tests compare floats to 12 decimal places rather than
  exactly; the last digit differed between CI runners.
- The test suite needs Ambuild installed (`pip install -e .`); nothing adds
  the checkout to `sys.path` any more. `tests/test.py`, which ran the suite a
  second time, is removed.
- `.gitattributes` keeps shell scripts, Slurm scripts, `.conf` files and
  Dockerfiles LF in Windows checkouts, so images built on Windows run.
- CI runs the HOOMD launcher tests on one process and two MPI ranks
  (`mpi` job in `test-hoomd2.yml`).

### Removed
- HOOMD-blue 1 support: `ambuild/hoomd1.py` and its three tests, which only
  ran under HOOMD 1.
- HOOMD-blue 2 support (`ambuild/hoomd2.py`); installing HOOMD-blue 2 now gives a
  clear error. The parity test keeps HOOMD 2.9.3's energies as its fixed
  reference.
- `test-hoomd2.yml`, including the multi-rank MPI launcher job: there is no MPI
  build of HOOMD-blue 4+ to run it on.
- The `hoomd` pip extra: HOOMD-blue is not on PyPI, so it could never
  install. Install HOOMD-blue from conda-forge.

### Fixed
- `ab_util.run_command` logged its keyword arguments at debug level, which would
  have included the whole environment when one is passed; `env` is left out.
- **With HOOMD-blue 2 or later installed, `Cell.writeXyz()` wrote files with no
  atoms, so `Cell.poreblazer()` analysed an empty box** (surface area NaN,
  diameters infinite). `cellData()` returned rigid-body particles instead of
  atom coordinates; `writeXyz()`, `writeCar()` and `ab_util`'s per-fragment
  export now ask for atoms. This predates 2.0.1: Poreblazer results and `.xyz`
  files from environments with HOOMD installed (such as the glotzerlab
  containers) should be regenerated.
- HOOMD-blue 3 and later were not detected (`hoomd.__version__` became
  `hoomd.version.version`), so Ambuild ran as if HOOMD were missing.
- Docs said the upstream Poreblazer Makefile compiles without optimisation;
  it uses `-O2 -unshared` (its `OFLAGS`).
- `Hoomd2.createSnapshot()` filled the snapshot on every MPI rank (only rank 0
  holds its arrays) and ordered particle, bond, angle and dihedral types by
  set iteration, which differs between processes; types are now sorted.
- The CSV step log had doubled line endings (`\r\r\n`) on Windows; it is
  now opened with `newline=""` as the `csv` module requires. Output on Linux
  is unchanged.

### Known issues
- A recorded run whose script dies without `Cell.close()` (or a `with`
  block) stays `running` in `run.json`.
- Recording is not resumed automatically when a cell is restored from a
  pickle; call `Cell.startRecording()`.
- `run.json` records the host name and absolute input paths.
- Logging uses the process-wide root logger, so when two cells exist in one
  process the most recently created one owns the `.log` file. CSV, pickle,
  structure and Poreblazer output are kept separate.
- Tests pass for the default seeds; individual randomised tests are not yet
  deterministic on their own.
- `testCatalysis.testCat1Paf2` and `testCat2Paf2` are skipped: depending on
  the random layout they need the `hc-cp-cp` angle parameter, which
  `tests/params` lacks. `testCell.testCat2Paf2` is skipped: HOOMD fails with
  "Error computing cell list" for some layouts.
- Builds are not reproducible even with fixed `random` and hash seeds: blocks
  are held in sets ordered by memory address.
- `joinBlocks` and `zipBlocks` do not write a CSV row, and `fragment_types`
  is recorded as a `defaultdict` repr.
- `Cell.writeCar()` fails when called without `data` (`CellData` is not
  subscriptable). Present before 2.0.1.
- Running HOOMD-blue across MPI ranks needs HOOMD-blue built from source, and
  is no longer tested in CI.
- Under MPI, only all-atom HOOMD calculations are decomposed; rigid-body
  calculations (the default) run on one process.

## [2.0.1]

### Fixed
- Growing and joining blocks crashed on Python 3.11+ because
  `random.sample()` no longer accepts sets (`ab_cell.py`).
- CML output depended on the Python version: attributes are now written in
  sorted order, matching the reference files, on Python 3.8+.
- Replaced `is 0` comparison that raised a `SyntaxWarning`.
- Invalid regex escape in `standard_inputs/cell_block_analysis.py`.
- `hoomd2.py` shebang pointed at a developer's miniconda installation.
- Tests used `np.int` (removed in NumPy 1.24) and `assertEquals`
  (removed in Python 3.12).

### Added
- `pyproject.toml` with package metadata, `requires-python >= 3.9` and a
  `hoomd` optional extra.
- `docs/` folder with this changelog and an architecture overview.
- README sections on installing the package and running the tests.
- Example scripts read `AMBUILD_PARAMS_DIR`, `AMBUILD_BLOCKS_DIR` and
  `POREBLAZER_EXE`, falling back to the previous `/opt` locations.

### Changed
- `setup.py` is a shim; metadata lives in `pyproject.toml`. The previous
  `setup.py` imported `ambuild` and broke `pip install .` under build isolation.
- `tests/run_tests.py` no longer adds the checkout to `sys.path`; install
  Ambuild first (`pip install -e .`).
- Example scripts no longer modify `sys.path`.
- CI (`test-basic.yml`) runs on push and pull request, installs the package,
  and tests Python 3.9, 3.11 and 3.13. Actions updated to current versions.

### Known issues
- `testCell.testWriteCml` is marked as an expected failure: its reference CML
  assumes an older end-group ordering.
- `testCatalysis.testUnbonding` is marked as an expected failure: it uses a
  `catalyst=` option that is not implemented.
- `testPoreblazer.testDummy` is skipped where `/bin/cat` is unavailable.

## [2.0.0]

Released upstream in [linucks/ambuild](https://github.com/linucks/ambuild);
changes were not recorded in this file.

## [1.0.0]

First AMBUILD public release under GPL v3.0.
