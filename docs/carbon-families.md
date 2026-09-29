# Porous aromatic frameworks, graphyne and graphdiyne

Two more families of large carbon networks, each built in a 60 Å cell like the covalent
triazine frameworks (`docs/ctf-networks.md`), with the same build scheme:
- **Seed and grow:** 8 seeds, then ten passes of grow 60, join 2, zip and a rigid-body
  optimisation with dihedrals.
- **Measure:** Poreblazer on a 0.3 Å grid.
- **Resources:** 8 CPUs, 8 GB, 8 hours.

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

Each recipe was run once in full, on 8–10 CPUs with HOOMD-blue 4 (`ambuild:test` image). Pores are from Poreblazer.

| Recipe | Time | Atoms | Frameworks (largest, atoms) | Density g/cm³ | PLD Å | Largest pore Å | Percolates in | Surface m²/g | He volume cm³/g | Largest join stretch Å |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `paf1_large` | 11.9 min | 7,013 | 2 (4,260) | 0.390 | 8.7 | 18.7 | 1 direction | 4,592 | 1.98 | 0.065 |
| `paf_adamantane_large` | 25.0 min | 11,780 | 4 (3,390) | 0.609 | 5.0 | 13.1 | 3 directions | 1,983 | 1.01 | 0.075 |
| `graphyne_large` | 0.7 min | 3,530 | 1 | 0.228 | 12.7 | 18.9 | 1 direction | 8,082 | 3.86 | 0.004 |
| `graphdiyne_large` | 8.4 min | 4,096 | 2 (2,602) | 0.284 | 10.2 | 18.0 | 1 direction | 6,763 | 2.95 | 0.004 |

Notes:
- **PAF-1:** 606 joins; the median is 0.006 Å from r0 and 5 are more than 0.05 Å from it.
  - The amorphous build is denser than crystalline PAF-1's diamond net (about 0.32 g/cm³): its frameworks fill the cell with shorter, tangled strands. Its surface area of 4,592 m²/g is below PAF-1's reported BET of about 5,600.
- **Adamantane PAF:** its larger node didn't give larger pores here. It fitted more blocks into the cell (213 nodes, 333 biphenyls), in four frameworks that interpenetrate. The result is denser, with narrower pores, though they percolate in all three directions.
  - Its joins are stretched by the crowding described below.
- **One run each:** these are single builds, not averages; a sweep over seeds gives the spread.
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

**Adamantane crowding:** in `paf_adamantane_large`, the joins end up 0.03–0.06 Å longer
than r0 after optimisation, with a median of 0.037. The cage's CH₂ hydrogens and the
phenyl's ortho hydrogens are 1.9–2.2 Å apart, inside H···H contact, and the rigid blocks
can't flex to relieve it (`params/gaff_paf/README.md`).

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
