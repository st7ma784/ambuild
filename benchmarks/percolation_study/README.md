# Percolation labelling study

Evidence that Poreblazer 3.0.5's cluster labelling (`clusteranalysis` in
`percolation.f90`) can split one connected cluster into several labels, and what an
exact union-find labelling changes. The white paper "Fast, exact Poreblazer" presents
the results; `docs/benchmarks.md` summarises them. Ambuild's Poreblazer fork keeps
upstream's labelling, so its results match upstream's.

| File | What it does |
| --- | --- |
| `build.sh` | Fetches the fork's `percolation.f90` and builds the programs below against it |
| `make_exact.py` | Makes the exact variant: adds `clusteranalysis_exact` (periodic union-find, clusters numbered by first site in scan order) and calls it instead of `clusteranalysis` |
| `study.f90` | Links Poreblazer's labelling and the exact one against the same spanning code. `random`: statistics on random site lattices; `grd`: spanning against probe radius on a `nitrogen_network.grd`; `dump`: the pieces of the largest cluster at one radius |
| `perctest.f90` | Poreblazer's labelling against an independent union-find on 60 random 40³ lattices |
| `pblabel.py` | A line-by-line Python port of the labelling (checked against `labelcheck.f90`), the exact labelling, and the search for the smallest failing lattice |
| `labelcheck.f90` | Prints Poreblazer's labels for lattices on stdin, to check the Python port |

## Reproducing

```sh
./build.sh                                   # needs git, gfortran and python3
build/perctest                               # 54-56 of 60 differ (the lattices are unseeded)
for L in 16 32 64; do build/study random $L 0.15 0.70 0.01 50 7 > data/sweep_$L.txt; done
# nitrogen_network.grd from a Poreblazer run with visualisation="grd" on a saved cell
# (benchmarks/compare_poreblazer.py prepare), e.g. the 30 A, 6-block cell:
build/study grd path/to/nitrogen_network.grd 9.8 11.0 0.02 > data/rsweep_box30_blocks6.txt
build/study dump path/to/nitrogen_network.grd 10.24 > data/dump_box30_blocks6.txt
python3 pblabel.py search > data/example.json
python3 pblabel.py lattices 3 300 > lattices.txt
build/labelcheck < lattices.txt > labels.txt && python3 pblabel.py check lattices.txt labels.txt
```

## Data

- `data/sweep_L.txt`: per occupation probability p, the fraction of lattices labelled
  differently, the excess of Poreblazer's cluster count over the true count, the
  fraction of occupied sites in clusters Poreblazer splits, and the spanning
  probability under each labelling (50 lattices per point, seed 7).
- `data/rsweep_*.txt`: axes spanned by the first spanning cluster against probe
  radius, for each labelling, on the 30 Å/72-atom and 40 Å/168-atom cells. Radii come
  from the grid file, which stores 5 significant figures.
- `data/dump_box30_blocks6.txt`: at radius 10.24 Å, the largest true cluster and its
  pieces under Poreblazer's labelling, with a projection along z.
- `data/example.json`: the smallest failing lattice found (8 sites) and the full trace
  of Poreblazer's label table.
