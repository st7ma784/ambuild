# GAFF 1.81 parameters for benzene rings, biaryl links and alkyne linkers

Force-field parameters for the atom types of the `benzene_135`, `benzene_14` and
`acetylene` building blocks, used by the example recipes `li_ion_carbon` (benzene +
alkyne) and `benzene_network` (benzene + biaryl links):

| type | atoms |
| --- | --- |
| `ca` | aromatic (benzene) carbon |
| `cp` | aromatic carbon that links to another block: GAFF's biaryl carbon (link `cp-cp` 1.4854 Å) |
| `ha` | hydrogen on an aromatic carbon, and on an alkyne carbon |
| `c1` | sp (alkyne) carbon |

Generated, not edited: from GAFF 1.81 (Wang et al., J. Comput. Chem. 25, 1157-1174, 2004)
as distributed in OpenMM's force-field project (openmmforcefields, file
`openmmforcefields/ffxml/amber/gaff/ffxml/gaff-1.81.xml` at commit
3f73b6ff9730e4e356fc6a0dbde6039517d59412, sha256
b534eb703e3c8952e121f3a6e703ec7c09156559c5068603859c0635d6f88756; itself generated from
AmberTools 24.8's `gaff-1.81.dat`), with:

    python scripts/gaff_params.py gaff-1.81.xml ambuild/recipes/params/gaff_benzene_alkyne ca ha c1 cp --alias=cp=ca

The script's docstring gives the conversions to Ambuild's forms and units (kcal/mol, Å,
HOOMD-blue's k/2 convention).

**Choices made**

- **`cp` for linking carbons.** A block's linking carbons are typed `cp` whatever they link to.
  - GAFF's own `cp` terms apply to ring-ring links: the bond, the angles about the linking carbon, and the biaryl torsion (`X-cp-cp-X`, 1 kcal/mol, n 2).
  - Where GAFF has no `cp` term (bonds to hydrogen or an alkyne carbon, and angles and dihedrals through them), the `ca` term stands in. The row's comment says `(cp as ca)`. This is what antechamber would do: it types an aryl carbon bonded to an alkyne `ca`.
- **Don't type two adjacent ring carbons `cp`.** GAFF's `cp-cp-cp` angle (90°) belongs to other ring systems. The GAFF blocks link at 1,3,5 and 1,4, so this never arises.
- **Don't mix these types with the legacy test blocks** (`benzene.car` etc.), whose `cp` means any aromatic carbon (`tests/params`: `cp-cp` 1.387 Å, the ring bond).
- **Alkyne hydrogen.** GAFF has two types for hydrogen on an sp carbon; `ha` is used, since its C–H length (1.0668 Å) is closest to acetylene's measured 1.063 Å. Only linker ends left unbonded carry it.
- **No impropers.** GAFF's are periodic and Ambuild's harmonic. The blocks are rigid in a rigid-body optimisation, and the ring dihedrals (k 7.25, n 2) keep rings planar in an all-atom one.
- **No charges.** The blocks carry none, so electrostatics are left out; the hydrocarbon's partial charges are small.
- **Optimise with dihedrals** (`do_dihedral`) when rings link directly: GAFF's biaryl torsion then sets the twist between linked rings. Without it only the ortho hydrogens' clash does, and rings end up nearly perpendicular. `benzene_network` does this.
