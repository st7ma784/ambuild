# Porous aromatic frameworks, graphyne and graphdiyne

Two more families of large carbon networks, each built in a 60 Å cell like the covalent
triazine frameworks (`docs/ctf-networks.md`), with the same build scheme:
- **Seed and grow:** 8 seeds, then ten passes of grow 60, join 2, zip and a rigid-body
  optimisation with dihedrals.
- **Measure:** Poreblazer on a 0.3 Å grid.
- **Resources:** 8 CPUs, 8 GB, 8 hours.

**All four recipes close their networks.** After the passes they add a closing phase:
three rounds of a wide zip and an all-atom optimisation (`docs/closing.md`). That closes
rings, lets the framework span the cell, and lets the blocks flex. The two PAF recipes also
run a `conduction` stage after Poreblazer (`docs/conduction.md`).

Each recipe allows only the joins of its own topology.

## The blocks

| Block | Unit | Links | Typing | Geometry |
| --- | --- | --- | --- | --- |
| `carbon_tetrahedral` | a single sp³ carbon | 4, tetrahedral (end group `q`) | `c3`, `hc` caps | C–H 1.09 Å |
| `adamantane` | C₁₀H₁₆ cage | 4, at the bridgehead carbons (`q`) | `c3`, `hc` | C–C 1.54, C–H 1.09 Å |
| `biphenyl` | 4,4′-biphenyl | 2, at the outer para carbons (`b`) | `cp` linking and inter-ring, `ca`, `ha` | ring C–C 1.397, inter-ring 1.4854 Å, twist 44.4° (gas phase) |
| `butadiyne` | H–C≡C–C≡C–H | 2, at the end carbons (`d`) | `cg≡ch–ch≡cg` | C≡C 1.217, C–C 1.384 Å (gas-phase electron diffraction) |

The other blocks (`benzene_135`, `acetylene_cg`) are described in `docs/carbon-linkers.md`.

## The recipes

| Recipe | Blocks | Joins | Topology |
| --- | --- | --- | --- |
| `paf1_large` | tetrahedral carbon, biphenyl | node–biphenyl only (`c3–cp`, 1.516 Å) | PAF-1: C–(C₆H₄)₂–C, diamond-like, made amorphous |
| `paf_adamantane_large` | adamantane, biphenyl | node–biphenyl only | a larger tetrahedral node than PAF-1's, for larger cages |
| `graphyne_large` | 1,3,5-benzene, alkyne | ring–alkyne only (1.433 Å) | graphyne: ring–C≡C–ring |
| `graphdiyne_large` | 1,3,5-benzene, butadiyne | ring–butadiyne only (1.433 Å) | graphdiyne: ring–C≡C–C≡C–ring, exactly two alkynes per strut |

The strut blocks are what make these topologies exact. If phenylenes could join
phenylenes, or alkynes alkynes, the struts would come in every length. `biphenyl` and
`butadiyne` fix them at two rings and two alkynes.

Parameters:
- PAF recipes: `params/gaff_paf` (its README gives sources and choices).
- Graphyne and graphdiyne: `params/gaff_carbon`.

## Measured builds

Each recipe was run once in full, with HOOMD-blue 4 (`ambuild:test` image). Pores are from
Poreblazer, and joins and angles are measured by nearest image.
All with the closing phase, on 5 CPUs; the PAFs also ran their conduction stage.

| Recipe | Time | Atoms | Frameworks (largest, atoms) | Rings between blocks | Spans the cell | Density g/cm³ | PLD Å | Largest pore Å | Pores percolate in | Surface m²/g | He volume cm³/g | Largest join stretch Å |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `paf1_large` | 10.2 min | 7,011 | 2 (4,260) | 1 | no | 0.390 | 8.6 | 18.9 | 1 direction | 4,587 | 1.98 | 0.033 |
| `paf_adamantane_large` | 21.8 min | 11,752 | 1 | 11 | x, y, z | 0.609 | 5.2 | 13.2 | 1 direction | 1,976 | 1.01 | 0.036 |
| `graphyne_large` | 0.9 min | 3,518 | 1 | 6 | x, z | 0.228 | 12.7 | 19.1 | 1 direction | 8,058 | 3.86 | 0.008 |
| `graphdiyne_large` | 1.3 min | 4,056 | 1 | 8 | x, y, z | 0.283 | 11.1 | 17.7 | 1 direction | 7,039 | 2.97 | 0.007 |

**Graphyne and graphdiyne, π conduction** (liminal, calibrated; measured on the built
structures, as the recipes have no conduction stage):

| Recipe | Resistor network, mean (weakest axis), g0 | Coherent T, log10, 300 K | log10 T exactly at E_F | Gap (eV) |
| --- | --- | --- | --- | --- |
| `graphyne_large` | 0.015 (0, along y) | −55.5 (y not spanned) | −183 | 3.54 |
| `graphdiyne_large` | 0.042 (0.031) | −33.6 (−38.1) | −110 | 3.39 |

Both are made of meta-linked 1,3,5-benzenes. Coherent transmission shows a sharp
destructive-interference dip exactly at the Fermi level, which the resistor network can't
see. Even at 300 K they transmit far less than the para-linked sp³-node network of
`tpm_phenylene_large` (−18.0; `docs/sp3-sp2-networks.md`). Alkyne carbons bend up to
14–19° from linear at the junctions after closing.

**π conduction of the PAFs** (liminal, calibrated: `docs/conduction.md`):

| Recipe | π sites | sp³ bridges | Conjugated domains (largest) | Gap eV | Resistor network, g0: mean (weakest axis) | By tunnelling | Coherent T, log10, 300 K: mean (weakest) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `paf1_large` | 3,660 | 441 | 305 (12 sites) | 4.21 | 0: no framework spans the cell | – | – |
| `paf_adamantane_large` | 3,996 | 552 | 333 (12 sites) | 3.80 | 0.0056 (0.0026) | 100% | −19.4 (−21.7) |

Notes:
- **PAF-1 hardly closes:** one ring in the closing phase. Its free ends are mostly on the
  single-carbon nodes, whose tetrahedral caps rarely point at a partner even within 3 Å
  and 110°. Its two frameworks stay trees, so it can't conduct across the cell.
  - `tpm_phenylene_large` (`docs/sp3-sp2-networks.md`) builds the same kind of net from
    pre-built tetraphenylmethane nodes. Their free ends are ring carbons, and it closes
    into one spanning framework.
- **PAF-1's density and surface:** the amorphous build is denser than crystalline PAF-1's
  diamond net (about 0.32 g/cm³), with shorter, tangled strands filling the cell. Its surface
  area of 4,587 m²/g is below PAF-1's reported BET of about 5,600.
- **The adamantane PAF closes into one framework spanning the cell.** Its conduction is all
  tunnelling, and weaker than `tpm_phenylene_large`'s (0.0056 against 0.019 g0; −19.4
  against −18.0 coherently). Each hop crosses a chain of three sp³ atoms
  (ring–C–CH₂–C–ring) instead of one.
  - Its larger node didn't give larger pores. It fitted more blocks into the cell (213
    nodes, 333 biphenyls), so the result is denser, with narrower pores.
- **One run each:** these are single builds, not averages; a sweep over seeds gives the
  spread.
- **Before the self-image fix,** `paf1_large` failed in pass 3. A cluster about 90 Å across overlapped its own periodic image in the 60 Å cell (`docs/CHANGELOG.md`, Fixed).

## Tests

`tests/testCarbonFamilies.py` covers:
- **The blocks:** each one's geometry and end groups.
- **The recipes:** each allows only its own topology's joins.
- **Parameters:** every angle and dihedral any join can create has one.
- **Scaled-down builds** (30 Å, two passes):
  - each is one framework, with only the expected joins, placed at r0;
  - with HOOMD-blue, after optimisation, joins are within 0.05 Å of r0 (0.08 for
    adamantane, see below), and the angles are within 10° of their ideal.
- **The gallery:** every example recipe is in exactly one family.

**Adamantane crowding:** in the rigid passes of `paf_adamantane_large`, the joins end up
0.03–0.075 Å longer than r0, with a median of about 0.037. The cage's CH₂ hydrogens and the
phenyl's ortho hydrogens are 1.9–2.2 Å apart, inside H···H contact, and rigid blocks can't
flex to relieve it (`params/gaff_paf/README.md`). The closing phase's all-atom optimisation
lets them flex: in the full build the worst join is then 0.036 Å long, down from 0.075.
The scaled-down test keeps its 0.08 Å tolerance, since its builds are small and quickly
closed.

## The gallery

The web GUI's **Gallery** page (`/gallery`, and `GET /api/gallery`) lists every example
recipe by family: Li-ion carbon networks, carbon linkers, graphyne and graphdiyne, CTFs,
and PAFs. `ambuild/gallery.json` defines the families.

For each recipe it shows:
- its blocks, joins, cell and build;
- what it measures;
- the median pore limiting diameter, surface area and density of its latest finished
  runs;
- **Run**, **Sweep** and **Campaign** links, which start from the recipe saved under the
  same name. `deploy/demo/seed_recipes.py` saves the examples.

Example campaigns for a family link to the New campaign page with that spec
(`/campaigns/new?spec=ion_sieve`).
