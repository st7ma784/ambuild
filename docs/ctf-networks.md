# Large covalent triazine frameworks

Three example recipes build large porous networks from triazine rings, benzenes and
alkynes, in the spirit of covalent triazine frameworks (CTFs) such as CTF-1. Each builds
in a 60 Å cell, several thousand atoms, sized for a Slurm node, and ends with Poreblazer.

## The blocks

| Block | Unit | Links | Typing (GAFF 1.81, `params/gaff_ctf`) |
| --- | --- | --- | --- |
| `triazine` | s-triazine, C₃N₃ | 3, at its carbons (end group `t`) | `cp` linking carbons, `nb` ring nitrogens; C–N 1.338 Å, N–C–N 126.8°, C–N–C 113.2° (measured) |
| `benzene_135` | benzene | 3 (1,3,5) | `cp`, `ca` |
| `benzene_14` | benzene | 2 (para) | `cp`, `ca` |
| `acetylene_cg` | alkyne | 2 (end groups `g`, `h`) | `cg≡ch`: alkynes join like end to like end, so alkyne–alkyne joins are the polyyne single bond (1.369 Å) |

Joins: triazine–ring, triazine–triazine and ring–ring are biaryl bonds (1.485 Å);
triazine or ring to alkyne, 1.433 Å.

## The recipes

| Recipe | Blocks | Joins |
| --- | --- | --- |
| `ctf1_large` | triazine, 1,4-phenylene | triazine–phenylene only: strictly alternating, as CTF-1 from 1,4-dicyanobenzene |
| `ctf_alkyne_large` | triazine, alkyne | triazine–alkyne and alkyne–alkyne (ethynylene and polyyne struts), no triazine–triazine |
| `ctf_mixed_large` | triazine, both benzenes, alkyne | every one to itself and the others |

**How each builds:**
1. **Seed:** a 60 Å cell with 8 seeds (the mixed recipe adds 4 1,3,5-benzenes).
2. **Ten passes**, each:
   - grow 60 blocks;
   - join clusters twice;
   - zip;
   - a rigid-body optimisation with dihedrals.
3. **Poreblazer** on a 0.3 Å grid, about 340 MB for this cell. The default 0.2 Å grid
   would need about 1 GB.

**Resources:** 8 CPUs, 8 GB and 8 hours, for the Slurm agent.

**Why join:** grow extends each seed's own cluster, and zip only bonds free ends that
happen to meet. Without joins, the first versions of these recipes ended as 30–40
separate clusters, one per seed, none bonded to another. Two joins per pass, which move
whole clusters to bond them to each other, turn the seeds' clusters into one or two large
frameworks spanning the cell. Fewer seeds with more growth each needs fewer joins; joins are the
slow part of a build.

**Going larger:** sweep or campaign the cell (`/cell/box`), the grow count, or the number
of passes. Poreblazer's memory grows with the cell's volume over the grid spacing cubed
(`ambuild.ab_poreblazer.memory_estimate_mb`): an 80 Å cell at 0.3 Å needs about 710 MB.
HOOMD-blue across several MPI tasks helps only for all-atom calculations
(`docs/benchmarks.md`).

## Measured builds

Full builds on 10 CPUs (local Docker, HOOMD-blue 7.2, Poreblazer with 8 threads):

| Recipe | Time | Atoms | Pieces | Largest piece | Worst join vs r0 | Density (g/cm³) | PLD (Å) | Largest pore (Å) | Pores span | Surface (m²/g) | Helium volume (cm³/g) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ctf1_large` | 12.3 min | 5,253 | 2 | 2,722 atoms | 0.020 Å | 0.36 | 10.4 | 23.7 | 1 direction | 4,790 | 2.28 |
| `ctf_alkyne_large` | 0.6 min | 2,093 | 1 | 2,093 atoms | 0.002 Å | 0.19 | 19.4 | 26.4 | 1 direction | 8,426 | 4.89 |
| `ctf_mixed_large` | 6.7 min | 3,990 | 2 | 2,706 atoms | 0.019 Å | 0.27 | 12.2 | 21.8 | 2 directions | 6,718 | 3.18 |

- **Connectivity:** the joins turn 8–12 seeds into one or two large frameworks. The two
  pieces of `ctf1_large` and `ctf_mixed_large` share the cell but aren't bonded to each
  other. More joins per pass, or a last join stage, would bond them, at the cost of time.
- **Pores:** all three have pores wide enough for most small molecules, with
  percolating networks. The alkyne struts give the most open structure.
- **Caveat:** these are amorphous builds, and their numbers aren't a crystalline CTF's.

## Tests

`tests/testCtf.py` covers:
- **The triazine block:** its geometry and typing, and that it's planar.
- **The recipes:** shipped, valid and sized (60 Å, 8 CPUs, a 0.3 Å Poreblazer grid).
- **Joins:** every join has a single-bond length, and CTF-1 is strictly alternating.
- **Parameters:** every angle and dihedral any join can create is in `gaff_ctf`.
- **Scaled-down builds** of each recipe (a 30 Å cell, two passes):
  - each ends as one framework;
  - each join is placed at its r0;
  - with HOOMD-blue, after optimisation, every join is within 0.05 Å of r0, and the
    angles are within 10° at triazine carbons (116.6°), benzene carbons (120°) and along
    alkynes (180°).
