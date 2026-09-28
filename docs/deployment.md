# Deploying Ambuild across a server room

This guide runs each part of Ambuild on the machine that suits it: the database on fast
disks, object storage on big disks, the web GUI on a small service VM, and builds on
Slurm or on compute boxes. Every part is a published container image (except the
login-node agent, which is a Python install), and every machine's files are in
[`deploy/datacentre/`](../deploy/datacentre/).

For one machine (a demo, or development), use the Compose stack in
[`deploy/README.md`](../deploy/README.md) instead. For Kubernetes, use the Helm chart
(`deploy/helm/ambuild`), which takes the same images and settings.

## The pieces

| Role | Directory | Image | Suits |
| --- | --- | --- | --- |
| Database | `database/` | `postgres:16` | fast local SSD, a few GB of RAM |
| Object storage | `storage/` | `chrislusf/seaweedfs` (or your MinIO, Ceph RGW, cloud S3) | large, cheap disks |
| Web GUI and API | `web/` | `ghcr.io/st7ma784/ambuild-web` | a small VM, behind a TLS proxy |
| Campaign controller | `campaigns/` | `ghcr.io/st7ma784/ambuild-campaigns` | anywhere (tiny; share the web VM) |
| Slurm agent, through slurmrestd | `agent-slurmrest/` | `ghcr.io/st7ma784/ambuild-agent` | anywhere that reaches slurmrestd (share the web VM) |
| Slurm agent, on a login node | `login-node/` | none: a Python venv | a login node |
| Local agent | `agent-local/` | `ghcr.io/st7ma784/ambuild-agent` | compute or GPU boxes outside Slurm |
| Cluster side | `cluster/` | the build environment (below) | the Slurm cluster |

The images are built and pushed by `.github/workflows/publish-images.yml`. On every push to
`master` it tags each image `sha-<commit>` and `latest`; a `vX.Y.Z` Git tag adds `X.Y.Z`
and `X.Y`. Pin `AMBUILD_TAG` to a `sha-` or release tag in production, so every machine
runs the same version. New packages on ghcr.io start private: make them public in the
package settings on GitHub, or `docker login ghcr.io` on each machine with a token that
can read packages. `ghcr.io/st7ma784/ambuild-uploader` (for `ambuild-upload --init`) and
`ghcr.io/st7ma784/ambuild` (the build runtime) are published too.

### Choosing how builds run

An agent claims queued work from the web API and runs it. Run one or more of:

- **`slurmrest`: Slurm through slurmrestd.** The agent is a container anywhere that can
  reach slurmrestd and the web GUI. It needs no login node, Slurm commands, munge key or
  shared filesystem. It submits the same jobs as the login-node agent through Slurm's
  REST API, as one cluster user, with that user's JWT. The jobs stage their recipe and
  input files on the cluster (sent inside the job script, checked by sha256), upload the
  run while it builds and again at the end. The agent learns how each run ended from the
  web API. This is the choice when services live in a server room and Slurm is
  administered separately.
- **`slurm`: on a login node.** The agent runs as the cluster user on a login node and
  uses `sbatch`, `squeue` and `scancel`. Use it where slurmrestd is not available.
- **`local`: in its own container.** Each build runs inside the agent's container on that
  machine. It suits workstations and GPU boxes outside Slurm, and small labs.

Both Slurm agents claim work queued for `slurm`, so users see one "Slurm" choice;
register the agent with backend `slurm` either way. Both follow their jobs across
restarts, turn a sweep into one array job, and map a recipe's `resources` (cpus, gpus,
memory_mb, time) onto the job.

## Network

Nothing connects *to* an agent or the controller: they poll the web API. So they can sit
behind NAT or a firewall that only allows outgoing connections.

| From | To | Port | Why |
| --- | --- | --- | --- |
| users' browsers | web (TLS proxy) | 443 | the GUI |
| web | database | 5432 | everything |
| web | object storage | 8333 (or your S3's) | run files, structures |
| every agent, the controller | web | 443 | the agent API (token) |
| local agents | database, object storage | 5432, 8333 | uploading runs |
| cluster compute nodes | database, object storage | 5432, 8333 | upload jobs and live uploads |
| slurmrest agent | slurmrestd | 6820 (or the proxy's) | submitting and following jobs |

Put TLS on anything that crosses machines: a reverse proxy for the web GUI and S3, and
`?sslmode=require` in `DATABASE_URL` once PostgreSQL has a certificate.

## Settings and secrets: what goes where

**URLs**

| Setting | Points at | Set on |
| --- | --- | --- |
| `DATABASE_URL` | database: `postgresql://ambuild:<password>@<db-host>:5432/ambuild` | web, local agents, cluster `upload.env` |
| `S3_ENDPOINT_URL`, `AMBUILD_S3_BUCKET` | object storage, and the bucket (created by init) | web, local agents, cluster `upload.env` |
| `AMBUILD_API_URL` | the web GUI, as that machine reaches it | every agent, the controller |
| `AMBUILD_SLURMRESTD_URL` | slurmrestd | the slurmrest agent |
| `AMBUILD_RUNS_ROOT` | a directory on the cluster's shared filesystem | both Slurm agents (a path on the cluster, not on the agent's machine) |

**Secrets**

| Secret | Created by | Given to | Never needed by |
| --- | --- | --- | --- |
| PostgreSQL password (inside `DATABASE_URL`) | you, in `database.env` | web, local agents, cluster `upload.env` | the slurmrest agent, the controller, builds |
| S3 keys (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) | you, in `storage.env` (or your S3's admin) | web, local agents, cluster `upload.env` | the slurmrest agent, the controller, builds |
| Agent tokens (`AMBUILD_AGENT_TOKEN`) | the web GUI: `ambuild-web init --agent name:backend`, or the Agents page (shown once, stored hashed) | that one agent | everything else |
| Controller token (`AMBUILD_CAMPAIGNS_TOKEN`) | the same, with kind `campaigns` | the controller | everything else |
| Slurm JWT (`slurm.jwt`) | the Slurm admins: `scontrol token username=<user> lifespan=<seconds>` | the slurmrest agent | everything else |
| Slurm's JWT signing key | the Slurm admins | slurmctld (and slurmdbd) only | the agent, slurmrestd |

A token lets its agent take and report work, and nothing more. Revoke it on the Agents
page. Builds run with the database, storage and Slurm credentials removed from their
environment, and upload credentials reach the cluster only through `upload.env`.

## Setting it up

In this order.

### 1. Database

```sh
cd deploy/datacentre/database
cp database.env.example database.env && chmod 600 database.env   # set POSTGRES_PASSWORD, DATA_DIR
docker compose --env-file database.env up -d
```

Back up with `pg_dump`. Restrict port 5432 to the machines in the network table above.

### 2. Object storage

```sh
cd deploy/datacentre/storage
cp storage.env.example storage.env && chmod 600 storage.env      # set the keys, DATA_DIR
docker compose --env-file storage.env up -d
```

Or use existing S3 storage: create a key pair for Ambuild and skip this machine.

### 3. Web GUI

```sh
cd deploy/datacentre/web
cp web.env.example web.env && chmod 600 web.env                  # DATABASE_URL, S3 settings
docker compose --env-file web.env run --rm uploader-init          # result tables and the bucket
docker compose --env-file web.env run --rm web init \
    --agent hpc:slurm --agent controller:campaigns                # prints each token once
docker compose --env-file web.env up -d web
```

Proxy `https://ambuild.lab.internal` to `127.0.0.1:8000`. The status page (`/status`)
shows whether the web GUI reaches the database and storage, and a card per agent. The
GUI has no sign-in yet (milestone 10 in [web-gui.md](web-gui.md)): keep it on the lab
network.

### 4. The cluster side (for either Slurm agent)

On the cluster, as the user whose jobs these will be (a service account such as
`ambuild` is simplest):

1. **The runs directory:** create `AMBUILD_RUNS_ROOT` on the shared filesystem, writable
   by that user. Run directories, staged recipes (`.ambuild-work/`), input files
   (`.ambuild-blobs/`) and job logs go there.
2. **Upload settings:** put `cluster/upload.env.example` in `~/.config/ambuild/upload.env`
   (mode 600, directory 700) with the database and storage settings. Compute nodes must
   reach both.
3. **A build environment on the compute nodes**, with `python` (with Ambuild and
   HOOMD-blue), `ambuild-upload`, and Poreblazer for recipes that use it. Either:
   - **An environment on the shared filesystem:** a conda/mamba environment with
     HOOMD-blue, then `pip install` Ambuild and `services/ingest` into it (as in
     [install.md](install.md)). The jobs activate it: `AMBUILD_SLURM_SETUP=source
     .../bin/activate` for slurmrest; the login-node agent passes on its own `PATH`.
   - **Or the published image, with Apptainer.** `ghcr.io/st7ma784/ambuild-agent` holds
     Ambuild, HOOMD-blue, Poreblazer and `ambuild-upload`:

     ```sh
     apptainer pull /shared/ambuild/ambuild.sif docker://ghcr.io/st7ma784/ambuild-agent:<tag>
     mkdir -p /shared/ambuild/bin
     for tool in python ambuild-upload; do
         printf '#!/bin/sh\nexec apptainer exec --bind /shared /shared/ambuild/ambuild.sif %s "$@"\n' "$tool" \
             > /shared/ambuild/bin/$tool
     done
     chmod +x /shared/ambuild/bin/*
     ```

     Then put `/shared/ambuild/bin` first on the jobs' `PATH`, and set
     `POREBLAZER_EXE=/opt/poreblazer/poreblazer.exe` (the path inside the image).

For slurmrest, the Slurm admins also need to:
- **Enable JWT authentication:** `AuthAltTypes=auth/jwt` and
  `AuthAltParameters=jwt_key=...` in `slurm.conf` (and `slurmdbd.conf`).
- **Run slurmrestd with JWTs** (`slurmrestd -a rest_auth/jwt ...`) as an unprivileged
  user, with `SLURM_JWT=daemon` in its environment. Without that, slurmrestd talks to
  slurmctld with munge, which rejects the forwarded token.
- **Serve accounting too, if slurmdbd is available** (the default plugin set). Without
  slurmdbd, restrict slurmrestd to `-s openapi/slurmctld`; the agent needs only these
  endpoints.
- **Issue the service user's token:** `scontrol token username=ambuild lifespan=<seconds>`.
  Pick a lifespan to suit local policy, and put each new token in the agent's token file
  before the old one expires. The agent rereads the file for every request, so no
  restart is needed. Until then the agent's status card shows "slurmrestd rejected the
  token", and it claims nothing, so queued work waits rather than failing.
- **Put slurmrestd behind a TLS proxy** if it's reachable beyond the cluster network.
  Point `AMBUILD_SLURMRESTD_CA` at the proxy's CA if it isn't in the system's trust store.

### 5. Agents and the controller

**slurmrest** (on the web VM or any service machine):

```sh
cd deploy/datacentre/agent-slurmrest
cp agent.env.example agent.env && chmod 600 agent.env   # API URL and token, slurmrestd, user, runs root, setup
echo "<jwt>" > slurm.jwt && sudo chown 1000 slurm.jwt && chmod 400 slurm.jwt
docker compose --env-file agent.env up -d
```

`AMBUILD_SLURMREST_ENV` is the jobs' whole environment besides Ambuild's own variables:
through slurmrestd they inherit nothing from the agent. Set `PATH` there, or activate
an environment with `AMBUILD_SLURM_SETUP`. `AMBUILD_SLURMREST_JOB` adds job fields such
as `{"account": "chem", "qos": "normal"}`. The agent uses the newest slurmrestd API
version it has been tested with that slurmrestd offers: v0.0.42, v0.0.41 or v0.0.40, all
tested against Slurm 24.11. Set `AMBUILD_SLURMRESTD_VERSION` to try another.

**Login node:** follow the comments in `login-node/ambuild-agent.service`: a venv with
Ambuild, `services/ingest` and `services/agent`, a clone for `deploy/slurm`, and
`agent.env` plus `upload.env` in `~/.config/ambuild/`.

**Local agents** (each compute box):

```sh
cd deploy/datacentre/agent-local
cp agent.env.example agent.env && chmod 600 agent.env   # API URL, its own token, DB and S3 settings
docker compose --env-file agent.env up -d
```

**Campaign controller:**

```sh
cd deploy/datacentre/campaigns
cp campaigns.env.example campaigns.env && chmod 600 campaigns.env
docker compose --env-file campaigns.env up -d
```

Each agent appears on the status page within a heartbeat (30 s). A card shows its
backend, host, what it is running, and for Slurm the partitions' nodes. A slurmrest
agent's card also names its slurmrestd. A warning on a live agent means it can't reach
Slurm, and says why.

## Checking it end to end

1. On the status page: PostgreSQL, object storage and each agent are green.
2. **New run** → choose an example recipe → **Slurm** (or a local agent's backend) → Submit.
3. **The submission page** shows it claimed, then submitted (with its Slurm job ids), then
   running. The run page fills in while it builds, then finishes with its results.

CI checks the same path on a single-node Slurm cluster with slurmrestd:
`deploy/slurm/test/check_agent.py` covers the login-node agent, and
`deploy/slurm/test/check_slurmrest.py` covers the slurmrest agent, running as a user who
cannot read the runs directory. The slurmrest check includes a mid-run restart,
cancelling, a sweep as one array job, and replacing a rejected token without a restart.

## Upgrading

Set `AMBUILD_TAG` to the new tag in each machine's env file, then `docker compose
--env-file <file> pull && docker compose --env-file <file> up -d`. Upgrade the web
machine first, since its database schema is applied when it starts (and by `init`).
Then upgrade the agents and the controller to the same tag. Slurm agents can be
restarted at any time, since they take up their jobs again. A local agent stops its
running builds, uploads them as failed and reports them; its Compose file allows two
minutes for that.
