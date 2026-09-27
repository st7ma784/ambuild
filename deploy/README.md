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
