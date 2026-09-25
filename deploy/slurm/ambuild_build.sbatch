#!/bin/bash
# Run one recorded Ambuild build. Submitted by submit_build.sh, which sets:
#   AMBUILD_SCRIPT   the build script; it must create its Cell with
#                    outputDir=os.environ["AMBUILD_RUN_DIR"], recordRun=True,
#                    runId=os.environ["AMBUILD_RUN_ID"] (see example_build.py)
#   AMBUILD_RUN_DIR  the run directory, on a filesystem shared with the upload job
#   AMBUILD_RUN_ID   the run id, also the name of the run directory
#
# Ambuild runs HOOMD from a single process: keep --ntasks=1. GPUs, if the
# HOOMD build supports them, are requested with --gres=gpu:1.
#SBATCH --job-name=ambuild-build
#SBATCH --ntasks=1
#SBATCH --output=%x-%j.out
set -euo pipefail
: "${AMBUILD_SCRIPT:?}" "${AMBUILD_RUN_DIR:?}" "${AMBUILD_RUN_ID:?}"

srun python "$AMBUILD_SCRIPT"
