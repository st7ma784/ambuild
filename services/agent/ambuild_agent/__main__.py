"""ambuild-agent: run submissions queued in the Ambuild web GUI (local backend).

    AMBUILD_API_URL=http://ambuild-web:8000 AMBUILD_AGENT_TOKEN=... ambuild-agent

Environment:
    AMBUILD_API_URL             the web GUI
    AMBUILD_AGENT_TOKEN         this agent's token (ambuild-web init --agent NAME:local)
    AMBUILD_AGENT_DIR           working directory: blobs/, runs/, work/ (default ./agent)
    AMBUILD_AGENT_SLOTS         runs at once (default 1)
    AMBUILD_AGENT_POLL          seconds between passes (default 5)
    AMBUILD_AGENT_HEARTBEAT     seconds between heartbeats (default 30)
    AMBUILD_AGENT_UPLOAD        upload command (default ambuild-upload; empty: none)
    AMBUILD_AGENT_UPLOAD_EVERY  seconds between uploads of a running run (default 60; 0: at the end)
    AMBUILD_AGENT_KEEP_RUNS     1: keep run directories after they are uploaded
plus what ambuild-upload needs (DATABASE_URL, AMBUILD_S3_BUCKET, S3_ENDPOINT_URL, AWS_*),
which is not passed on to the builds, and POREBLAZER_EXE for recipes that run Poreblazer.
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
