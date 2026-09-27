"""ambuild-agent: run submissions queued in the Ambuild web GUI.

    AMBUILD_API_URL=http://ambuild-web:8000 AMBUILD_AGENT_TOKEN=... ambuild-agent

Backends (AMBUILD_AGENT_BACKEND):
    local  (default) each submission runs here, as a process, and is uploaded by the agent
    slurm  each submission is submitted with deploy/slurm/submit_build.sh from this login
           node; its upload job uploads it. The agent can stop and start again: it takes
           up its Slurm jobs where it left them.

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
  slurm:
    AMBUILD_RUNS_ROOT           shared directory for run directories (and staged inputs)
    AMBUILD_SLURM_DIR           deploy/slurm (submit_build.sh and the job scripts)
    AMBUILD_SLURM_PARTITION     partition for build jobs (default: the cluster's)
    AMBUILD_SLURM_OPTIONS       more sbatch options for build jobs, e.g. "--account=chem"
A recipe's "resources" (cpus, gpus, memory_mb, time) become sbatch options. Database and
storage credentials are never passed on to builds.
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
