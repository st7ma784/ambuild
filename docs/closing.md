# Closing a network

Ambuild grows a network from rigid blocks. Each new block bonds to one free end group, and
join steps bond whole clusters to each other. Neither step closes a ring: that is zip's
job. Zip bonds free end groups that are already close, within the bond length plus a margin
and pointing at each other within an angle margin.

## What the large builds were

With the usual zip margins (1.0 Å, 30°), zip almost never bonds anything in these builds.
Rigid blocks leave free ends near each other but badly aligned: in a graphyne build, the
closest allowed pairs were 2.5–3.8 Å apart, with one end pointing 80–120° away. So the
60 Å builds were trees:

| Build | Blocks placed | Joins | Frameworks | Rings (joins − blocks + frameworks) |
| --- | --- | --- | --- | --- |
| `graphyne_large` | 608 | 607 | 1 | 0 |
| `paf1_large` | 608 | 606 | 2 | 0 |
| `tpm_phenylene_large` | 491 | 487 | 4 | 0 |
| `tpm_sp2_network_large` | 608 | 606 | 2 | 0 |

A tree can't reach its own periodic image. So each framework was a large finite molecule
in a periodic cell, not a periodic network. That doesn't matter for Poreblazer, which
measures the space between atoms. It does matter for anything that needs a continuous
path through the solid, such as conduction (`docs/conduction.md`) or mechanical
properties. Real frameworks are full of rings.

## The closing phase

After the growth passes, every large carbon recipe repeats this three times: the CTFs,
graphyne and graphdiyne, the PAFs, the sp³–sp² networks, and the two carbon-linker
networks.

    {"op": "zip", "bond_margin": 3.0, "bond_angle_margin": 110, "clash_check": true}
    {"op": "optimise", "cycles": 20000, "rigid_body": false, "do_dihedral": true}

- **The zip:** it accepts free end groups up to 3 Å beyond the bond length, pointing up to
  110° off. It rejects bonds that pass through other atoms (the clash check) and bonds that
  would close a three-membered ring.
- **The all-atom optimisation:** unlike the rigid-body passes, it lets bond angles and
  dihedrals inside blocks bend. That pulls the new bonds to their lengths, and it also
  relieves crowding the rigid passes can't: the adamantane PAF's CH₂/ortho-H contacts, and
  the three aryl arms around a trigonal carbon.

**Tested on scaled-down builds** (30 Å cells):

| Build | Before | After | Joins after | Angles after |
| --- | --- | --- | --- | --- |
| graphyne | a tree, not wrapping | 5 rings; wraps along x, y and z; conducts 0.059 g0 | within 0.014 Å of r0 | within 13° of ideal |
| adamantane PAF | a tree; joins stretched up to 0.059 Å | 1 ring; wraps along z; conducts 3.5×10⁻⁴ g0, all by tunnelling | within 0.027 Å | within 4° |

A few closures are enough, when some cross the boundary, to make the framework span the
cell.

**In the full 60 Å builds:**

| Recipe | Frameworks before → after | Rings | Spans the cell | Largest join stretch | Largest angle deviation | Conductance g0 |
| --- | --- | --- | --- | --- | --- | --- |
| `tpm_phenylene_large` | 4 → 1 | 8 | x, y, z | 0.039 → 0.025 Å | 6.5° | 0 → 0.019 |
| `tpm_sp2_network_large` | 2 → 1 | 83 | x, y, z | 0.060 → 0.051 Å | 12.6° | 0 → 0.096 |
| `paf_adamantane_large` | 4 → 1 | 11 | x, y, z | 0.075 → 0.036 Å | 7.7° | 0 → 0.0056 |
| `paf1_large` | 2 → 2 | 1 | no | 0.065 → 0.033 Å | 8.1° | 0 → 0 |

| `ctf1_large` | 2 → 1 | 8 | x, y, z | 0.020 → 0.017 Å | 11.6° | 0 → 0.021 |
| `ctf_mixed_large` | 2 → 1 | 29 | x, y, z | 0.019 → 0.032 Å | 18.4° | 0 → 0.060 |
| `ctf_alkyne_large` | 1 → 1 | 5 | y, z | 0.002 → 0.013 Å | 17.2° | 0 → 0.013 |
| `graphyne_large` | 1 → 1 | 6 | x, z | 0.004 → 0.008 Å | 14.4° | 0 → 0.015 |
| `graphdiyne_large` | 2 → 1 | 8 | x, y, z | 0.004 → 0.007 Å | 18.9° | 0 → 0.042 |
| `carbon_nodes_network` (30 Å) | 2 → 2 | 1 | no | 0.027 Å | 10.1° | 0 |
| `carbon_all_linkers` (30 Å) | 7 → 7 | 1 | no | 0.010 Å | 6.3° | 0 |

(Conductance: the resistor network, in g0, with liminal's calibrated couplings.) The 30 Å linker networks are too small, and their free ends too few, to close
into one framework.

**PAF-1 barely closes.** Its free ends are mostly on single-carbon nodes, whose tetrahedral
caps rarely point at a partner. Pre-built nodes with ring-carbon ends close far more
easily.

## Bugs this uncovered, now fixed

- **The export's bond images** (`Cell.writeTopology`): a bond zipped to the block's own
  periodic image joins atoms a cell apart in the block's continuous coordinates. The export
  wrote that far vector, a 40 Å "bond", instead of the real one. Summed around a ring,
  those vectors always cancel, so no exported framework could ever wrap. It now writes the
  nearest image.
- **Three-membered rings from one zip pass:** an atom with several end groups, such as a
  trigonal carbon, could bond in one pass to two atoms that are bonded to each other. HOOMD-
  blue then fails ("The same particle can only occur once in a dihedral"). Zip now leaves
  such a bond out.
- **Zip's clash check and misaligned bonds** (`Cell.bondClash`): it counted the bond atoms'
  own neighbours when they projected onto the bond. For a well-aligned bond they sit behind
  its ends, but with end groups pointing well off the bond they always count. So in the
  first full build with the closing phase, the check rejected all 9 candidates every round.
  It now skips atoms bonded to the bond's atoms; a third block's atom on the bond still
  counts (`testCarbonLinkers.ZipRings`).
- **joinBlocks and wrapped blocks:** a block bonded to its own image spans the cell, and
  moving it would stretch those bonds. joinBlocks now moves the other block, or picks again.
- **Growing along the xy plane** (`Block.flip`): a bond direction with no z component
  divided by zero.
- **Zip's neighbour grid** binned z by the y coordinate. It still found every pair, just
  with more checks than needed.

## Using it

Add the closing phase to any recipe after its growth passes. Larger margins close more
rings, but they need the flexible optimisation to repair the geometry, and each round
costs an all-atom optimisation of the whole cell.

- **Check the geometry:** the tests' join and angle checks after optimisation are the
  guard. Measure joins by nearest image (`tests/testCarbonLinkers.py`: `length`,
  `anglesAt(..., cell)`); raw block coordinates read about 40 Å for a bond through the
  boundary.
- **Check that it wraps:** liminal's `conduct` reports the conjugated domains that
  percolate, and a conductance above zero needs a framework that spans the cell.
