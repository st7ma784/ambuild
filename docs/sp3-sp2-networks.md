# sp3–sp2 networks

Networks where every sp³ carbon is a node with four sp² arms. Structures like these can
conduct about as well as semiconductors, even though their sp³ nodes break the
conjugation between arms. That suggests charge crosses the nodes by tunnelling. So besides
their pores, these recipes measure the π network's gap and conductance, with and without
tunnelling through the sp³ centres (liminal's `conduct`; see `docs/conduction.md`).

## The node

`tetraphenylmethane`: C(C₆H₄)₄, linked at the four para carbons (end group `t`).

| | |
| --- | --- |
| Typing | `c3` centre; `cp` at the ipso and para carbons; `ca`, `ha` |
| Geometry | tetrahedral at the centre; centre–ipso 1.5156 Å (GAFF `c3–ca` r0); rings regular, C–C 1.397 Å, C–H 1.084 Å |
| Arms | related by S₄ about z, the molecule's symmetry in its crystal. The propeller twist, 33.5° from the plane holding the arm and the S₄ axis, is the one that keeps ortho hydrogens on different arms furthest apart: 2.44 Å, at van der Waals contact |

The node is pre-built, unlike PAF-1's single sp³ carbon (`carbon_tetrahedral`), which only
gains its phenyls when they join. That guarantees every sp³ centre has four sp² arms. A
PAF-1 build ends with some of its nodes' four ends still capped with hydrogen: in the full
`paf1_large` build, 610 end groups stayed free. With tetraphenylmethane, an unjoined arm is
still a phenyl ring.

## The recipes

| Recipe | Blocks | Joins | Parameters |
| --- | --- | --- | --- |
| `tpm_phenylene_large` | tetraphenylmethane, 1,4-phenylene | node arm–phenylene only (`cp–cp`, 1.485 Å): struts C–(C₆H₄)₃–C | `gaff_paf` |
| `tpm_sp2_network_large` | tetraphenylmethane, 1,4-phenylene, 1,3,5-benzene, trigonal sp² carbon (`carbon_node`) | node arms to all of them and to each other, and every sp²–sp² join among them (ten kinds) | `gaff_carbon` |

Neither allows an sp³–sp³ join: the only sp³ atoms are the node centres, inside their
blocks.

Both build like the other large recipes (`docs/carbon-families.md`), in a 60 Å cell:
- **Seed and grow:** 8 tetraphenylmethane seeds, then ten passes of grow 60, join 2, zip
  and a rigid-body optimisation with dihedrals.
- **Close:** three rounds of a wide zip and an all-atom optimisation (`docs/closing.md`).
  This closes rings, lets the framework span the cell, and lets the blocks flex. Without it
  the builds were trees, which can't conduct across the cell.
- **Measure:** Poreblazer on a 0.3 Å grid, then a `conduction` stage.
- **Resources:** 8 CPUs, 8 GB, 8 hours.

The `conduction` stage needs liminal (`LIMINAL_EXE`, or `liminal` on the PATH; the demo's
`ambuild-agent-liminal` image has it).

**The strict recipe** is the clean case. Every conjugated domain is one node's arms plus the
struts to its neighbours, cut at each sp³ centre. So all conduction is tunnelling.

**The network recipe** gives conjugated sp² domains of varying size and branching, which
can percolate between nodes. There are two things to watch:
- **Radical centres:** a trigonal carbon joined to three aryl groups is a trityl-like
  radical centre. Two in one domain make a diradical, whose frontier levels are degenerate.
  liminal counts these as radical domains (`el_radical_domains`).
- **Stretched joins:** the three aryl groups crowd each other's ortho hydrogens, and rigid
  blocks can't twist to relieve it. So in the rigid passes trigonal-node joins stretch up to
  0.06 Å; other joins stay within 0.03 Å. The closing phase's all-atom optimisation lets them
  relax.

## Measured builds

Each recipe was run once in full, on 5 CPUs with HOOMD-blue 4 and liminal 0.0.1 (C0), in
the `ambuild:test` image with liminal added. Joins and angles are measured by nearest
image after the closing phase.

**Structure and pores:**

| Recipe | Time | Atoms | Frameworks | Rings between blocks | Spans the cell along | Density g/cm³ | PLD Å | Largest pore Å | Pores percolate in | Surface m²/g | He volume cm³/g | Largest join stretch Å | Largest angle deviation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `tpm_phenylene_large` | 22.7 min | 9,714 | 1 | 8 | x, y, z | 0.559 | 6.1 | 13.6 | 1 direction | 2,567 | 1.18 | 0.025 | 6.5° |
| `tpm_sp2_network_large` | 25.4 min | 7,951 | 1 | 83 | x, y, z | 0.463 | 6.9 | 15.0 | 1 direction | 3,867 | 1.61 | 0.051 (node–node) | 12.6° (trigonal carbon) |

**π conduction** (liminal C0):

| Recipe | π sites | sp³ bridges | Conjugated domains (largest) | Radical domains | Gap eV | Conductance g0: mean (weakest axis) | Without tunnelling | By tunnelling |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `tpm_phenylene_large` | 5,574 | 876 | 431 (18 sites) | 0 | 3.58 | 0.0073 (0.0023, y) | 0 | 100% |
| `tpm_sp2_network_large` | 4,639 | 642 | 241 (355 sites) | 50 (41 open shell) | 0 | 0.044 (0.038, x) | 0 | 100% |

What these show:
- **Both conduct only by tunnelling through the sp³ nodes.** No conjugated domain spans
  the cell. The strict recipe's domains are single node-to-node struts (18 sites: three
  rings). This is the picture of conduction through broken conjugation, as a number to
  optimise.
- **The sp² network conducts six times better.** Its domains are bigger (up to 355 sites),
  so fewer tunnelling steps are needed. But it is full of radical centres, trigonal carbons
  with three aryl arms, which make the Hückel gap zero. The example campaign asks for none
  (`el_radical_domains` ≤ 0), so it will push towards fewer trigonal carbons.
- **Without the closing phase both were trees.** Four frameworks (phenylene) and two (sp²
  network), none spanning the cell, both with conductance 0. Pores barely change with
  closing: the strict recipe's PLD was 6.0 Å and its surface 2,600 m²/g before.
- **Geometry:** the sp² network's 83 closures leave junction angles up to 12.6° from ideal
  at trigonal carbons. The strict recipe's 8 stay within 6.5°.

## Aiming at conduction

The example campaign `semiconducting_sp3_sp2` (for `tpm_sp2_network_large`):
- **Varies:** the cell size (40–60 Å), the grow count (30–80) and the passes (5–10).
- **Maximises:** `el_conductance`, the mean over the three axes.
- **Requires:** no radical domains, and pores that percolate in at least one direction.

A gap constraint (`el_gap`) can be added, but it is a Hückel gap, uncalibrated: benzene's
is 5.7 eV. Use it to compare structures, not as a band gap to hit.

## Tests

`tests/testSp3Sp2.py` covers:
- **The node:** its composition, the tetrahedral centre, regular rings, links at the para
  carbons, and ortho H···H of at least 2.4 Å.
- **The recipes:** their topology and stages; every angle and dihedral their joins can make
  has parameters; the example campaign fits the network recipe.
- **Scaled-down builds:** only the intended joins, none at an sp³ carbon, each sp³ carbon
  keeping four `cp` neighbours, joins at r0. With HOOMD-blue, geometry after optimisation.
- **With liminal installed:** conduction on those builds.
  - Each node gives exactly six sp³ bridges, its four arms taken in pairs.
  - Every carbon but the node centres is a π site.
  - Graphyne has no bridges and is one domain.

`tests/testConduction.py` tests the stage with a stand-in for liminal.
