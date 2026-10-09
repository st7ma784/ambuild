# Benchmarks

Measurements behind the scaling decisions in [`../TODO.md`](../TODO.md) §4, §7
and §8. Raw results are in [`../benchmarks/results/`](../benchmarks/results/).

The HOOMD-blue figures were measured with HOOMD-blue 2.9.3, before Ambuild moved to
HOOMD-blue 4+. The launcher costs and the conclusions still apply; rerunning
`bench_hoomd.py` for multiple ranks needs an MPI build of HOOMD-blue 4+.

**Machine:** 4 × Intel Xeon E5-4620 v4 (2.1 GHz Broadwell, 80 threads), 30 GB
RAM, rootless Docker. The cores are slow, which makes fixed costs easy to see.
Every figure is the median of 3 runs.

## HOOMD-blue: in-process, worker process and MPI ranks

`benchmarks/bench_hoomd.py` in the OpenMPI build of HOOMD-blue 2.9.3
(`glotzerlab/software:2020.11.18-skylakex-cuda10-mlx-openmpi4.0.1`), capped at 16
CPUs. Benzene cells at a fixed density, all-atom unless stated. Each
measurement restores the same saved cell and times one `Cell.runMD()` call.

Wall time (s) for 2000 all-atom MD steps:

| Cell | Atoms | In-process | Worker, 1 process | 2 ranks | 4 ranks | 8 ranks |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 30 Å | 72 | **0.65** | 1.36 | 1.26 | 1.46 | 1.72 |
| 60 Å | 576 | **1.02** | 1.90 | 1.66 | 1.88 | 2.01 |
| 90 Å | 1,944 | **2.08** | 3.35 | 2.62 | 2.70 | 2.63 |
| 120 Å | 4,608 | 4.43 | 6.15 | 4.56 | 3.91 | **3.86** |

Wall time (s) for a single step, which is the fixed cost of a calculation
(process start, HOOMD context, building and transferring the snapshot):

| Cell | In-process | Worker, 1 process | 2 ranks | 4 ranks | 8 ranks |
| --- | ---: | ---: | ---: | ---: | ---: |
| 30 Å | 0.53 | 1.37 | 1.39 | 1.43 | 1.63 |
| 60 Å | 0.57 | 1.62 | 1.48 | 1.62 | 1.85 |
| 90 Å | 0.74 | 1.94 | 1.70 | 1.96 | 2.34 |
| 120 Å | 1.11 | 2.67 | 2.59 | 2.88 | 3.21 |

Rigid-body MD, 2000 steps (never decomposed; see TODO §7): in-process 0.65,
0.78, 1.51 and 3.06 s; in a worker 1.50, 1.73, 2.50 and 4.04 s.

**What it shows**

- **Launching a worker costs 0.8–1.6 s per calculation,** growing with the cell,
  whatever the rank count.
- **MPI does divide the MD work.** Subtracting the single-step cost, 2000 steps
  of the 120 Å cell take 3.3 s in one process, 1.0 s on 4 ranks and 0.65 s on
  8: about 5x. At 90 Å it is 1.3 s against 0.74 s on 4 ranks.
- **In-process is fastest below ~4,000 atoms** for 2000-step runs. Extrapolating
  the linear fit, 4 ranks break even after about 1,550 steps for the 120 Å cell
  and about 4,000 steps for the 90 Å cell.
- Typical Ambuild builds (hundreds to a few thousand atoms, rigid bodies, short
  optimisations between growth steps) should stay in-process. Use
  `AMBUILD_HOOMD_LAUNCHER` for all-atom MD on cells of ~5,000 atoms or more, or
  long runs on ~2,000 or more. Throughput for many builds comes from running
  them in parallel (TODO §8), not from splitting each one.

Caveats: one node only (no network between ranks), slow cores, and runs capped
at 16 CPUs with at most 8 ranks, so hyperthreads were not contended.

## Poreblazer: compiler flags

`benchmarks/bench_poreblazer.py` with Poreblazer v3.0.5 (`a753c72`) built three
ways by `benchmarks/poreblazer-flags.Dockerfile` (gfortran 14.2), each run on
one CPU against the same saved cells.

Wall time (s):

| Cell | Atoms | `-O0` | upstream `-O2 -unshared` | `-O3 -march=native` |
| --- | ---: | ---: | ---: | ---: |
| 20 Å | 24 | 3.69 | **1.99** | 2.21 |
| 30 Å | 72 | 33.67 | **19.77** | 20.34 |
| 40 Å | 168 | 167.87 | **94.58** | 107.96 |

- All three builds give identical results (surface area, pore limiting diameter,
  pore volumes).
- **The upstream `-O2` is already the fastest.** `-O3 -march=native` is 3–14%
  slower and `-O0` about 1.8x slower, so compiler flags are not worth pursuing.
- Runtime grows with the cell volume, not the atom count, and steeply: about 10x
  from 20 to 30 Å and 5x from 30 to 40 Å. The levers are OpenMP over the grid,
  splitting the work, or fanning out runs (as the Slurm array already does).

## Poreblazer: where the time goes

`benchmarks/profile_poreblazer.py` (results in `benchmarks/results/profile_*.json`)
times each of Poreblazer's eight steps, with CPU time and peak memory, for benzene
cells that vary one thing at a time; a `-pg` build adds a gprof profile. Same
machine, one CPU, upstream `-O2` build, 0.2 Å grid unless stated.

Wall time (s) of the steps that matter:

| Cell | Atoms | Total | Lattice | N₂ lattice | Pore size distribution | Peak memory |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 20 Å | 24 | 2.2 | 0.72 | 0.81 | 0.38 | 90 MB |
| 30 Å | 72 | 21.0 | 6.63 | 2.54 | 11.09 | 234 MB |
| 40 Å | 168 | 96.6 | 35.03 | 5.87 | 53.46 | 549 MB |
| 30 Å | 36 | 10.0 | 3.31 | 2.69 | 3.31 | 235 MB |
| 30 Å | 144 | 42.0 | 14.15 | 2.59 | 24.27 | 232 MB |
| 30 Å | 576 | 84.6 | 54.07 | 2.60 | 26.28 | 225 MB |

Helium lattice, helium volume, surface area and limiting diameter together take
under 1.5 s in every case.

- **Two steps take ~90% of the time.** `lattice_calculations` measures the distance
  from every grid cube to every atom (cost ∝ cubes × atoms), and dominates dense
  cells. `pore_distribution` takes 10,000 random points and, for each, scans the
  cubes from the largest empty sphere down until one contains the point; the scan
  gets longer as pores get smaller, and it dominates sparse cells.
- **Both spend their time in one function.** gprof puts 30–40% of the total in
  `fundcell_snglminimage`, the periodic minimum-image distance, called ~2.4 billion
  times for the 40 Å and 576-atom cells.
- **Memory grows with the grid volume**: ~90 MB at 20 Å, 549 MB at 40 Å; roughly
  1.8 GB can be expected at 60 Å.

Grid spacing (the 30 Å, 72-atom cell):

| Grid | Time | Memory | Pore limiting diameter | Maximum pore diameter | Surface area, volumes |
| --- | ---: | ---: | ---: | ---: | --- |
| 0.2 Å (default) | 21.7 s | 234 MB | 20.48 Å | 25.98 Å | reference |
| 0.3 Å | 6.1 s | 91 MB | 20.19 Å | 25.93 Å | unchanged |
| 0.4 Å | 2.9 s | 62 MB | 20.41 Å | 25.98 Å | unchanged |

A coarser grid is 3.6–7.6x faster here with little change, but diameters are only
resolved to about the grid spacing, so small-pore structures lose relatively more.
`Cell.poreblazer(exe, cubelet_size=0.3)` now selects it.

Builds that do not help:

| Build | 72 atoms | 576 atoms | Results |
| --- | ---: | ---: | --- |
| upstream `-O2` | 20.4 s | 80.9 s | reference |
| `-O2 -flto` (lets the distance function inline) | 20.9 s | 79.7 s | identical |
| `-O2 -fopenmp`, 4 threads | 20.9 s | 147.2 s | identical here |

Poreblazer already carries OpenMP directives on the lattice loop, which the
Makefile never enables. Switching them on is **slower** (and used 488 s of CPU for
the 576-atom cell), and the loop has data races: loop temporaries such as
`sig2_rdist2`, `rdist6`, `rdist12` and `lj_energy` are shared between threads, and
the cube lists are filled using a counter read after another thread may have
incremented it. Results matched in this test, but the races are real.

Where speed-ups could come from, all needing changes to Poreblazer's Fortran:

| Step | Parallel? | Better algorithm |
| --- | --- | --- |
| Lattice (cubes × atoms) | Yes: every cube is independent. Fix the races (private temporaries, per-thread cube lists merged afterwards) | Bin atoms into cells and check only those within the cutoff (12.8 Å) rather than every atom |
| Pore size distribution (10,000 samples) | Yes: samples are independent, given a per-thread random stream | A spatial index over the sphere centres instead of a linear scan |
| Surface area (atoms² × 500 trials) | Yes, per atom, with per-thread random streams | Cell list for the overlap test; negligible for current cells |
| Percolation, volumes | Small | — |

Poreblazer has no MPI support; splitting the grid across ranks is possible, but
threads and the algorithmic changes above come first, and many cells already run
in parallel as Slurm array tasks.

## Poreblazer: Ambuild's OpenMP fork

[st7ma784/poreblazer](https://github.com/st7ma784/poreblazer) (branch `ambuild`,
commit `8ed0c70`, described in its `FORK.md`) changes upstream 3.0.5 in five rounds:

1. **OpenMP made correct** (commit `618d0c0`): private temporaries and cubelet lists
   built after the lattice loop, and the 10,000 PSD sample sites drawn up front in the
   original order, then sampled in parallel. Built with `-fopenmp`.
2. **Less work** (commit `3ce6695`):
   - A *cell list* for the lattice step. Atoms are binned into ~2 Å cells, and each grid
     cube checks only atoms in cells within the 12.8 Å cutoff, in ascending atom order,
     so every sum and minimum is bit-identical. A cube whose nearest atom (or surface)
     could lie beyond the cutoff, i.e. in a pore wider than ~25 Å, checks every atom,
     as upstream does. Non-orthorhombic cells search further, in slanted
     coordinates, scaled by a bound on the cell's shape (commit `d70fa08`).
   - A task-parallel sort of the cubes by pore radius before the PSD. It was serial
     and took ~3 s at 60 Å. Tie order can differ, which changes no result.
   - Cluster relabelling in the percolation analysis through a lookup table, instead
     of searching every label so far for every site.
   - `nitrogen_network.grd` written a plane per statement, ~30% faster. What remains
     is gfortran's number formatting, which libgfortran serialises across threads.
3. **Opt-in exact percolation labelling** (commit `24d884e`), off by default; see
   "Upstream's cluster labelling splits connected clusters" below.
4. **Round 4** (commit `ac451fb`), each change still bit-identical:
   - *Pore size distribution by blocks.* Each sample's answer is the largest sphere
     containing its point. Cubes are grouped into blocks, visited from the largest radius
     they hold down, stopping when no block left can beat the best sphere so far, and
     skipping blocks out of reach. Same containment test, so same sphere.
   - *Spanning test in one pass* over the grid, instead of up to three passes per
     candidate cluster.
   - *Surface area in parallel*: random numbers drawn first in upstream's order, atoms in
     parallel, areas summed in atom order; the overlap test uses a cell list.
   - *Vectorised lattice distances*: a separate loop over contiguous arrays with an exact,
     vectorisable `anint`. The Makefile adds `-ffp-contract=off` (no fused multiply-adds)
     and `-fvect-cost-model=dynamic`.
5. **Half the memory** (commit `8ed0c70`); see "Memory" below.

`benchmarks/compare_poreblazer.py` runs each build on the same saved structures, in
a container limited to 8 CPUs (`benchmarks/results/compare_*.json`). The first four
cells are dense Ambuild cells; the last two are nearly empty, with pores far wider
than the cutoff, to exercise the all-atom fallback. Upstream and the "with grid"
columns write `nitrogen_network.grd` (the old `defaults.dat`); the last column uses
Ambuild's default, which does not:

| Cell | Atoms | Upstream | Lattice step | Round 1, 8 threads | Now, 1 thread | 8 threads | 8 threads, no grid | Speed-up |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 20 Å | 24 | 2.4 s | 0.9 → 0.9 s | 1.4 | 2.0 | 1.3 | 0.7 | 1.8x |
| 30 Å | 72 | 20.1 s | 6.9 → 4.3 s | 6.7 | 17.6 | 5.0 | 3.5 | 4.1x |
| 40 Å | 168 | 112.6 s | 38.4 → 11.2 s | 22.3 | 81.4 | 15.8 | 12.0 | 7.1x |
| 30 Å | 576 | 84.4 s | 54.9 → 30.3 s | 15.6 | 58.4 | 11.4 | 9.4 | 7.4x |
| 40 Å | 24 | 19.5 s | 4.9 → 5.4 s | – | 17.8 | 10.3 | 4.4 | 1.9x |
| 60 Å | 48 | 70.5 s | 30.9 → 30.5 s | – | 67.1 | 34.7 | 17.0 | 2.0x |

"Lattice step" is upstream against the fork, both on 1 thread; speed-up is upstream
against 8 threads with the grid. The cell list pays off as the cell grows past twice
the cutoff (3.4x on the 40 Å lattice step), and does nothing for the near-empty cells,
where nearly every cube falls back to checking every atom (which costs little there).

**Every run's output is identical to upstream's**: all 14 parsed results and the
`psd.txt`, `psd_cumulative.txt` and `nitrogen_network.grd` files (the grid holds
every cube's nearest-surface distance), at 1, 2, 4 and 8 threads (24 of 24 runs).

What remained serial after round 2: writing `nitrogen_network.grd` when asked for
(2–20 s, growing with the grid), and the percolation analysis in the helium lattice,
nitrogen lattice and limiting diameter steps. Those cost 0.1–0.4 s each for the dense
cells, and about 7 s in all at 60 Å (27 million grid cubes). The biggest parallel cost
left was the PSD search, which scanned the sorted cubes for every sample. Round 4 below
deals with it.

### Round 4: where the time went next

A denser, larger cell (50 Å, 150 benzene blocks, 1,800 atoms; 15.6 million grid cubes,
1 GB) shows where the time went after the cell list, without the grid file. Upstream
takes 982 s on one thread for this cell (`compare_upstream_50A.json`): the fork is 18x
faster on one thread and 84x faster on 8.

| Step | Before round 4, 1 thread | After | Before, 8 threads | After |
| --- | ---: | ---: | ---: | ---: |
| Lattice | 95.2 s | 46.3 s | 12.9 s | 6.3 s |
| Surface area | 7.1 s | 0.4 s | 7.1 s | 0.1 s |
| Pore size distribution | 247.2 s | 3.8 s | 29.3 s | 1.5 s |
| Limiting diameter | 15.9 s | 1.7 s | 15.7 s | 1.6 s |
| **Total** | **367.6 s** | **54.4 s** | **67.3 s** | **11.7 s** |

Every step that dominated is now small. The lattice step is left: its cost is inherent
(each cube against the atoms within 12.8 Å), and it scales with threads.

Across the benchmark cells (`compare_round4*.json`, grid file written):

| Cell | Atoms | Upstream | Round 2, 8 threads | Now, 1 thread | 8 threads | Speed-up |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 20 Å | 24 | 2.4 s | 1.3 | 1.6 | 1.2 | 2.0x |
| 30 Å | 72 | 20.1 s | 5.0 | 6.2 | 3.4 | 6.0x |
| 40 Å | 168 | 112.6 s | 15.8 | 15.0 | 8.1 | 13.9x |
| 30 Å | 576 | 84.4 s | 11.4 | 17.7 | 4.9 | 17.3x |
| 40 Å, near-empty | 24 | 19.5 s | 10.3 | 13.1 | 9.3 | 2.1x |
| 60 Å, near-empty | 48 | 70.5 s | 34.7 | 49.9 | 31.1 | 2.3x |

**Output is still identical**:
- the default labelling matches upstream in 12 of 12 runs (all 14 results and the
  three file hashes);
- exact labelling matches its reference in 6 of 6;
- the 50 Å cell matches upstream at 1 and 8 threads (all 14 results and both PSD hashes).

For the near-empty cells, most of what is left is writing `nitrogen_network.grd`
(20 s at 60 Å), which Ambuild does not ask for by default.

### Memory

Poreblazer's memory scales with the number of grid cubes, (side / cubelet size)³: 15.6
million at 50 Å and 0.2 Å, 125 million at 100 Å. Upstream and the earlier fork kept
about 70 bytes per cube. The fork now keeps about 34, all of them needed:

| Per cube | Bytes | What |
| --- | ---: | --- |
| `lattice_rdist2` | 8 | pore radius (squared), read by the PSD, the limiting diameter and the grid file |
| `lattice_lj_he` | 8 | helium Lennard-Jones energy (could be recomputed, at the cost of lattice time) |
| 4 masks | 4 | geometric, helium and nitrogen accessibility, and the limiting diameter's scratch mask |
| percolation labels | 4 | only while a percolation analysis runs (4 more with exact labelling) |
| sorted radii | 8 × accessible fraction | the limiting diameter's bisection |
| nitrogen list | 4 × accessible fraction | the PSD's sample sites |

Removed: the stored indices of each cube (computed from its number instead), an unused
list of geometric cubes, a helium list that repeated the helium mask, three index
copies in the sorted PSD array, and the 2-byte masks (now 1 byte). The arrays stay
separate, one per quantity (structure of arrays): the loops read one or two of them at a
time and the lattice distances vectorise, so packing them into a record per cube would
only add padding.

| Cell | Before | After |
| --- | ---: | ---: |
| 20 Å | 110 MB | 86 MB |
| 30 Å | 235 MB | 149 MB |
| 40 Å | 550 MB | 276 MB |
| 50 Å, 1,800 atoms | 1,045 MB | 502 MB |
| 60 Å | 1,857 MB | 929 MB |

Run times are unchanged, and output is still identical:
- the default labelling matches upstream in 12 of 12 runs;
- exact labelling matches its reference in 6 of 6;
- the 50 Å cell matches upstream on 1 and 8 threads.

`ab_poreblazer.memory_estimate_mb(A, B, C, cubelet_size)` bounds the peak: 64 MB plus
36 bytes per cube, or 40 with exact labelling. It uses Poreblazer's own grid
arithmetic. It is within 25% above every measurement, and gives about 4.4 GB for a 100 Å
cell.
- `Cell.poreblazer(exe, memory_limit_mb=...)` refuses a cell that would not fit, before
  starting, and returns the estimate as `memory_estimate_mb`.
- The Slurm fan-out asks for the estimate as `--mem`, plus 10% and room for the Python
  process (`AMBUILD_ARRAY_MEM` overrides it; `none` uses the site's default). Each task
  passes its allocation on as the limit.

### Upstream's cluster labelling splits connected clusters

The percolation analysis (`clusteranalysis` in `percolation.f90`) records only one
level of merges between cluster labels, so one connected cluster can come out as
several. On random 40³ lattices, 54 of 60 were labelled differently from their true
periodic components, worst near 30% occupancy: the percolation threshold, where the
limiting diameter bisection works. An exact union-find labelling (an experimental
build, not in the fork; `compare_exact_labelling.json`) changes the results of four
of the six cells:

| Cell | Atoms | Pore limiting diameter, upstream | Exact labelling | PSD |
| --- | ---: | ---: | ---: | --- |
| 30 Å | 72 | 20.48 Å | 20.99 Å | same |
| 40 Å | 168 | 20.91 Å | 21.39 Å | same |
| 30 Å | 576 | 7.83 Å | 7.83 Å | differs (larger nitrogen network) |
| 40 Å | 24 | 42.96 Å | 42.97 Å | same |

The fork keeps upstream's labelling by default, so its results match. Exact labelling
is opt-in (commit `24d884e`): `Cell.poreblazer(exe, percolation_labelling="exact")`
adds the labelling after the visualisation option in `defaults.dat` (`0, 1`), the fork logs
`Percolation labelling: exact`, and Ambuild raises if the executable does not confirm
it (upstream Poreblazer ignores the value). With the default, the fork still matches
upstream in all 12 runs checked (six cells, 1 and 8 threads). With exact labelling it
matches the experimental build above in all 12, and costs the same time. Exact
labelling would also let the percolation analysis run in parallel.

The full study is in `benchmarks/percolation_study/`, with its code and raw data. It
includes an 8-site counterexample traced step by step and statistics on random
lattices: as the lattice grows, the exact labelling's threshold estimate approaches
the known site percolation threshold (0.3116), while upstream's moves away from it.
It also shows spanning against probe radius on real cells: upstream's answer is not
monotonic, which misleads the limiting-diameter bisection.

## xTB checks: cost, and tblite against CP2K

The `xtb` stage's worker (`docs/xtb-spec.md`) on `li_ion_carbon` built without its
optimisation stages (472 atoms, C and H, a 30 Å cell) and its 2×1×1 and 2×2×1 supercells.
A different machine from the rest of this page: an Intel Core i7-1365U laptop, Docker
limited to 8 threads and 4 GB, conda-forge's `tblite` 0.7.0 and `xtb` 6.7.1. One run
each. Results are in `benchmarks/results/xtb_cp2k.json`.

One single point, wall time and peak memory:

| Method | 472 atoms | 944 atoms | 1,888 atoms |
| --- | --- | --- | --- |
| GFN1-xTB (`tblite`) | 19 s, 0.54 GB | 109 s, 1.9 GB | killed: out of memory at 3.4 GB |
| GFN2-xTB (`tblite`) | 28 s, 0.58 GB | 122 s, 2.1 GB | not run |
| GFN-FF (`xtb`) | 14 s, 0.49 GB | 71 s, 1.4 GB | killed: out of memory at 3.3 GB |

- **Doubling the atoms costs 5 to 6 times the time and 3 to 4 times the memory,** for the
  force field as for tight binding.
- **A relaxation step costs about a single point:** 15 LBFGS steps of GFN1-xTB on the
  472-atom cell took 201 s.
- **`ambuild.xtb.memoryEstimateMb`** is 150 MB + 550 MB × (atoms / 472)^1.85. It bounds
  the two sizes measured and is extrapolated above 944 atoms: about 7 GB at 1,900 and
  8 GB at 2,000, the stage's default limit.
- **Supercells agree with the cell** to 4 or more figures in the energy per atom, the
  largest force and the gap.

The same GFN1-xTB single point from `tblite` and from CP2K 2024.3, which implements the
method independently (`benchmarks/compare_xtb_cp2k.py`):

| Cell | Atoms | Largest force, `tblite` | CP2K | Largest difference on any atom | Energy difference | Time, `tblite` | CP2K |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| benzene, one C–H pushed out 0.2 Å, 10 Å cell | 12 | 6.5878 eV/Å | 6.5880 eV/Å | 0.0006 eV/Å | 0.12 meV per atom | under 1 s | 2 s |
| `li_ion_carbon`, unoptimised, 30 Å cell | 472 | 5.3055 eV/Å | 5.3047 eV/Å | 0.0025 eV/Å | 0.38 meV per atom | 19 s | 396 s |

- **The two programs agree on the forces to 0.05%,** and on which atom is most strained.
- **The energies differ slightly,** as CP2K runs at zero electronic temperature and
  `tblite` at 300 K.
- **The `xtb` binary's periodic GFN1 does not agree:** it gave −729.16 Eh for the
  472-atom cell, against −735.99 Eh from both of these. The stage never uses it for
  tight binding.
- **CP2K's time is with untuned settings** (orbital transformation, full single inverse
  preconditioner), so it is an upper bound.

What optimising the build is worth, by the same measure: the recipe as shipped has a
largest force of 1.64 eV/Å, against 5.31 eV/Å without its optimisation stages.

## Reproducing

From the repository root, on a machine with Docker:

```sh
# HOOMD-blue
docker run --rm --cpus=16 -v "$PWD":/ambuild:ro -v "$PWD/benchmarks/results":/results \
  --user 0:0 -e OMPI_ALLOW_RUN_AS_ROOT=1 -e OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 \
  -e PYTHONPATH=/ambuild -e AMBUILD_TESTS_DIR=/ambuild/tests \
  -e OMPI_MCA_plm_rsh_agent=/bin/false -e OMPI_MCA_btl=self,vader -e OMPI_MCA_pml=ob1 \
  -e OMPI_MCA_btl_vader_single_copy_mechanism=none \
  glotzerlab/software:2020.11.18-skylakex-cuda10-mlx-openmpi4.0.1 \
  python3 /ambuild/benchmarks/bench_hoomd.py /results/hoomd.json --sizes 30 60 90 120 --ranks 2 4 8

# Poreblazer: build the variants, save the cells once, time each build
for v in "O0|-O0" "O2|" "O3|-O3 -march=native"; do
  docker build -f benchmarks/poreblazer-flags.Dockerfile --build-arg OFLAGS="${v#*|}" -t ambuild-bench-pb:${v%%|*} .
done
docker run --rm -v "$PWD/pb":/pb ambuild-bench-pb:O2 python3 /ambuild/benchmarks/bench_poreblazer.py prepare /pb
for t in O0 O2 O3; do
  docker run --rm --cpus=1 -v "$PWD/pb":/pb -v "$PWD/benchmarks/results":/results ambuild-bench-pb:$t \
    python3 /ambuild/benchmarks/bench_poreblazer.py run /pb $t /results/poreblazer_$t.json
done
```

Poreblazer profile (the gprof build compiles and links with `-pg`):

```sh
docker build -f benchmarks/poreblazer-flags.Dockerfile -t ambuild-bench-pb:O2 .
docker build -f benchmarks/poreblazer-flags.Dockerfile --build-arg OFLAGS="-O2 -pg" \
  --build-arg LINKERFLAGS=-pg -t ambuild-bench-pb:gprof .
docker run --rm --cpus=1 -v "$PWD/benchmarks/results":/results ambuild-bench-pb:O2 \
  python3 /ambuild/benchmarks/profile_poreblazer.py /results/profile_O2.json
docker run --rm --cpus=1 -v "$PWD/benchmarks/results":/results ambuild-bench-pb:gprof \
  python3 /ambuild/benchmarks/profile_poreblazer.py /results/profile_gprof.json --gprof
```

Upstream against the fork (identical structures; the results record every parsed
value and the hashes of the PSD files and `nitrogen_network.grd`):

```sh
docker build -f benchmarks/poreblazer-flags.Dockerfile -t ambuild-bench-pb:upstream .
docker build -f benchmarks/poreblazer-flags.Dockerfile \
  --build-arg POREBLAZER_REPO=https://github.com/st7ma784/poreblazer.git \
  --build-arg POREBLAZER_COMMIT=9d4cbccbd0def671d4007099203e8e4b935c3cd3 \
  -t ambuild-bench-pb:fork .   # the fork's Makefile flags
docker run --rm -v "$PWD/cases":/cases ambuild-bench-pb:fork \
  python3 /ambuild/benchmarks/compare_poreblazer.py prepare /cases
docker run --rm --cpus=1 -v "$PWD/cases":/cases -v "$PWD/benchmarks/results":/results ambuild-bench-pb:upstream \
  python3 /ambuild/benchmarks/compare_poreblazer.py run /cases upstream /results/compare_upstream_grd.json --threads 1
docker run --rm --cpus=8 -v "$PWD/cases":/cases -v "$PWD/benchmarks/results":/results ambuild-bench-pb:fork \
  python3 /ambuild/benchmarks/compare_poreblazer.py run /cases fork /results/compare_celllist.json
```

The `--user 0:0` and `OMPI_ALLOW_RUN_AS_ROOT*` settings are for rootless Docker,
where the container's root user is the host user.

tblite against CP2K, for an exported structure `cell/structure.xyz` (`docs/export.md`):

```sh
docker build -f tests/docker/xtb.Dockerfile -t ambuild-xtb tests/docker
run="docker run --rm -v $PWD:/ambuild -v $PWD/cell:/cell -e PYTHONPATH=/ambuild -w /cell"
$run ambuild-xtb python -m ambuild.xtb_worker structure.xyz --out xtb.json --forces
$run ambuild-xtb python /ambuild/benchmarks/compare_xtb_cp2k.py input structure.xyz cell.inp
$run --entrypoint cp2k cp2k/cp2k:2024.3_openmpi_generic_psmp -i cell.inp -o cp2k.out
$run ambuild-xtb python /ambuild/benchmarks/compare_xtb_cp2k.py compare xtb.json cp2k.out forces.xyz
```
