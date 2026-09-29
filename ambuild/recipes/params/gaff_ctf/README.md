# GAFF 1.81 parameters for covalent triazine frameworks

Force-field parameters for the building blocks of the `ctf1_large`, `ctf_alkyne_large` and
`ctf_mixed_large` example recipes (`docs/ctf-networks.md`):

| Type | Atoms | Blocks |
| --- | --- | --- |
| `cp` | aromatic carbon linked to another block | `triazine`, `benzene_135`, `benzene_14` |
| `ca` | aromatic carbon | `benzene_135`, `benzene_14` |
| `nb` | aromatic (pyridine-type) nitrogen | `triazine` |
| `cg`, `ch` | sp carbons in conjugated systems: `cg≡ch` is the triple bond; `cg–cg` and `ch–ch` are single bonds (1.3693 Å), as between the alkynes of a polyyne | `acetylene_cg` |
| `ha` | hydrogen on an aromatic or sp carbon | all |

The values come from GAFF 1.81, from the same source as `gaff_carbon` and
`gaff_benzene_alkyne`: openmmforcefields `gaff-1.81.xml` at commit
3f73b6ff9730e4e356fc6a0dbde6039517d59412, sha256
b534eb703e3c8952e121f3a6e703ec7c09156559c5068603859c0635d6f88756, itself from AmberTools
24.8's `gaff-1.81.dat`. They are generated, not edited:

    python scripts/gaff_params.py gaff-1.81.xml ambuild/recipes/params/gaff_ctf \
      ca ha cp nb cg ch --alias=cp=ca --alias=cg=c1 --alias=ch=c1 --angle=nb-cp-*:nb-cp-cp

Exact GAFF terms are used wherever GAFF has them, and every stand-in is named in its
row's comment:
- **`cp` as `ca`**, as in the other sets.
- **`cg` and `ch` as `c1`**, e.g. for the hydrogen on a free alkyne end.
- **Angles at a triazine carbon with its nitrogens and whatever it bonds to outside the
  ring** (`nb-cp-*`): GAFF's `nb-cp-cp` (116.6°), i.e. the exocyclic angle of a ring whose
  N–C–N is 126.8°. GAFF lacks `nb-ca-ha` for a free triazine C–H.

`tests/testCtf.py` checks that every bond, angle and dihedral the recipes' joins can
create is present.

Joins and their lengths:

| Join | Length (Å) |
| --- | --- |
| triazine–ring, triazine–triazine, ring–ring (`cp–cp`, biaryl) | 1.4854 |
| triazine or ring to alkyne (`cp–cg`, `cp–ch`) | 1.4328 |
| alkyne–alkyne (`cg–cg`, `ch–ch`) | 1.3693 |

An alkyne's `cg` end is never joined to another's `ch` end, which would be GAFF's triple
bond.
