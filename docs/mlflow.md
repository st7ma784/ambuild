# MLflow

Every finished run is also logged to an MLflow tracking server, alongside the web GUI's own
database. It holds the settings each structure was generated with and the properties it
ended up with, which is what MLflow's run comparison, parallel-coordinates and scatter views
are for.

## What is logged

One MLflow run per Ambuild run, in the experiment `ambuild/<recipe name>`: the recipe's
`name` field, such as `ambuild/PAF-1: tetrahedral carbon and biphenyl, large`. Runs from
Python scripts go to `ambuild/scripts`.

| | |
| --- | --- |
| **Parameters** | the recipe, flattened by JSON pointer, the same paths sweeps and campaigns vary: `cell/box`, `cell/bond_margin`, `stages/1/repeat`, `stages/1/stages/0/count`, `stages/5/through_space`, …; `blocks`, `fragments/<type>`, `bond_types` and `params` (the parameter set); `recipe_seed`, and `seed`, the seed the run actually used |
| **Final properties** (metrics) | `final_density`, `final_num_particles`, `final_num_blocks`, `final_num_free_endgroups`, `final_potential_energy` and `final_build_seconds`, from the last build step; the latest Poreblazer result (`surface_area_m2_g`, `pore_limiting_diameter_a`, `helium_volume_cm3_g`, …); the latest ion map of each ion (`li_escape_barrier`, …); the latest conduction result (`el_gap`, `el_conductance`, `el_log_transmission`, `el_log_hopping`, …) |
| **History** (metrics by step) | `step/density`, `step/num_particles`, `step/num_blocks` |
| **Artifacts** | `recipe.json` and `run.json` |
| **Tags** | `ambuild.run_id`, `ambuild.status`, `ambuild.recipe`, `ambuild.recipe_sha256`, `ambuild.version`, `ambuild.git_commit`, `ambuild.parent_run_id`, `ambuild.host`, `ambuild.slurm_job_id`, `ambuild.error` for failed runs, and `ambuild.url`, a link to the run's page in the web GUI |

**Statuses:** finished runs are FINISHED, failed ones FAILED, and incomplete or cancelled
ones KILLED. Runs still running aren't logged until they end.

**Parameter values** longer than 500 characters are cut, with "…" at the end.

## How it gets there

`ambuild-upload` (services/ingest) logs each run it uploads once the run has finished, so
every route is covered: the agent's local builds, Slurm jobs, slurmrestd jobs and the
uploader's scans.
- **Finding a run again:** it looks the run up by its `ambuild.run_id` tag, so uploading
  again changes nothing. A run uploaded first as incomplete and later as finished is updated
  in place. Parameters are only logged the first time, because MLflow can't change them.
- **MLflow being down never fails an upload:** the uploader checks the server's health
  first and logs a warning instead.

**Configuration** (environment of the uploader, the agent and the Slurm upload jobs):

| Variable | Default (compose) | |
| --- | --- | --- |
| `MLFLOW_TRACKING_URI` | `http://mlflow:5000` | the tracking server; unset: nothing is logged |
| `AMBUILD_PUBLIC_URL` | empty | the web GUI's address as people reach it (e.g. `http://scc-hdd-01:8080`), for `ambuild.url` links |
| `AMBUILD_MLFLOW_URL` | `http://127.0.0.1:5050` | the MLflow UI's address, for the web GUI's **MLflow** link |

The uploader needs `mlflow-skinny`: `pip install "ambuild-ingest[mlflow]"`. The published
ingest and agent images include it.

**Existing runs:** `ambuild-upload --mlflow-backfill` logs every run in the database not yet
logged at its current status, reading recipes back from object storage. The compose stack
runs it as the `mlflow-backfill` service each time it starts.

## The server

`deploy/mlflow` holds the image: MLflow 2.22, with SQLAlchemy kept below 2.1, which removed a
class this MLflow imports.
- **Runs** go in PostgreSQL, in a database `mlflow` on the stack's server. The entrypoint
  creates it if it's missing, because an existing PostgreSQL volume doesn't rerun init
  scripts.
- **Artifacts** go in the S3 store under `s3://ambuild-mlflow`, served through the tracking
  server (`--serve-artifacts`), so clients need no S3 credentials.

In the compose file it's the `mlflow` service (profiles `web` and `mlflow`), on
`127.0.0.1:${AMBUILD_MLFLOW_PORT:-5050}`.

```sh
docker compose -f deploy/docker-compose.yml --profile web up -d    # includes MLflow and the backfill
```

**Slurm and slurmrestd jobs** upload from the cluster, so set `MLFLOW_TRACKING_URI` (and
`AMBUILD_PUBLIC_URL`) in their environment too, as for `DATABASE_URL`
([deployment.md](deployment.md)), with an address the compute nodes can reach.

**Using it from Python:** point MLflow's client at the server; MLflow ≥ 2.17 works. For
example, every finished PAF-1 run with its pore limiting diameter and cell size:

```python
import mlflow
mlflow.set_tracking_uri("http://scc-hdd-01:5050")
runs = mlflow.search_runs(experiment_names=["ambuild/PAF-1: tetrahedral carbon and biphenyl, large"],
                          filter_string="attributes.status = 'FINISHED'")
runs[["params.cell/box", "params.stages/1/stages/0/count", "metrics.pore_limiting_diameter_a"]]
```

**Not logged:** the build's structures and other run files stay in object storage and the
web GUI, linked by `ambuild.url`. MLflow holds settings and results, not data.

## Derived metrics

Logged with every run, and filled in by the review for runs logged before they existed, so
runs of different sizes can be compared:

| Metric | Meaning |
| --- | --- |
| `void_fraction` | helium-accessible volume ÷ the cell's volume |
| `pore_window_ratio` | pore limiting ÷ largest pore diameter: 1 is a uniform channel; near 0, large cages behind narrow windows |
| `single_framework` | 1 if the build is one bonded framework |
| `el_spans_all` | 1 if it conducts along all three axes (resistor network) |
| `free_end_groups_per_1000_atoms` | how unfinished the network is; the rigid-block builds sit at 60–90 |
| `build_ms_per_atom` | build time per atom |

## Review: the runs worth looking at

`ambuild-review` (services/ingest) reads every run in MLflow and picks the ones to look at
in production. It runs automatically:
- after any upload that logged a run;
- after the backfill at stack start-up.

There is no timer. You can also run it by hand: `ambuild-review [--criteria FILE]
[--report report.md]`.

**Strata:** runs are compared only within their stratum, by default recipe × cell size, so
a 30 Å test build is never ranked against a 60 Å production one.

**Scores:** within each stratum, each run's *porosity* and *conductance* scores (0–1) are
the weighted mean of its percentile ranks on these metrics, each in its better direction:
- **porosity:** surface area, pore limiting diameter, void fraction, percolated dimensions;
- **conductance:** coherent transmission, hopping, conductance along the weakest axis,
  spanning all three axes.

A run lacking a metric is scored on the rest. A stratum of one run isn't ranked.

**Gates** a run must pass to count among the best: finished; pores percolating in at least
one direction; pore limiting diameter at least 1.52 Å (a bare Li⁺); no radical π domains.
A metric a recipe doesn't measure isn't a failure.

**Picks** (the `review.picks` tag):

| Pick | Which runs |
| --- | --- |
| `top_porosity`, `top_conductance`, `top_both` | the best 3 gated runs per stratum on each score, and on their mean |
| `pareto` | gated runs that no other run beats on both porosity and conductance (across strata, as the scores are percentiles within them): the trade-off front |
| `outlier` | in a stratum of at least 8, a robust z-score (median and MAD) of at least 3.5 on any of surface area, PLD, void fraction, density, blocks, transmission, hopping, conductance, build time or free end groups; `review.outliers` says which metrics, and which way |
| `good_outlier` | an outlier in the better direction of a scored metric that also passes the gates: an unusually good build, worth reproducing |
| `stratified` | per stratum, the runs nearest the 10th, 50th and 90th percentiles of each score: a small reference set spanning the range, for regression and comparison tests |
| `edge_case` | named rules (the `review.edge_cases` tag), listed below |

**Edge cases:**

| Edge case | What it flags |
| --- | --- |
| `porous_but_closed` | surface area over 1,000 m²/g but no percolating pore |
| `fragmented` | 5 or more separate frameworks |
| `spans_without_coherent_path` | the resistor network spans every axis, but coherent transmission is below 10⁻⁴⁰ (interference or gaps) |
| `conducts_through_radicals` | conducts, with radical π domains, whose levels inflate transmission |
| `narrow_window_large_cage` | pore window ratio under 0.3 with a cage over 10 Å |
| `unfinished_network` | over 150 free end groups per 1000 atoms |
| `failed` | the build failed |

**Where to find the results:**
- **On every run:** tags for the picks, edge cases, outliers, both scores, the stratum and
  the gate result. They can be filtered in MLflow's UI, for example
  `tags.\`review.picks\` LIKE '%pareto%'` or `tags.\`review.edge_cases\` != ''`.
- **The `ambuild/review` experiment:** one run per review pass.
  - **Artifacts:** `report.md` (the Pareto front, the best per stratum, good and other
    outliers, edge cases, the stratified set and a table of strata, each run linked to its
    GUI page and its MLflow run), `picks.csv`, and the `criteria.json` used.
  - **Metrics:** counts of each pick, so the review's own history is a time series.
  - **The latest report** is also the experiment's description, so opening the experiment
    shows it.

**Changing the criteria:** copy `services/ingest/ambuild_ingest/review_criteria.json`, edit
it, and point `AMBUILD_REVIEW_CRITERIA` (for the automatic reviews) or `--criteria` at the
copy. You can change the gates, the score metrics and weights, the strata, the outlier
threshold, the quantiles and the edge-case rules. `AMBUILD_REVIEW=0` turns the automatic
reviews off.
