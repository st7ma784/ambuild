# Carbon linkers that join themselves and each other

Two example recipes build carbon networks from linkers that can join their own kind as
well as each other: rings to rings, alkynes to alkynes (polyynes), nodes to nodes. Mixed
joins give longer struts and more branching, and so larger, more open networks than
`li_ion_carbon`'s ring–alkyne alternation.

## The blocks

`tests/blocks` (installed as Ambuild's blocks). Every linking carbon is typed for GAFF 1.81
(`ambuild/recipes/params/gaff_carbon`, whose README gives each term's source):

| Block | Unit | Links | Typing | Geometry |
| --- | --- | --- | --- | --- |
| `benzene_135` | C6 ring | 3 (1,3,5), end group `a` | `cp` linking, `ca` ring | C–C 1.397 Å |
| `acetylene_cg` | C2 alkyne | 2, end groups `g` and `h` | `cg≡ch`, GAFF's conjugated sp carbons | C≡C 1.203 Å |
| `carbon_node` | trigonal sp² carbon | 3 at 120°, end group `a` | `ca` | one carbon |
| `allene` | C3 cumulene, H(R)C=C=C(R)H | 2, end group `e` | `ce=c1=ce` | C=C 1.308 Å, end planes perpendicular |
| `cyclopropenyl` | C3 ring | 3, radially (150°), end group `u` | `cu` | C–C 1.363 Å, as aromatic C₃H₃⁺ |
| `propargyl` | –CH₂–C≡C– | 2, end groups `c` (CH₂) and `h` (alkyne) | `c3–cg≡ch` | CH₂–C 1.458, C≡C 1.203 Å |

(`acetylene`, typed `c1`, stays as it was for `li_ion_carbon`. `acetylene_cg` is the one
that can join other alkynes.)

## Joins, and their lengths

With typed bond lengths, a grown block is placed at its join's GAFF r0:

| Join | r0 (Å) |
| --- | --- |
| ring–ring (biaryl, `cp–cp`) | 1.485 |
| ring–alkyne, node–alkyne (`ca/cp–cg/ch`) | 1.433 |
| alkyne–alkyne (polyyne single bond, `cg–cg`, `ch–ch`) | 1.369 |
| node–node (`ca–ca`) | 1.398 |
| node–ring (`ca–cp`) | 1.406 |
| allene–ring, cyclopropenyl–ring or node | 1.476 |
| allene–alkyne, cyclopropenyl–alkyne | 1.427–1.431 |
| allene–allene | 1.457 |
| propargyl CH₂–ring or node | 1.516 |
| propargyl CH₂–alkyne | 1.467 |

Three joins would get the wrong bond from GAFF, so the recipes leave them out:
- **An alkyne's `cg` end to another's `ch` end.** It would take the triple bond's length
  (1.206 Å). Alkynes join like end to like end instead: `B:g-B:g`, `B:h-B:h` and
  `B:h-F:h` (propargyl), but not `B:g-B:h` or `B:g-F:h`.
- **Cyclopropenyl to cyclopropenyl** (GAFF `cu–cu`, 1.30 Å).
- **Allene to cyclopropenyl** (`ce–cu`, 1.307 Å). Both of these are double-bond lengths.

## The recipes

- **`carbon_nodes_network`:** benzene rings (C6), alkynes (C2) and trigonal sp² carbon
  nodes, with all nine joins among them.
  - **Build:** 6 rings and 6 nodes are seeded in a 30 Å cell. Six passes follow, each a
    grow, a zip and a rigid-body optimisation with dihedrals; then Poreblazer.
  - **What forms:** every kind of join. Without its optimisations it builds 328 atoms (17
    rings, 32 alkynes, 35 nodes); with them, 360.
- **`carbon_all_linkers`:** all six blocks, with 32 kinds of join. It seeds rings, nodes
  and cyclopropenyl nodes.

Both are sweepable like any recipe. The cell size, seeds and grow counts are the usual
parameters for aiming at larger networks, e.g. in a campaign.

## How well the geometry holds

`tests/testCarbonLinkers.py` covers:
- **The blocks:** their geometry and end groups.
- **The recipes' joins:** every join the recipes allow has a single-bond length between
  1.3 and 1.55 Å, and none of the three wrong ones is allowed.
- **Parameters:** every bond, angle and dihedral any allowed join can create has a
  parameter, so HOOMD-blue never stops on a missing one.
- **Placement:** each recipe builds with its joins placed at r0 (the nodes network makes
  all nine kinds).
- **Optimisation**, with HOOMD-blue: each full recipe keeps every join within 0.05 Å of
  r0, and the angles at the joined atoms within 10° of their ideal. The ideal is 120 at
  ring and node carbons, 180 along alkynes, 148 exocyclic at the cyclopropenyl, about 120
  at allene ends and 109.5 at the propargyl CH₂.

Measured with HOOMD-blue 7.2:

| Recipe | Joins | Worst length vs r0 (Å) | Worst angles |
| --- | --- | --- | --- |
| `carbon_nodes_network` | 72 | 0.024 | node 9.1°, ring 7.6°, alkyne 1.8° |
| `carbon_all_linkers` | 72 | 0.009 | allene 5.5°, ring 3.9°, node 3.2°, cyclopropenyl 2.6° |

A trigonal node surrounded by other blocks is the most strained, but still within the
10° test.

## Limits

- **Node bonds:** the node's bonds to rings (1.406 Å) are shorter than a triarylmethyl's
  single bonds (about 1.47 Å). The typing treats nodes as graphitic, conjugated carbon.
- **Cyclopropenyl charge:** the ring is neutral here, though the aromatic C₃H₃ unit is a
  cation.
- **Allene stand-ins:** its junction angles are stand-ins (`c1-c2-ha`, 120.4°), since GAFF
  has no allene–aryl terms.
- **Stand-ins in general:** see the parameter set's README for every one.
