# Deploying run recording

Recorded runs (`Cell(outputDir=..., recordRun=True)`, see
[docs/architecture.md](../docs/architecture.md)) are uploaded to PostgreSQL and
S3-compatible object storage by `ambuild-upload` (`services/ingest`).

| Path | What |
| --- | --- |
| `docker-compose.yml` | PostgreSQL 16, SeaweedFS (S3 API), the uploader, and test profiles |
| `slurm/` | Job scripts: build, upload, Poreblazer fan-out |
| `slurm/test/` | Single-node Slurm image that tests `slurm/` end to end |
| `k8s/` | K3s fallback: init Job, on-demand upload scan Job, runs PVC, Secret template |
| `helm/ambuild/` | Helm chart for the web GUI, deployed through Rancher's Fleet (`fleet.yaml`) |
| `demo/` | Demo runs for the web GUI's Compose profile |
| `datacentre/` | One directory per machine role, for running each part on its own machine from the published images ([docs/deployment.md](../docs/deployment.md)) |

## Local stack

```sh
cp deploy/.env.example deploy/.env      # set passwords
docker compose -f deploy/docker-compose.yml up -d
# upload everything under $AMBUILD_RUNS_DIR (default deploy/runs)
docker compose -f deploy/docker-compose.yml run --rm uploader --scan /runs
```

The `init` service creates the tables and the bucket; `ambuild-upload --init`
does the same against any database and bucket. MinIO no longer publishes
container images, so the local stack uses SeaweedFS; the uploader only uses the
S3 API and works with MinIO, Ceph RGW or AWS S3 as well.

Tests:

```sh
docker compose -f deploy/docker-compose.yml --profile test run --rm --build ingest-test
docker compose -f deploy/docker-compose.yml --profile slurm-test run --rm --build slurm-test
docker compose -f deploy/docker-compose.yml down -v
```

## Web GUI

The web GUI (`services/web`, planned in [docs/web-gui.md](../docs/web-gui.md)) reads the
same database and bucket. A local demo, with a few recorded runs (three with Poreblazer
results, one failed) built and uploaded on the first start:

```sh
docker compose -f deploy/docker-compose.yml --profile web up -d --build
# http://127.0.0.1:8080 (AMBUILD_WEB_PORT to change); the status page shows PostgreSQL
# and object storage, and why either is unreachable
docker compose -f deploy/docker-compose.yml --profile web-test run --rm --build web-test
```

It has no sign-in yet: keep it on the lab network.

The demo also runs a local agent (`services/agent`, the `ambuild-agent` image), which
builds what is submitted on the New run page in its own container and uploads it; a
demo recipe is saved on the first start. The agent's token is `AMBUILD_AGENT_TOKEN`
(development default in the Compose file; set it in `deploy/.env`), registered by the
`web-init` service. Submission end to end, as CI checks it:

```sh
docker compose -f deploy/docker-compose.yml --profile agent-test run --rm --build agent-test
```

To run an agent elsewhere, register it and give it the token:

```sh
ambuild-web init --agent lab-box:local        # prints a new token once
AMBUILD_API_URL=http://ambuild-web:8000 AMBUILD_AGENT_TOKEN=... ambuild-agent
```

It needs Ambuild (and HOOMD-blue, and Poreblazer for recipes that use it) and
`ambuild-upload` with its database and storage settings; builds do not get those.

Campaigns (goal-directed sweeps) are steered by the campaign controller, the demo's
`campaigns` service (`services/campaigns`). It uses the web API with a token of kind
`campaigns` (`AMBUILD_CAMPAIGNS_TOKEN`, registered by `web-init`), keeps no state, and can
run anywhere the web GUI is reachable:

```sh
ambuild-web init --agent controller:campaigns     # prints a new token once
AMBUILD_API_URL=http://ambuild-web:8000 AMBUILD_CAMPAIGNS_TOKEN=... ambuild-campaigns
```

For the `gp` method, build its image with `--build-arg EXTRAS=gp` (adds PyTorch).

### Helm chart and Fleet

`helm/ambuild/` deploys the web GUI and, with `agent.enabled`, a K3s agent that builds
submissions in its own pod; PostgreSQL and S3 are external, named in its values, and the
credentials (and the agent's token) come from a Secret created outside Git
(`helm/ambuild-secret.example.yaml`). The web pod's init container applies the schema
and registers the agent. Point a Fleet GitRepo's `paths` at
`deploy/helm/ambuild`. Fleet bundles every file under that path and stores the bundle
in etcd, and Helm stores each release in a Secret; both are capped at about 1 MiB, so
the directory holds the chart and `fleet.yaml` only, and `scripts/check_chart_size.sh`
(run in CI) fails if the packaged chart, the rendered manifests or the bundled files
grow past a small budget.

## Slurm

On the login node, with Ambuild and `services/ingest` installed in the job
environment:

```sh
mkdir -p ~/.config/ambuild && chmod 700 ~/.config/ambuild
cat > ~/.config/ambuild/upload.env <<'ENV'
DATABASE_URL=postgresql://ambuild:...@db-host:5432/ambuild
AMBUILD_S3_BUCKET=ambuild-runs
S3_ENDPOINT_URL=https://s3-host
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
ENV
chmod 600 ~/.config/ambuild/upload.env

export AMBUILD_RUNS_ROOT=/shared/ambuild/runs
deploy/slurm/submit_build.sh my_build.py --time=04:00:00 --gres=gpu:1
POREBLAZER_EXE=/opt/poreblazer/poreblazer.exe \
    deploy/slurm/submit_build.sh --poreblazer my_build.py
```

A build script creates its cell with `outputDir=os.environ["AMBUILD_RUN_DIR"]`,
`recordRun=True` and `runId=os.environ["AMBUILD_RUN_ID"]`
(`slurm/example_build.py`). The upload job runs `afterany` the build, so failed,
cancelled and timed-out builds are uploaded too (as `failed` or `incomplete`).
With `--poreblazer`, each pickle the build writes gets its own array task and
child run, and a second upload follows the array.

Ambuild runs as a single process. Submit with `--ntasks=N` to run each HOOMD-blue
calculation across N MPI tasks (`srun --ntasks=N python -m ambuild.hoomd_worker`);
this needs an MPI build of HOOMD-blue 4+ (conda-forge has none: build it from source),
and `AMBUILD_SRUN_MPI=pmix` (or similar)
if your cluster's `srun` needs `--mpi=`. For example:

```sh
AMBUILD_SRUN_MPI=pmix deploy/slurm/submit_build.sh my_build.py --ntasks=8 --gpus-per-task=1
```

Only all-atom calculations (`rigidBody=False`) are split across tasks; rigid-body
calculations, Ambuild's default, run on one task: HOOMD-blue 2 could not decompose
Ambuild's bonded rigid bodies, and this is untested with HOOMD-blue 4+. Small cells are often faster on one task
anyway; benchmark before scaling up.

A recipe (`python -m ambuild.recipe`) is submitted the same way:
`submit_build.sh --recipe recipe.json` (with `AMBUILD_BLOBS` for recipes that
reference files by sha256, and `AMBUILD_SEED` to override the seed). Many recipes go
in one array job with `submit_array.sh TASKS_FILE`, where each line of the file is
`RUN_ID RECIPE [SEED]`. At most `AMBUILD_ARRAY_MAX` tasks (default 50) run at once, and
one upload job follows for all the runs. The Slurm agent uses this for the web GUI's
sweeps.

### Slurm agent for the web GUI

To run what users submit in the web GUI on the cluster, run the agent on a login node,
in the same environment (with `services/agent` installed too) and the same
`upload.env`. Add the agent on the web GUI's Agents page, which shows its token once:

```sh
export AMBUILD_API_URL=https://ambuild.lab.internal AMBUILD_AGENT_TOKEN=...
export AMBUILD_AGENT_BACKEND=slurm AMBUILD_RUNS_ROOT=/shared/ambuild/runs
export AMBUILD_SLURM_DIR=$PWD/deploy/slurm
export AMBUILD_SLURM_PARTITION=cpu AMBUILD_SLURM_OPTIONS="--account=chem"   # optional
ambuild-agent          # in tmux, or as a systemd user service
```

The agent does the following:
- claims queued submissions and stages each recipe and its inputs under `AMBUILD_RUNS_ROOT`;
- submits them with `submit_build.sh --recipe`; the recipe's `resources` become `--cpus-per-task`, `--gpus`, `--mem` and `--time`;
- follows the jobs, and uploads running runs every minute so their pages update live;
- stops a job with `scancel` when a user cancels it;
- reports to the status page, including the partitions' nodes from `sinfo`.

It can be stopped and started at any time: it takes up its Slurm jobs again. It never
connects to the database; its token lets it take and report work only.

Without a login node, the `slurmrest` backend submits the same jobs through slurmrestd with
a cluster user's JWT, from a container anywhere (`datacentre/agent-slurmrest/`). Its jobs
stage their recipes on the cluster and upload their runs themselves, so the agent needs
no Slurm commands, munge key, shared filesystem or database credentials. See
[docs/deployment.md](../docs/deployment.md), which also lists the cluster-side setup
(JWT authentication, slurmrestd with `SLURM_JWT=daemon`, the user's `upload.env`).

## K3s fallback

```sh
kubectl apply -f deploy/k8s/runs-pvc.yaml          # the shared runs filesystem
kubectl apply -f my-secret.yaml                    # from k8s/secret.example.yaml
kubectl apply -f deploy/k8s/uploader.yaml          # init Job (creates the tables)
kubectl create -f deploy/k8s/upload-scan.yaml      # a scan, whenever one is wanted
```

Nothing is scheduled. Each `upload-scan.yaml` Job scans the runs volume once and
uploads finished runs without a `.ambuild-uploaded` marker, plus runs untouched for
a day as `incomplete`; run one after an outage, or whenever uploads look behind.
Build the image with
`docker build -f services/ingest/Dockerfile -t <registry>/ambuild-ingest:0.1.0 .`
and set it in `k8s/uploader.yaml` and `k8s/upload-scan.yaml`.
