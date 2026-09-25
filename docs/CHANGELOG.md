# Changelog

All notable changes to Ambuild are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

The roadmap in [`../TODO.md`](../TODO.md) lists planned work; the increments
being delivered from it are described in [architecture.md](architecture.md).
Entries move from *Unreleased* into a version section when it is tagged.

## [Unreleased]

Work in progress towards run recording (see
[architecture.md § Delivery plan](architecture.md#delivery-plan)).

### Added
- `Cell(outputDir=...)`: all files a cell writes (log, CSV, pickles,
  `writeXyz`/`writeCml`/`writeCar`, HOOMD 2 logs and dumps, Poreblazer runs)
  go into that directory, which is created if needed. Relative filenames are
  resolved against it; absolute paths are unchanged. The default is the
  current working directory, as before. Restoring a pickle keeps its
  `outputDir`.
- `Cell.close()` closes the cell's CSV and log files.

### Changed
- `Cell.poreblazer()` no longer changes the process working directory; the
  Poreblazer helpers take a `directory` argument.
- The CSV step log is flushed after every row.
- `tests/run_tests_docker.sh` pins `glotzerlab/software:2020.11.18-cuda10`
  (HOOMD 2.9.3, Python 3.6); the untagged image has moved past HOOMD 2.
- `tests/run_tests.py` seeds `random` (`AMBUILD_TEST_SEED`, default 1) and CI
  sets `PYTHONHASHSEED=0`, so runs are reproducible. Previously
  `testSubunit`, `testGrowPolymerRandom`, `testDeleteBlocksType` and
  `testCell.testCat2Paf2` failed intermittently.
- The test suite needs Ambuild installed (`pip install -e .`); nothing adds
  the checkout to `sys.path` any more. `tests/test.py`, which ran the suite a
  second time, is removed.

### Known issues
- Logging uses the process-wide root logger, so when two cells exist in one
  process the most recently created one owns the `.log` file. CSV, pickle,
  structure and Poreblazer output are kept separate.
- Tests pass for the default seeds; individual randomised tests are not yet
  deterministic on their own.
- `testCatalysis.testCat1Paf2` and `testCat2Paf2` are skipped: depending on
  the random layout they need the `hc-cp-cp` angle parameter, which
  `tests/params` lacks.
- `Cell.writeCar()` fails when called without `data` (`CellData` is not
  subscriptable). Present before 2.0.1.

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
