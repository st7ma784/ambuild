# Stacey: proposing the next recipes from real cells

Status: specification, not yet built.

**Stacey** stands for *Surrogate Trained on Actual Cells' Evidence and Yields*. It learns
from cells that were actually made and tested, and proposes which recipes to make next.

AmPorSandbox (`docs/amporsandbox-spec.md`) predicts what a *simulated* build will look like.
Stacey answers the question the lab cares about: given what real cells did, what should we
make next? Its MUM (`docs/mum-spec.md`) reads the micrographs.

## What it learns from

For each cell the lab makes and tests:

| Input | What it is | Where it comes from |
| --- | --- | --- |
| **The recipe** | the Ambuild recipe the material was designed from, and the manufacturing parameters: batch, precursor ratio, concentration, temperature, electrode loading, electrolyte, formation protocol | the ELN/LIMS record, linked to the Ambuild run or recipe |
| **Simulation priors** | the simulated structure's porosity and conductance, and AmPorSandbox's predictions with intervals | MLflow |
| **Charging curves** | voltage, current and capacity over time, every cycle | the cycler's files (BioLogic, Arbin, Neware, …) |
| **The post-mortem** | what was found when the cell was opened: failure mode (dendrites, cracking, delamination, gassing, electrolyte loss, short), measurements (thicknesses, mass changes, impedance), and the micrographs | the lab's post-mortem record; the images go to MUM |

It predicts, for a recipe not yet made:
- the cell's performance: capacity, capacity retention at N cycles, coulombic efficiency,
  impedance growth, rate capability;
- its likely failure modes;

and it proposes the next batch of recipes to make.

## Is it a genetic algorithm?

**Not as the main loop.** A genetic algorithm needs thousands of fitness evaluations. Here
each evaluation is a real cell: days of making, weeks of cycling, then a post-mortem. With
tens to a few hundred cells, a GA would barely get past its first generation.

**Use Bayesian optimisation with Gaussian processes instead.** This is built for few,
expensive, noisy evaluations:
- **The surrogate:** a GP over the recipe and manufacturing parameters predicts each
  performance measure with an uncertainty. With little data, a GP is the right model: it is
  honest about what it doesn't know, and it needs no more than tens of points to be useful.
- **Choosing the next batch:** an acquisition function picks the batch that best balances
  likely good cells against informative ones, for example batch expected improvement, or
  expected hypervolume improvement for several objectives at once.
- **Where a GA still fits:** optimising the acquisition function over the recipe space. That
  is cheap to evaluate, so an evolutionary search over it is fine, especially for the
  discrete parts of a recipe, such as which blocks and which joins. It's a tool inside the
  loop, not the loop.

**Simulation as a cheap first look:** a *multi-fidelity* GP treats Ambuild builds as a
cheap, biased look at the truth and real cells as the expensive, trusted one. It learns how
the two relate, so hundreds of simulations help where there are only tens of cells. This is
how AmPorSandbox's work feeds Stacey.

**Using early cycles:** cycle life can be predicted from the first tens of cycles (Severson
et al., Nature Energy 4, 383 (2019), predicting from the first 100). So Stacey updates its
predictions as a cell cycles. Long tests can be cut short when the outcome is clear, which
frees cyclers sooner.

## The fitness score

**The fitness is a few objectives with constraints, not one number.** One weighted score
would hide the trade-offs the lab needs to see.

**Objectives, from the charging curves:**

| Objective | Measured as |
| --- | --- |
| Capacity | mAh/g at the reference rate, after formation |
| Retention | % of initial capacity after N cycles, or predicted cycle life to 80% |
| Efficiency | mean coulombic efficiency over cycles 10–50 |
| Rate capability | capacity at a high rate ÷ at the reference rate |
| Impedance growth | change in internal resistance over cycling |

**Constraints and penalties, from the post-mortem:**
- **Safety:** shorts, dendrites through the separator, gassing. Hard constraints: a recipe
  likely to fail this way isn't proposed. Stacey models the probability of each failure mode
  with a GP classifier and keeps it below a threshold.
- **Other failure modes,** such as cracking and delamination, lower a recipe's score.
- **Manufacturability:** reproducibility across a batch's cells, and yield.

**What the lab sees:** the Pareto front of the objectives among recipes that satisfy the
constraints, and each proposed recipe's predicted position on it, with uncertainty.

## Workflow

1. **Record the cells.** Every cell made and tested gets a lab record (see "Lab data"
   below), linked to its recipe, its Ambuild run, its cycling files, its post-mortem and its
   micrographs.
2. **Process the curves.** Cycling files are parsed into per-cycle summaries (capacity,
   efficiency, voltage hysteresis, dQ/dV peaks) and early-cycle features.
3. **Train** Stacey's GP models. This is quick, and is redone whenever a cell's results
   arrive.
4. **Propose a batch.** "Propose N recipes for next week" returns the recipes, why each was
   chosen (likely good, informative, or probing a failure mode), and the predicted results
   with uncertainty.
5. **Plan deliberate defects.** The plan calls for making some cells with known defects to
   learn failure modes. Stacey can propose these too: recipes chosen because their failure
   behaviour is the most uncertain.
6. **Make and test** the cells; their results return to step 1.

**Logging:** each training and proposal goes to MLflow under `stacey/`:
- the data snapshot;
- the models and their cross-validated errors and calibration;
- the proposed batch;
- and later, how its cells actually did against Stacey's predictions. That record is the
  real test of the model.

## UI

Stacey has three pages in the web GUI:
- **Cells:** every cell's recipe, status (made, cycling, finished, opened), curves and
  post-mortem, and Stacey's predictions alongside.
- **Propose:** choose the objectives, constraints and batch size, see the proposed batch and
  why, and accept it, which creates the planned samples in the lab record.
- **Front:** the Pareto front of real cells and of predictions, coloured by failure mode.

## Lab data: what's needed first

Stacey needs a home for experimental results: the plan's "experimental results in the same
database". Proposed:
- **Tables** in Ambuild's database: samples (batch, recipe, manufacturing parameters, link
  to the Ambuild run and to the ELN/LIMS ID), cells, cycling files (in object storage, with
  parsed summaries), post-mortem records, and images (for MUM).
- **An API to load them,** and importers for the cyclers' file formats.
- **Links both ways** between sample IDs and run IDs.

This is shared with MUM, and is the first piece to build.

## Repository

Stacey is a package in the AmPorSandbox repository, `amporsandbox.stacey`, sharing its
MLflow plumbing, templates and evaluation. A separate repository would duplicate those.

Its own parts are:
- the lab-data client;
- the curve processing;
- the GP models, using BoTorch and GPyTorch;
- the acquisition and proposal code;
- the commands `stacey train | propose | report`.

## Milestones

| | Delivers | Needs |
| --- | --- | --- |
| S1 | Lab data: tables, API, a cycler importer, links between sample and run IDs | Ambuild's database |
| S2 | Curve processing: per-cycle summaries and early-cycle features, tested on public cycling datasets until lab data arrives | S1 |
| S3 | Single-objective Bayesian optimisation on retention, with failure as a constraint, proposing batches; logged to MLflow | S2, AmPorSandbox A1 |
| S4 | Several objectives (expected hypervolume improvement) and the Pareto front | S3 |
| S5 | Multi-fidelity: Ambuild simulations and AmPorSandbox predictions as the cheap fidelity | S3, AmPorSandbox A2 |
| S6 | Early-cycle prediction and stopping tests early | S2, enough cycled cells |
| S7 | The UI: Cells, Propose and Front pages | S3 |
| S8 | MUM's image embeddings as features | MUM M3 |

## Open questions

- **The search space:** which manufacturing parameters are varied, over what ranges, and
  which are fixed by the process? This needs the lab.
- **The ELN/LIMS in use,** and whether it has an API.
- **The batch size and rhythm the lab can sustain,** which the proposal size follows.
- **Weighing the objectives:** the Pareto front avoids choosing weights up front, but a
  default ranking within the front is still needed.
