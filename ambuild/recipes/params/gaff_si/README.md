# GAFF 1.81 with silicon from UFF

Force-field parameters for the tetraphenylsilane node of `tps_phenylene_large`
(`docs/sp3-sp2-networks.md`):

| Type | Atoms | Source |
| --- | --- | --- |
| `si` | the tetrahedral silicon node | bonds and Lennard-Jones from UFF; angles and dihedrals as GAFF `c3` |
| `cp` | aromatic carbon linked to another block or ring | GAFF (as `ca` where GAFF lacks a `cp` term) |
| `ca`, `ha` | aromatic carbon, and its hydrogen | GAFF |

GAFF 1.81 has no silicon. The aromatic terms come from GAFF 1.81, from the same source as
the other sets: openmmforcefields `gaff-1.81.xml` at commit
3f73b6ff9730e4e356fc6a0dbde6039517d59412, sha256
b534eb703e3c8952e121f3a6e703ec7c09156559c5068603859c0635d6f88756. They are generated, not
edited:

    python scripts/gaff_params.py gaff-1.81.xml ambuild/recipes/params/gaff_si si ca cp ha \
      --alias=cp=ca --alias=si=c3 \
      --bond=si-cp:451.1:1.87 --bond=si-ca:451.1:1.87 --bond=si-ha:338.9:1.48 --bond=si-si:276.1:2.35 \
      --lj=si:0.402:3.826

## Silicon

**Bonds** (`--bond`):
- **r0:** measured lengths. Si–C(aryl) is 1.87 Å, as in tetraphenylsilane's crystal. Si–H is
  1.48 Å and Si–Si 2.35 Å, typical values.
- **k:** UFF's rule (Rappé, Casewit, Colwell, Goddard and Skiff, J. Am. Chem. Soc. 114, 10024
  (1992)): k = 664.12 Z*_I Z*_J / r³ kcal/mol/Å², with Z* = 2.323 (Si3), 1.912 (C_R) and 0.712
  (H_). UFF writes the bond energy as k/2 (r − r0)², as Ambuild does, so k is used directly.

**Lennard-Jones** (`--lj`): UFF's Si3.
- ε = 0.402 kcal/mol.
- σ = x/2^(1/6) = 4.295/1.1225 = 3.826 Å. UFF's x is the distance at the minimum.
- Pairs with GAFF types follow Lorentz–Berthelot, like every other set.

**Angles and dihedrals: GAFF `c3`** (`--alias=si=c3`), noted as "si as c3" in each row.
Silicon in tetraphenylsilane is tetrahedral like carbon, and its torsions are low-barrier.
- The node angle is therefore GAFF's ca–c3–ca, 112.2°.
- The torsion about the Si–C bond is GAFF's X–c3–ca–X, which is zero.

These are stand-ins, and the weakest part of the set. With rigid blocks they only act
across the joins (`cp–cp`), which are all-GAFF, so they matter only in the closing phase's
all-atom optimisation.

## Checks

`tests/testSp3Sp2.py`:
- **Bonds and pairs:** Si–C(aryl) is 1.87 Å, and Si's Lennard-Jones terms are UFF's.
- **Completeness:** every angle and dihedral the recipe's joins can create has parameters.
