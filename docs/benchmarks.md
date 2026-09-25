# Benchmarks

Measurements behind the scaling decisions in [`../TODO.md`](../TODO.md) §4, §7
and §8. Raw results are in [`../benchmarks/results/`](../benchmarks/results/).

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

The `--user 0:0` and `OMPI_ALLOW_RUN_AS_ROOT*` settings are for rootless Docker,
where the container's root user is the host user.
