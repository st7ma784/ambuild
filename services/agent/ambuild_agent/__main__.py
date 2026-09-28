"""ambuild-agent: run submissions queued in the Ambuild web GUI.

    AMBUILD_API_URL=http://ambuild-web:8000 AMBUILD_AGENT_TOKEN=... ambuild-agent

Backends (AMBUILD_AGENT_BACKEND):
    local      (default) each submission runs here, as a process, and is uploaded by the agent
    slurm      each submission is submitted with deploy/slurm/submit_build.sh from this login
               node; its upload job uploads it. The agent can stop and start again: it
               takes up its Slurm jobs where it left them.
    slurmrest  the same jobs, submitted through slurmrestd with a JWT, from anywhere that
               reaches it (e.g. a container): no Slurm commands, munge or shared
               filesystem needed here. The jobs stage their recipes on the cluster and
               upload their runs themselves, live and at the end.

Environment:
    AMBUILD_API_URL             the web GUI
    AMBUILD_AGENT_TOKEN         this agent's token (the web GUI's Agents page, or
                                ambuild-web init --agent NAME:BACKEND)
    AMBUILD_AGENT_BACKEND       local or slurm
    AMBUILD_AGENT_SLOTS         submissions at once (default 1 local, 50 slurm)
    AMBUILD_AGENT_POLL          seconds between passes (default 5 local, 15 slurm)
    AMBUILD_AGENT_HEARTBEAT     seconds between heartbeats (default 30)
    AMBUILD_AGENT_UPLOAD        upload command (default ambuild-upload; empty: none)
    AMBUILD_AGENT_UPLOAD_EVERY  seconds between uploads of a running run, for live
                                progress (default 60; 0: none)
    AMBUILD_UPLOAD_ENV          file of upload settings for those uploads (slurm default:
                                ~/.config/ambuild/upload.env, as the upload jobs use)
  local:
    AMBUILD_AGENT_DIR           working directory: blobs/, runs/, work/ (default ./agent)
    AMBUILD_AGENT_KEEP_RUNS     1: keep run directories after they are uploaded
  slurm and slurmrest:
    AMBUILD_RUNS_ROOT           shared directory for run directories (and staged inputs), on
                                the cluster
    AMBUILD_SLURM_DIR           deploy/slurm (submit_build.sh and the job scripts)
    AMBUILD_SLURM_PARTITION     partition for jobs (default: the cluster's)
  slurm:
    AMBUILD_SLURM_OPTIONS       more sbatch options for build jobs, e.g. "--account=chem"
  slurmrest:
    AMBUILD_SLURMRESTD_URL      e.g. http://slurm-head:6820
    AMBUILD_SLURM_USER          the cluster user the jobs run as
    AMBUILD_SLURM_JWT_FILE      a file holding that user's JWT (scontrol token), read for
                                every request; or AMBUILD_SLURM_JWT, the token itself
    AMBUILD_SLURMRESTD_VERSION  API version, e.g. v0.0.41 (default: the newest tested one
                                that slurmrestd offers: v0.0.42, v0.0.41, v0.0.40)
    AMBUILD_SLURMRESTD_CA       CA bundle for an https slurmrestd
    AMBUILD_SLURM_SETUP         a shell line each job runs first, e.g.
                                "source /opt/ambuild/venv/bin/activate"
    AMBUILD_SLURMREST_ENV       the jobs' environment as JSON, e.g. {"PATH": "...",
                                "POREBLAZER_EXE": "..."} (PATH default /usr/local/bin:/usr/bin:/bin)
    AMBUILD_SLURMREST_JOB       more job fields as JSON, e.g. {"account": "chem", "qos": "normal"}
    AMBUILD_SLURM_LOG_DIR       on the cluster, for the jobs' output (default AMBUILD_RUNS_ROOT)
    AMBUILD_SLURM_UPLOAD_ENV    on the cluster, the upload settings (default
                                ~/.config/ambuild/upload.env of the cluster user)
    AMBUILD_ARRAY_MAX           most array tasks running at once (default 50)
A recipe's "resources" (cpus, gpus, memory_mb, time) become sbatch options (job fields for
slurmrest). Database and storage credentials are never passed on to builds.
"""
import argparse
import logging

from ambuild_agent.agent import Agent, Config


def main():
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    Agent(Config.fromEnvironment()).run()


if __name__ == "__main__":
    main()
