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
