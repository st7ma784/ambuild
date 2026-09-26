#!/bin/bash
# Run one recorded Ambuild build. Submitted by submit_build.sh, which sets:
#   AMBUILD_SCRIPT   the build script; it must create its Cell with
#                    outputDir=os.environ["AMBUILD_RUN_DIR"], recordRun=True,
#                    runId=os.environ["AMBUILD_RUN_ID"] (see example_build.py)
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
: "${AMBUILD_SCRIPT:?}" "${AMBUILD_RUN_DIR:?}" "${AMBUILD_RUN_ID:?}"

if [ "${SLURM_NTASKS:-1}" -gt 1 ] && [ -z "${AMBUILD_HOOMD_LAUNCHER+set}" ]; then
    export AMBUILD_HOOMD_LAUNCHER="srun --ntasks=$SLURM_NTASKS${AMBUILD_SRUN_MPI:+ --mpi=$AMBUILD_SRUN_MPI}"
fi
echo "HOOMD-blue launcher: ${AMBUILD_HOOMD_LAUNCHER-in-process}"

python "$AMBUILD_SCRIPT"
