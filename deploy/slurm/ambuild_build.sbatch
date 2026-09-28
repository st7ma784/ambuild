#!/bin/bash
# Run one recorded Ambuild build. Submitted by submit_build.sh, which sets:
#   AMBUILD_SCRIPT   the build script; it must create its Cell with
#                    outputDir=os.environ["AMBUILD_RUN_DIR"], recordRun=True,
#                    runId=os.environ["AMBUILD_RUN_ID"] (see example_build.py)
#   AMBUILD_RECIPE   instead of AMBUILD_SCRIPT, a recipe (ambuild.recipe), built with
#                    AMBUILD_BLOBS (its input files) and AMBUILD_SEED if set
#   AMBUILD_RUN_DIR  the run directory, on a filesystem shared with the upload job
#   AMBUILD_RUN_ID   the run id, also the name of the run directory
#
# Ambuild runs as a single process. With more than one task (--ntasks=N), each HOOMD-blue
# optimisation or MD run is launched across all N tasks with srun, which needs an MPI build
# of HOOMD-blue 4+ (conda-forge's builds have no MPI; build HOOMD-blue from source). Set AMBUILD_SRUN_MPI to add --mpi=<type> (e.g. pmix) if your cluster
# needs it, or set AMBUILD_HOOMD_LAUNCHER yourself to override the launcher entirely.
# GPUs, if the HOOMD build supports them, are requested with e.g. --gpus-per-task=1.
#SBATCH --job-name=ambuild-build
#SBATCH --ntasks=1
#SBATCH --output=%x-%j.out
set -euo pipefail
: "${AMBUILD_RUN_DIR:?}" "${AMBUILD_RUN_ID:?}"

if [ "${SLURM_NTASKS:-1}" -gt 1 ] && [ -z "${AMBUILD_HOOMD_LAUNCHER+set}" ]; then
    export AMBUILD_HOOMD_LAUNCHER="srun --ntasks=$SLURM_NTASKS${AMBUILD_SRUN_MPI:+ --mpi=$AMBUILD_SRUN_MPI}"
fi
echo "HOOMD-blue launcher: ${AMBUILD_HOOMD_LAUNCHER-in-process}"

# With AMBUILD_LIVE_UPLOAD_EVERY=N (the slurmrest agent sets it), upload the run every N
# seconds while it builds, so its page in the web GUI is live. In a subshell, so the
# build never sees the upload credentials; the upload job still uploads it at the end.
if [ "${AMBUILD_LIVE_UPLOAD_EVERY:-0}" -gt 0 ]; then
    (
        set -a
        # shellcheck disable=SC1090
        source "${AMBUILD_UPLOAD_ENV:-$HOME/.config/ambuild/upload.env}"
        set +a
        while sleep "$AMBUILD_LIVE_UPLOAD_EVERY"; do
            if [ -f "$AMBUILD_RUN_DIR/run.json" ]; then ambuild-upload "$AMBUILD_RUN_DIR" >/dev/null 2>&1 || true; fi
        done
    ) &
fi

if [ -n "${AMBUILD_RECIPE:-}" ]; then
    # exec, so scancel's SIGTERM reaches the runner, which records the run as cancelled
    exec python -m ambuild.recipe run "$AMBUILD_RECIPE" --output "$AMBUILD_RUN_DIR" --run-id "$AMBUILD_RUN_ID" \
        ${AMBUILD_BLOBS:+--blobs "$AMBUILD_BLOBS"} ${AMBUILD_SEED:+--seed "$AMBUILD_SEED"}
fi
python "${AMBUILD_SCRIPT:?set AMBUILD_SCRIPT or AMBUILD_RECIPE}"
