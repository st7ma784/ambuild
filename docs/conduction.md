# π conduction

A recipe's `conduction` stage asks liminal whether the structure's π system could carry
charge, and how. It reports:
- the conjugated domains;
- the Hückel gap;
- the network's conductance along each axis as a resistor network, with and without
  tunnelling through sp³, Si and other node atoms;
- its coherent (Landauer) transmission, which also sees quantum interference, energy
  mismatch between domains, and the gap;
- hopping between conjugated domains, with the gap as thermal activation;
- optionally, through-space couplings between stacked rings.

These make good metrics to optimise or constrain in sweeps and campaigns.

liminal is an external program (`external/liminal`), as with ion maps
(`docs/ion-maps.md`). Ambuild writes the cell to `conduction_<n>/structure.xyz` and its
topology (`docs/export.md`), then runs:

    liminal conduct structure.xyz --out conduct.json

It then reads `conduct.json` ("liminal-conduction" version 1). Two liminal documents go
with it:
- `docs/conduction.md`: the model and the file;
- `docs/calibration.md`: how the couplings through node atoms were fitted to DFT.

## The stage

    {"op": "conduction"}

| Setting | Default | Meaning |
| --- | --- | --- |
| `t_sp3` | liminal's: 0.60 eV | coupling between two π sites through one sp³ carbon, fitted to DFT (it ranges 0.18–0.78 eV with the rings' orientation) |
| `sp3_decay` | liminal's: 0.36 | its decay per extra sp³ atom in a chain (poorly determined) |
| `max_bridge` | liminal's: 3 | the longest chain of node atoms followed |
| `max_dense` | liminal's: 8000 | the largest conjugated domain diagonalised for the gap (π sites) |
| `through_space` | false | also couple π sites that aren't bonded (stacked rings, within 5 Å), with liminal's DFT-calibrated terms. Recommended; it adds about 15 minutes for a 5,500-site network |

Settings left out aren't passed, so liminal's calibrated defaults apply. The couplings
through other elements (Si, ...) are liminal's too. An element liminal has no coupling for
interrupts conduction, and is reported in the results (`uncoupled_bridge_atoms`).

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
| `el_conductance` | the cell's conductance as a resistor network, the mean over the three axes (g0) |
| `el_conductance_min` | along its weakest axis (g0) |
| `el_conjugated_conductance` | the same without the couplings through node atoms: what conjugation alone carries |
| `el_tunnelling_share` | 1 − conjugated / total: how much of the conductance needs tunnelling through nodes |
| `el_log_transmission` | log10 of the coherent transmission, thermally averaged at 300 K, the mean over the axes the network spans |
| `el_log_transmission_min` | the same along the weakest axis (None if some axis isn't spanned) |
| `el_log_hopping` | log10 of the hopping conductance between conjugated domains at 300 K (critical path, Marcus rates, thermal activation across the gap), the mean over axes, for the better carrier. An axis spanned by one conjugated domain counts as band-like, at a ceiling of 15.6 |
| `el_log_hopping_min` | the same along the weakest axis (None if some axis isn't spanned) |
| `el_largest_domain_fraction` | the largest conjugated domain, as a fraction of the π sites |
| `el_radical_domains` | domains with an odd electron count or degenerate frontier levels (for example, a trityl-like centre) |

**Units:**
- **Conductances:** in g0 (one untwisted aromatic bond), per cell, with a unit potential
  drop across the cell.
- **Transmission:** per cell, dimensionless, relative (it depends on the leads, which are
  kept the same for every structure).

**Three views, compare them:**
- **The resistor network** treats every coupling as incoherent. It answers "is there a path,
  and how many?", and can't see phase or energies.
- **Coherent transmission** keeps phase across one cell, so it sees interference and the gap.
- **Hopping** is the regime real amorphous materials are usually in: localised domains,
  thermally activated, with orbital symmetry deciding which links carry anything. Its
  reorganisation energy is assumed (0.25 eV) until calibrated.

**What the checks on Ambuild's builds showed** (liminal's `docs/conduction.md` has the
details):
- **The controls hold on real structures:**
  - cutting the bonds across one face gives exactly 0 through it;
  - re-wrapping changes nothing;
  - a 2×2×2 supercell gives exactly twice the per-cell conductance;
  - t_sp3 = 0 gives 0 in a network joined only through nodes.
- **The two measures can disagree completely.** The resistor network ranks graphdiyne
  (0.042 g0) above `tpm_phenylene_large`'s sp³-node network (0.019). But coherent
  transmission at the Fermi level ranks the sp³-node network first, by tens of orders of
  magnitude:

  | Structure | log10 T at E_F |
  | --- | --- |
  | sp³-node network | −17.5 |
  | CTF-1 | −30 |
  | graphdiyne | −110 |
  | graphyne | −183 |

  Graphyne and graphdiyne are made of meta-linked 1,3,5-benzenes, and show a sharp
  destructive-interference dip exactly at the Fermi level. So a network interrupted by sp³
  or Si nodes can transmit better than a fully conjugated one.
- **At 300 K the dip is partly smoothed:** graphyne's thermal average is 10⁻⁵⁵, not 10⁻¹⁸³.
  That's why `el_log_transmission` uses the thermal average.
- **Stacked rings matter** (`through_space`). Ring contacts under 5 Å raise graphyne's
  resistor conductance from 0.015 to 0.043 g0 (it then conducts along y too), and its
  coherent transmission from 10⁻⁵⁵ to 10⁻³⁷. CTF-1 goes from 0.021 to 0.120 g0. In the
  Si-node network, stacked rings bypass the weak Si nodes: its resistor conductance rises
  31-fold, to within a factor of 2 of the carbon-node network (0.059 against 0.121 g0).
  Whether a Si-noded framework conducts may turn on how densely its rings stack. The recipes
  with a conduction stage turn it on.
- **Hopping sees orbital symmetry.** The built Si-node network spans y as a resistor network
  but doesn't hop along y: some of its bridges join ring sites where the frontier orbital has
  a node.

**What the numbers are good for:** ranking structures and constraining them, for example
"no radicals", "transmits along all three axes", or "at least half the current conjugated".
- **They are not conductivities in S/m,** and the gap is a Hückel gap, not a band gap:
  benzene's is 5.7 eV.
- **Absolute values need more:** thermally activated carriers across the gap, hopping
  between localised domains, and calibration against measured materials, including
  Si-noded frameworks that do and don't conduct. liminal's roadmap has those steps.

**Current needs a framework that spans the cell.** Both measures need a framework bonded to
its own periodic image. Ambuild's rigid-block builds are trees unless they end with a
closing phase (`docs/closing.md`), and a tree never spans the cell. Coherent transmission
across an axis the network doesn't span is counted as 0.

The sp³–sp² recipes (`docs/sp3-sp2-networks.md`) and the PAF recipes
(`docs/carbon-families.md`) run this stage after closing. The example campaign
`semiconducting_sp3_sp2` aims at it.
