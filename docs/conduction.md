# π conduction

A recipe's `conduction` stage asks liminal whether the structure's π system could carry
charge, and how much of that depends on tunnelling through sp³ atoms. It reports:
- the conjugated domains;
- the Hückel gap;
- the network's conductance along each axis, with and without tunnelling.

These make good metrics to optimise or constrain in sweeps and campaigns.

liminal is an external program (`external/liminal`), as with ion maps
(`docs/ion-maps.md`). Ambuild writes the cell to `conduction_<n>/structure.xyz` and its
topology (`docs/export.md`), then runs:

    liminal conduct structure.xyz --out conduct.json [--t-sp3 0.3] [--sp3-decay 0.455] [--max-bridge 3]

It then reads `conduct.json` ("liminal-conduction" version 1). liminal's `docs/conduction.md`
describes the model and the file.

## The stage

    {"op": "conduction", "t_sp3": 0.3, "sp3_decay": 0.455, "max_bridge": 3, "max_dense": 8000}

| Setting | Default | Meaning |
| --- | --- | --- |
| `t_sp3` | 0.3 eV | coupling between two π sites through one sp³ atom (an estimate, to be calibrated by DFT) |
| `sp3_decay` | 0.455 | its decay per extra sp³ atom in the chain: half the measured ~0.91 per CH₂ decay of alkane conductance |
| `max_bridge` | 3 | the longest chain of sp³ atoms followed |
| `max_dense` | 8000 | the largest conjugated domain diagonalised for the gap (π sites) |

The stage needs `LIMINAL_EXE`, or `liminal` on the PATH, with liminal's `conduct` extra
(NumPy and SciPy). A recipe with the stage won't start without liminal, and a failed run
stops the recipe.

Each result is recorded as a `conduction_result` event, with `conduct.json` as an artifact.
The web GUI's run page shows them in a **π conduction** table.

## Metrics

From the run's latest result, for sweeps and campaigns:

| Metric | Meaning |
| --- | --- |
| `el_gap` | the network's Hückel gap (eV): the lowest LUMO minus the highest HOMO of any domain |
| `el_conductance` | the cell's conductance, the mean over the three axes (g0) |
| `el_conductance_min` | along its weakest axis (g0) |
| `el_conjugated_conductance` | the same without the sp³ couplings: what conjugation alone carries |
| `el_tunnelling_share` | 1 − conjugated / total: how much of the conductance needs tunnelling |
| `el_largest_domain_fraction` | the largest conjugated domain, as a fraction of the π sites |
| `el_radical_domains` | domains with an odd electron count or degenerate frontier levels (for example, a trityl-like centre) |

**Units:** conductances are in g0, the conductance of one untwisted aromatic bond, per
cell, with a unit potential drop across the cell.

**What the numbers are good for:** they are relative. Use them to rank structures and to
constrain them ("no radicals", "conducts in all three directions", "at least half the
current conjugated").
- They are not conductivities in S/m, and the gap is a Hückel gap, not a band gap: benzene's
  is 5.7 eV.
- Absolute values need the levels' energies (thermally activated carriers across the gap)
  and calibration against DFT or measurement. liminal's roadmap has those steps.

**Current needs a framework that spans the cell.** A conductance above zero needs a
framework bonded to its own periodic image. Ambuild's rigid-block builds are trees unless
they end with a closing phase (`docs/closing.md`), and a tree never spans the cell. So
without closing, every conductance is 0, and the gap and domains are the only figures that
mean anything.

The sp³–sp² recipes (`docs/sp3-sp2-networks.md`) and the PAF recipes
(`docs/carbon-families.md`) run this stage after closing. The example campaign
`semiconducting_sp3_sp2` aims at it.
