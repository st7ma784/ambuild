# Ambuild runtime image: Python 3.12, HOOMD-blue 7 and NumPy from conda-forge, with the
# ambuild package installed, in debian-slim. Three targets:
#
#   ambuild             Ambuild with HOOMD-blue
#   ambuild-poreblazer  the same plus Poreblazer (the default target)
#   ambuild-agent       that plus the web GUI's agent and ambuild-upload
#
#   docker build -t ambuild .
#   docker build --target ambuild -t ambuild:no-poreblazer .
#   docker build --build-arg HOOMD_VARIANT=gpu -t ambuild:gpu .
#
# Run a build script from the current directory:
#   docker run --rm -v "$PWD:/work" -w /work --user "$(id -u):$(id -g)" ambuild python build.py
#
# AMBUILD_PARAMS_DIR and AMBUILD_BLOCKS_DIR point at the parameter files and example
# building blocks shipped in the image (tests/params and tests/blocks); POREBLAZER_EXE
# at Poreblazer in the ambuild-poreblazer target.

ARG HOOMD_VERSION=7.2.0
ARG HOOMD_VARIANT=cpu
# Ambuild's fork of Poreblazer (FORK.md there), as built and tested by the fork's CI and
# published to GHCR; a sha- tag pins one commit. scripts/update_poreblazer.sh moves the pin.
ARG POREBLAZER_IMAGE=ghcr.io/st7ma784/poreblazer:sha-d70fa08

# --- conda-forge environment, pruned of headers, static libraries and caches
FROM mambaorg/micromamba:2.3.2 AS hoomd-env
USER root
ARG HOOMD_VERSION
ARG HOOMD_VARIANT
RUN micromamba create -y -p /opt/env -c conda-forge \
        "python=3.12" "hoomd=${HOOMD_VERSION}=${HOOMD_VARIANT}*" numpy \
 && micromamba clean -a -f -y \
 && find /opt/env \( -name "*.a" -o -name "*.pyc" -o -name "__pycache__" \) -prune -exec rm -rf {} + \
 && rm -rf /opt/env/include /opt/env/share/doc /opt/env/share/man /opt/env/conda-meta \
           /opt/env/lib/python3.12/site-packages/hoomd/pytest /opt/env/lib/python3.12/test

# --- Poreblazer: the fork's published image (built on Debian bookworm with its Makefile)
FROM ${POREBLAZER_IMAGE} AS poreblazer-build

# --- Ambuild
FROM debian:bookworm-slim AS ambuild
COPY --from=hoomd-env /opt/env /opt/env
ENV PATH=/opt/env/bin:$PATH \
    AMBUILD_PARAMS_DIR=/opt/ambuild/params \
    AMBUILD_BLOCKS_DIR=/opt/ambuild/blocks
COPY pyproject.toml setup.py README.md LICENSE /tmp/ambuild/
COPY ambuild /tmp/ambuild/ambuild
RUN pip install --no-cache-dir --no-deps /tmp/ambuild \
 && rm -rf /tmp/ambuild \
 && find /opt/env -name "__pycache__" -prune -exec rm -rf {} + \
 && useradd --create-home --uid 1000 ambuild
COPY tests/params /opt/ambuild/params
COPY tests/blocks /opt/ambuild/blocks
USER ambuild
WORKDIR /home/ambuild
CMD ["python"]

# --- Ambuild with Poreblazer
FROM ambuild AS with-poreblazer
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgfortran5 libgomp1 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=poreblazer-build /opt/poreblazer/poreblazer.exe /opt/poreblazer/poreblazer.exe
ENV POREBLAZER_EXE=/opt/poreblazer/poreblazer.exe
USER ambuild

# --- The web GUI's agent (services/agent): runs queued recipes in this container and
# uploads them with ambuild-upload (local backend), or submits them to a Slurm cluster
# through slurmrestd (slurmrest backend), sending the job scripts in /opt/ambuild-slurm
FROM with-poreblazer AS ambuild-agent
USER root
COPY services/ingest /tmp/ingest
COPY services/agent /tmp/agent
RUN pip install --no-cache-dir "/tmp/ingest[mlflow]" /tmp/agent \
 && rm -rf /tmp/ingest /tmp/agent \
 && find /opt/env -name "__pycache__" -prune -exec rm -rf {} +
COPY deploy/slurm/*.sh deploy/slurm/*.sbatch /opt/ambuild-slurm/
USER ambuild
ENV AMBUILD_AGENT_DIR=/home/ambuild/agent AMBUILD_SLURM_DIR=/opt/ambuild-slurm
CMD ["ambuild-agent"]

# --- Ambuild with Poreblazer (the last stage, so the default target)
FROM with-poreblazer AS ambuild-poreblazer
