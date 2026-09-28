# Ambuild across several machines

One directory per machine role, each with a Compose file (or systemd unit) and an example
settings file. The images come from ghcr.io (`.github/workflows/publish-images.yml`).
[docs/deployment.md](../../docs/deployment.md) says which machine each role suits,
the order to set them up, the network, and which URLs and secrets go where.

| Directory | Runs | Needs |
| --- | --- | --- |
| `database/` | PostgreSQL | a password |
| `storage/` | SeaweedFS S3 (or use your own S3) | an access key pair |
| `web/` | the web GUI and API; `init` makes tables, bucket and agent tokens | database and storage settings |
| `campaigns/` | the campaign controller | the web URL and its token |
| `agent-slurmrest/` | a Slurm agent that submits through slurmrestd | the web URL and token, slurmrestd URL, a cluster user's JWT |
| `login-node/` | a Slurm agent on a login node (systemd user service) | the web URL and token, `upload.env` |
| `agent-local/` | a build machine outside Slurm | the web URL and token, database and storage settings |
| `cluster/` | the cluster user's `upload.env` | database and storage settings |

Copy each `*.env.example` to the name its Compose file says, fill it in, and `chmod 600` it.
Settings files hold secrets: keep them out of Git.
