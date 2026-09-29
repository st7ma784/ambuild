# GAFF 1.81 parameters for carbon linkers that join themselves and each other

Force-field parameters for the building blocks of the `carbon_nodes_network` and
`carbon_all_linkers` example recipes (`docs/carbon-linkers.md`):

| Type | Atoms | Blocks |
| --- | --- | --- |
| `ca` | aromatic carbon; the trigonal sp² node | `benzene_135`, `carbon_node` |
| `cp` | aromatic carbon linked to another block | `benzene_135` |
| `cg`, `ch` | sp carbons in conjugated systems: `cg≡ch` is the triple bond; `cg–cg` and `ch–ch` are single bonds (1.3693 Å), as between the alkynes of a polyyne | `acetylene_cg`, `propargyl` |
| `c1` | sp carbon: the allene's central carbon | `allene` |
| `ce` | inner sp² carbon of a conjugated system: the allene's end carbons | `allene` |
| `cu` | sp² carbon of a three-membered ring | `cyclopropenyl` |
| `c3` | sp³ carbon | `propargyl` |
| `ha`, `hc` | hydrogen on an sp²/sp carbon, and on an sp³ carbon | all |

The values come from GAFF 1.81 (Wang et al., J. Comput. Chem. 25, 1157-1174, 2004), as
distributed in OpenMM's force-field project. The source is the file
`openmmforcefields/ffxml/amber/gaff/ffxml/gaff-1.81.xml` at commit
3f73b6ff9730e4e356fc6a0dbde6039517d59412 (sha256
b534eb703e3c8952e121f3a6e703ec7c09156559c5068603859c0635d6f88756), itself generated from
AmberTools 24.8's `gaff-1.81.dat`. They are generated, not edited:

    python scripts/gaff_params.py gaff-1.81.xml ambuild/recipes/params/gaff_carbon \
      ca ha cp c1 cg ch ce cu c3 hc \
      --alias=cp=ca --alias=cg=c1 --alias=ch=c1 --alias=ce=c2 --alias=cu=ce \
      --angle=cu-cu-cu:cu-cu-cx --angle=cu-cu-*:cu-cu-ha --angle=c1-ce-*:c1-c2-ha \
      --angle=*-ca-*:ca-ca-ca --angle=*-c3-*:c3-c3-c3 --angle=cg-ch-*:cg-ch-ch --angle=ch-cg-*:ch-cg-cg \
      --dihedral=*-ca-cu-*:X-cp-cp-X --dihedral=*-cp-cu-*:X-cp-cp-X --dihedral=*-ce-cu-*:X-cp-cp-X \
      --dihedral=*-cg-cu-*:X-c1-c1-X --dihedral=*-ch-cu-*:X-c1-c1-X --dihedral=*-c3-cu-*:X-c2-c3-X

Exact GAFF terms are used wherever GAFF has them. Every stand-in is named in its row's
comment. `tests/testCarbonLinkers.py` checks that every bond, angle and dihedral the
recipes' joins can create is present.

## Stand-ins, and why

- **Types GAFF has few terms for:**
  - `cp`: as `ca`, as in `gaff_benzene_alkyne`.
  - `cg`, `ch`: as `c1`, e.g. for the hydrogen on a free alkyne end.
  - `ce`: as `c2`, e.g. the allene's H–C=C angle.
- **Cyclopropenyl bonds** to other blocks: `cu` as `ce`, giving single bonds between
  conjugated carbons (1.427–1.476 Å).
- **Angles that depend on the atom's role:**

  | Angle | Stand-in | Value |
  | --- | --- | --- |
  | cyclopropenyl, exocyclic (`cu-cu-*`) | `cu-cu-ha` | 147.7° |
  | cyclopropenyl ring (`cu-cu-cu`) | `cu-cu-cx` | 64.5°, within a rigid block |
  | at the allene's end carbons (`c1-ce-*`) | `c1-c2-ha` | 120.4° |
  | at the trigonal node (`*-ca-*`) | `ca-ca-ca` | 120° |
  | at the propargyl CH₂ (`*-c3-*`) | `c3-c3-c3` | tetrahedral |
  | along alkynes (`cg-ch-*`, `ch-cg-*`) | | linear |

- **Torsions about bonds to a cyclopropenyl:**
  - to a conjugated carbon: `X-cp-cp-X`, the biaryl torsion, which favours coplanar
    rings as conjugation does;
  - to an alkyne: `X-c1-c1-X`;
  - to the propargyl CH₂: `X-c2-c3-X`, nearly free rotation.
- **Multi-term torsions:** where GAFF has several Fourier terms for one torsion
  (`c3-c3-c3-c3`), the largest is kept, since Ambuild's dihedrals take one term.

## Choices

- **The trigonal node is typed `ca`,** as a carbon of a conjugated sp² network (graphitic
  carbon). Its bonds are 1.398 Å to another node and 1.406 Å to a ring's `cp`. A
  triarylmethyl's single bonds would be about 1.47 Å; a graphitic network's are about
  1.42 Å. The angles about it are 120°.
- **Alkynes join like end to like end.** `acetylene_cg`'s carbons are `cg` (end group `g`)
  and `ch` (end group `h`). The recipes allow `B:g-B:g` and `B:h-B:h`, but not `B:g-B:h`,
  so every alkyne–alkyne join is the `cg–cg` or `ch–ch` single bond, never `cg–ch`, GAFF's
  triple bond. The propargyl's alkyne end is a `ch` (`F:h`) and follows the same rule.
- **Joins left out of the recipes.** GAFF's own `cu–cu` (1.30 Å) and `ce–cu` (1.307 Å) are
  double bonds, so cyclopropenyl–cyclopropenyl and allene–cyclopropenyl joins are not in
  the recipes' bond types.
- **The cyclopropenyl ring carries no charge,** although the aromatic C₃H₃ unit is a cation;
  Ambuild's blocks carry none. Its C–C (1.363 Å) is the cation's.
- **No impropers**, as in `gaff_benzene_alkyne`: rigid blocks keep their own geometry.
- **Don't mix these files with other parameter sets.** The types mean what this table
  says, and `tests/params` uses `cp` differently.
