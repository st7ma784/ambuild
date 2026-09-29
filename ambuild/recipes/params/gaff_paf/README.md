# GAFF 1.81 parameters for porous aromatic frameworks

Force-field parameters for the building blocks of the `paf1_large` and
`paf_adamantane_large` example recipes (`docs/carbon-families.md`):

| Type | Atoms | Blocks |
| --- | --- | --- |
| `c3` | sp³ carbon: the tetrahedral node, and adamantane's carbons | `carbon_tetrahedral`, `adamantane` |
| `hc` | hydrogen on an sp³ carbon | `carbon_tetrahedral`, `adamantane` |
| `cp` | aromatic carbon linked to another block or ring | `biphenyl` |
| `ca`, `ha` | aromatic carbon, and its hydrogen | `biphenyl` |

The values come from GAFF 1.81, from the same source as the other sets: openmmforcefields
`gaff-1.81.xml` at commit 3f73b6ff9730e4e356fc6a0dbde6039517d59412, sha256
b534eb703e3c8952e121f3a6e703ec7c09156559c5068603859c0635d6f88756, from AmberTools 24.8's
`gaff-1.81.dat`. They are generated, not edited:

    python scripts/gaff_params.py gaff-1.81.xml ambuild/recipes/params/gaff_paf c3 hc ca cp ha --alias=cp=ca

The only stand-in is `cp` as `ca`, as in the other sets. GAFF has every other term these
joins need, which `tests/testCarbonFamilies.py` checks.

**The join:** node to phenyl, `c3–cp` (GAFF `c3–ca`), is 1.5156 Å; the angles at the node
are GAFF's `ca–c3–ca`, 112.2°.

**Crowding at adamantane:** after optimisation the adamantane–phenyl joins are 0.03–0.06 Å
longer than r0. The cage's CH₂ hydrogens and the phenyl's ortho hydrogens are five to six
bonds apart, so they interact (Ambuild excludes only up to 1–4 pairs), and they end up
1.9–2.2 Å apart, inside H···H contact. The rigid blocks can't flex to relieve it, so the
join stretches.
