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
3. **Closing:** three rounds of a wide zip and an all-atom optimisation (`docs/closing.md`),
   which close rings, including across the periodic boundary.
4. **Poreblazer** on a 0.3 Å grid, about 340 MB for this cell. The default 0.2 Å grid
   would need about 1 GB.

**Resources:** 8 CPUs, 8 GB and 8 hours, for the Slurm agent.

**Why join:** grow extends each seed's own cluster, and zip only bonds free ends that
happen to meet. Without joins, the first versions of these recipes ended as 30–40
separate clusters, one per seed, none bonded to another. Two joins per pass, which move
whole clusters to bond them to each other, turn the seeds' clusters into one or two large
frameworks. Fewer seeds with more growth each needs fewer joins; joins are the slow part of
a build.

**Why close:** joins and growth never close a ring, so without the closing phase the
frameworks were trees. None was bonded to its own periodic image, so none spanned the cell,
despite reaching across it.

**Going larger:** sweep or campaign the cell (`/cell/box`), the grow count, or the number
of passes. Poreblazer's memory grows with the cell's volume over the grid spacing cubed
(`ambuild.ab_poreblazer.memory_estimate_mb`): an 80 Å cell at 0.3 Å needs about 710 MB.
HOOMD-blue across several MPI tasks helps only for all-atom calculations
(`docs/benchmarks.md`).

## Measured builds

Full builds with the closing phase, on 5 CPUs (local Docker, HOOMD-blue 7.2, Poreblazer
with 8 threads). Joins and angles are measured by nearest image.

| Recipe | Time | Atoms | Frameworks | Rings between blocks | Spans the cell | Worst join vs r0 | Worst junction angle | Density (g/cm³) | PLD (Å) | Largest pore (Å) | Pores span | Surface (m²/g) | Helium volume (cm³/g) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ctf1_large` | 11.5 min | 5,235 | 1 | 8 | x, y, z | 0.017 Å | 11.6° | 0.36 | 10.4 | 23.6 | 1 direction | 4,740 | 2.28 |
| `ctf_alkyne_large` | 0.7 min | 2,083 | 1 | 5 | y, z | 0.013 Å | 17.2° (alkyne) | 0.19 | 19.4 | 27.0 | 1 direction | 8,420 | 4.90 |
| `ctf_mixed_large` | 5.3 min | 3,932 | 1 | 29 | x, y, z | 0.032 Å | 18.4° (alkyne) | 0.27 | 12.1 | 21.8 | 1 direction | 6,731 | 3.20 |

**π conduction** (liminal, calibrated: `docs/conduction.md`):

| Recipe | Resistor network, mean (weakest axis), g0 | By tunnelling | Coherent T, log10, 300 K: mean (weakest) | Gap (eV) |
| --- | --- | --- | --- | --- |
| `ctf1_large` | 0.021 (0.012) | 0% | −30.2 (−31.9) | 3.67 |
| `ctf_alkyne_large` | 0.013 (0, along x) | 0% | −20.0 (x not spanned) | 3.31 |
| `ctf_mixed_large` | 0.060 (0.053) | 0% | −31.2 (−38.2) | 2.82 |

- **Connectivity:** closing merges each build into one framework that spans the cell (the
  alkyne one along two axes). Before closing, `ctf1_large` and `ctf_mixed_large` were two
  trees each, and none spanned it.
- **Geometry:** joins stay within 0.032 Å. Alkyne carbons bend up to 17–18° from linear at
  the junctions, the price of closing rings through rod-like struts.
- **Conduction is fully conjugated** (no sp³ nodes). The mixed recipe, with the most rings,
  conducts best as a resistor network. The alkyne one, with the fewest π sites between
  faces, transmits best coherently.
- **Pores:** all three have pores wide enough for most small molecules, with percolating
  networks. The alkyne struts give the most open structure.
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
