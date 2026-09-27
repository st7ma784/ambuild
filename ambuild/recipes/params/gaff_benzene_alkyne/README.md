# GAFF 1.81 parameters for benzene rings and alkyne linkers

Force-field parameters for the atom types of the `benzene_135` and `acetylene` building
blocks, used by the example recipe `li_ion_carbon`:

| type | atoms |
| --- | --- |
| `ca` | aromatic (benzene) carbon |
| `ha` | hydrogen on an aromatic carbon, and on an alkyne carbon |
| `c1` | sp (alkyne) carbon |

Generated, not edited: from GAFF 1.81 (Wang et al., J. Comput. Chem. 25, 1157-1174, 2004)
as distributed in OpenMM's force-field project (openmmforcefields, file
`openmmforcefields/ffxml/amber/gaff/ffxml/gaff-1.81.xml` at commit
3f73b6ff9730e4e356fc6a0dbde6039517d59412, sha256
b534eb703e3c8952e121f3a6e703ec7c09156559c5068603859c0635d6f88756; itself generated from
AmberTools 24.8's `gaff-1.81.dat`), with:

    python scripts/gaff_params.py gaff-1.81.xml ambuild/recipes/params/gaff_benzene_alkyne ca ha c1

The script's docstring gives the conversions to Ambuild's forms and units (kcal/mol, Å,
HOOMD-blue's k/2 convention). Choices made:

- GAFF has two types for hydrogen on an sp carbon; `ha` is used (its C-H length, 1.0668 Å,
  is closest to acetylene's measured 1.063 Å). Only linker ends left unbonded carry it.
- No impropers: GAFF's are periodic and Ambuild's harmonic. The blocks are rigid bodies in
  a rigid-body optimisation, and the ring dihedrals (k 7.25, n 2) keep rings planar in an
  all-atom one.
- No charges: the blocks carry none, so electrostatics are left out (the hydrocarbon's
  partial charges are small).
