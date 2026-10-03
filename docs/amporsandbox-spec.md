# AmPorSandbox: models that predict a build's results from its recipe

Status: specification, not yet built.

**AmPorSandbox** (Amps + Pore + sandbox) trains models that predict, from a recipe alone,
what a build will produce:
- porosity: surface area, pore limiting diameter, void fraction, percolation;
- conductance: coherent transmission, hopping, the resistor network;
- other useful outcomes: build time, the chance of failure, fragmentation.

It finds its training data in MLflow, where every finished Ambuild run is already logged
(`docs/mlflow.md`), and logs its models back there.

## Goals

1. **Predict before building.** Estimate a recipe's porosity and conductance, with an
   honest interval, in milliseconds, instead of a 25-minute build.
2. **Screen campaigns.** Score thousands of candidate recipes and build only the promising
   ones, and the ones the model is least sure of.
3. **Plan resources.** Predict build time and memory, to set Slurm resources.
4. **Make every model reproducible.** Each model records the exact runs it was trained on,
   its config and its code version, and is compared against a trivial baseline.

**Not goals, at first:**
- predicting from built structures (fast stand-ins for Poreblazer or liminal); this may
  come later as a second task type;
- generating recipes directly.

## Shape: a spin-out, connected through MLflow

AmPorSandbox is its own repository: `st7ma784/AmPorSandbox`, private, a git submodule at
`external/AmPorSandbox`.
- **Kept apart:** like liminal, it never imports Ambuild, and Ambuild never imports it.
- **MLflow is the interface:**
  - **AmPorSandbox reads:** each run's flattened recipe parameters, its `recipe.json`
    artifact, its metrics and its tags (including the review's strata and picks);
  - **AmPorSandbox writes:** training runs, metrics, datasets and models, with the model
    registry recording which model is current (the `champion` alias);
  - **Ambuild reads** only predictions, from a small predict service built from
    AmPorSandbox's image.

```
Ambuild builds ──ambuild-upload──▶ MLflow ◀──reads runs── AmPorSandbox train
     ▲                              │  ▲                        │
     │                              │  └─────models, metrics────┘
     └── predictions ◀── predict service (champion model) ◀────┘
```

## The template: one config file

Everything a training run does is set by one JSON config:

```
amporsandbox train config.json
```

The config has three sections, `data`, `model` and `train`:

```json
{
  "data": {
    "experiments": ["ambuild/Carbon network for Li-ion channels*", "ambuild/sp3-sp2*"],
    "status": ["FINISHED"],
    "exclude_tags": {"review.edge_cases": ["failed"]},
    "features": ["recipe_params", "block_descriptors", "join_graph"],
    "targets": ["surface_area_m2_g", "pore_limiting_diameter_a", "void_fraction",
                "el_log_transmission", "el_log_hopping", "final_build_seconds"],
    "split": {"group_by": "recipe_without_seed", "folds": 5, "holdout_families": ["paf"]}
  },
  "model": {"template": "gbt_quantile", "quantiles": [0.1, 0.5, 0.9], "options": {}},
  "train": {"seed": 0, "register_as": "porosity-conductance"}
}
```

### Data: the dataloader

**Fetching:** the dataloader finds runs in MLflow by experiment pattern, status and tags. For
each run it downloads `recipe.json` and reads its metrics.

**Snapshotting:** the matching runs are written to a parquet file, named by the hash of its
contents. The snapshot is logged as the training run's MLflow dataset input. So a model
always knows exactly which runs it saw, and a retrain on the same snapshot reproduces it.

**Features come only from the recipe**, since the point is to predict before building:

| Feature set | What it holds |
| --- | --- |
| `recipe_params` | the flattened recipe, by the same JSON-pointer names sweeps use: cell size, seed count, grow counts, passes, margins, closing on or off, `through_space`, … The seed itself is excluded |
| `block_descriptors` | per block type, parsed from the recipe's `.car` and `.csv` files: atoms, rings, elements, end groups, sp³ and Si nodes, meta and para linkage, node degree. Totals are weighted by seed and grow counts |
| `join_graph` | which block types may join which (the `bond_types`), as counts by kind of join, and as a small graph for graph models |

**Targets:** any MLflow metric. A run without a target is left out of that target's
training and scoring, not imputed.

**Splits:** these matter most, because one recipe built with different seeds gives
different structures.
- **`group_by: recipe_without_seed`** keeps all seeds of a recipe in the same fold. Without
  it, a model can score well just by having seen the recipe before.
- **`holdout_families`** keeps whole recipe families out of training, to measure
  generalisation to new chemistry.
- **Stratification:** folds are stratified by the review's strata (`review.stratum`), so
  each fold sees every kind of run.

### Model templates

Every template has the same interface:
- `fit(X, y)`;
- `predict(X)`, giving the median;
- `predict_interval(X)`, giving the quantiles;
- `save` and `load`.

| Template | Use |
| --- | --- |
| `stratum_median` | the baseline: the median of the recipe's stratum. Every model is scored against it |
| `gbt_quantile` | gradient-boosted trees, one per target and quantile. The default for tabular data |
| `gp` | a Gaussian process with calibrated uncertainty. For small data, and for steering campaigns |
| `mlp`, `set_graph` | neural models over block sets and the join graph. Later, when there's data for them |

**Intervals are not optional.** Seed-to-seed spread is large, especially for conductance,
so a single number would mislead. Every prediction carries its 10–90% interval.

**Adding a template** means one module implementing the interface, plus its options in the
config.

### What a training run logs to MLflow

AmPorSandbox logs to the experiment `amporsandbox/<register_as>`:

| | |
| --- | --- |
| **Params** | the whole config, flattened; the code version |
| **Dataset input** | the snapshot's hash and size, and its runs by stratum |
| **Metrics, per target** | MAE and R² on held-out folds; the same against the baseline (`skill = 1 − MAE / MAE_baseline`); interval coverage (the share of true values inside the 10–90% interval, ideally 0.8); errors per stratum and per held-out family |
| **Metrics, overall** | the hit rate on the review's good outliers (`good_outlier_recall@k`): would the model have pointed at the runs worth reproducing? |
| **Artifacts** | the model, the feature list, the config, plots (predicted against true, calibration), and a short report |
| **Registry** | the model version, with the metrics above. Promotion to `champion` is by hand, through the UI, and only if `skill` beats the current champion |

## Workflow

1. **Generate data.** Space-filling sweeps over each family's recipe parameters, at several
   seeds each, using the existing `qmc` campaign method. See "Data first" below.
2. **Train.** From the web GUI's Models page or the command line. Training runs as a queued
   job (a new job kind, `train`), on the agent like builds, so it runs on the cluster with no
   new infrastructure.
3. **Compare.** On the Models page, or in MLflow's UI for detailed comparison.
4. **Promote** the best model to `champion`.
5. **Use it.**
   - **Predict before building:** the New run page shows the champion's predictions, with
     intervals, for the recipe being edited.
   - **Surrogate campaigns:** a new campaign method, `model`. Each round, it samples many
     candidate recipes in the campaign's parameter space, scores them with the champion,
     and builds those most likely to improve the objective, plus some of the most uncertain
     (expected improvement on the predicted distribution). Their results flow back through
     MLflow into the next training.
   - **Resources:** predicted build time and memory become submissions' default Slurm
     resources.

## UI: the Models page

A new page in the web GUI, alongside Runs, Sweeps and Campaigns:
- **Models:** registered models, with their targets, skill against the baseline, interval
  coverage, training size and date, and which is `champion`.
- **Train:** a form to choose experiments, targets, feature sets and template. It writes the
  config, which can also be downloaded, and queues the training job.
- **A model's page:** its metrics per target and per stratum, the calibration and
  predicted-against-true plots, its dataset, and links to its MLflow run.
- **Promote:** set `champion`.
- **Predict:** paste or pick a recipe, and see the predictions with intervals.

The web service calls the predict service over HTTP, so it doesn't take on ML dependencies.

## Repository layout

```
AmPorSandbox/
  src/amporsandbox/
    data.py          MLflow fetch, snapshot, features, splits
    features/        recipe_params, block_descriptors (.car/.csv parsing), join_graph
    models/          stratum_median, gbt_quantile, gp, (mlp, set_graph)
    train.py         config → folds → fit → score → log → register
    evaluate.py      metrics, calibration, per-stratum, good-outlier recall
    serve.py         the predict service (champion model, HTTP)
    __main__.py      amporsandbox train | evaluate | predict | serve | snapshot
  configs/           examples: porosity-baseline.json, porosity-conductance.json
  tests/             stand-in MLflow client, synthetic recipes with known answers
  docs/              concepts, configs, templates, the results files
  Dockerfile         trainer and predict service
```

Its dependencies are MLflow, pandas, scikit-learn and LightGBM, plus PyTorch later for the
neural templates, kept as an optional extra.

## Ambuild's side

- **The `train` job kind** in the agent: runs `amporsandbox train` in AmPorSandbox's image,
  with the config from the submission.
- **The Models page and the New run predictions** in the web GUI, calling the predict
  service.
- **The `model` campaign method** in the campaigns service.
- **A compose service** `predict`, running the champion model, with `AMBUILD_PREDICT_URL`
  pointing at it.

## Data first

At the time of writing MLflow holds about 390 runs:
- about 200 of them in one Li-ion stratum;
- 4 with conduction results;
- many failed or test runs.

That's enough for a porosity baseline in the Li-ion family. It's not enough for
conductance, or for any model that generalises across families.

So the first deliverable after the template is data. For each family in the gallery, run a
quasi-random sweep over its main recipe parameters (cell size, grow counts, passes, the
closing margins): about 60 points with 3 seeds each. That's about 180 builds per family,
with the conduction stage on where the family has one. A 30 Å cell keeps each build to
minutes.

**The models and builds then improve each other:** surrogate campaigns choose the next
builds where the model is weakest or a recipe most promising.

## Evaluation rules

- **Report skill against the baseline,** never raw error alone. A model that doesn't beat
  the stratum median isn't promoted.
- **Grouped folds only.** A random split that puts a recipe's seeds on both sides is never
  reported.
- **Hold out a family.** At least one family is held out per evaluation, and its error is
  reported separately.
- **Check the intervals.** Coverage outside 0.7–0.9 means the intervals are mis-calibrated
  and are flagged on the model page.
- **Retrain on new data and compare with the champion.** Don't overwrite it.

## Milestones

| | Delivers | Needs |
| --- | --- | --- |
| A1 | The repository and template: the dataloader (fetch, snapshot, features, grouped splits), `stratum_median` and `gbt_quantile`, `amporsandbox train` logging to MLflow, tests against a stand-in MLflow client | MLflow (done) |
| A2 | The first real model: a porosity baseline on the Li-ion family, with skill and coverage reported | A1 |
| A3 | Data generation: per-family `qmc` sweeps with seeds, conduction on | Ambuild campaigns (done) |
| A4 | Training as a job, and the Models page: train, compare, promote | A1; the agent's `train` job kind |
| A5 | Predictions: the predict service, and predictions on the New run page | A4 |
| A6 | Surrogate campaigns: the `model` campaign method, an active-learning loop | A5, A3 |
| A7 | Resource predictions as default Slurm resources | A5 |
| A8 | Neural templates (`mlp`, `set_graph`), and the `gp` template for small data | A3 data |

## Open questions

- **Block descriptors** are parsed from the `.car` and `.csv` files the recipe references.
  Runs submitted through the GUI refer to them by sha256, so the dataloader fetches them from
  Ambuild's object store, or from a new web API for blobs, which would be cleaner. A1 decides.
- **Failure modelling:** failure and fragmentation are classification targets, but most
  current failures are test runs. They need filtering, perhaps by owner or recipe name, before
  they're used.
- **Several targets in one model or one per target?** Templates are one per target at
  first, which is simpler and easier to evaluate. Revisit with the neural templates.
