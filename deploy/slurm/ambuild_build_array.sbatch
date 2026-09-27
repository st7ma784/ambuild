#!/bin/bash
# One task of a recipe array job, submitted by submit_array.sh: builds line
# SLURM_ARRAY_TASK_ID + 1 of AMBUILD_TASKS ("RUN_ID RECIPE [SEED]") as a recorded run in
# AMBUILD_RUNS_ROOT/RUN_ID, with AMBUILD_BLOBS as the recipe's input files.
#SBATCH --job-name=ambuild-array
#SBATCH --ntasks=1
#SBATCH --output=%x-%A_%a.out
set -euo pipefail
: "${AMBUILD_TASKS:?}" "${AMBUILD_RUNS_ROOT:?}" "${SLURM_ARRAY_TASK_ID:?}"

read -r run_id recipe seed < <(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "$AMBUILD_TASKS") || true
: "${run_id:?no line $((SLURM_ARRAY_TASK_ID + 1)) in $AMBUILD_TASKS}" "${recipe:?}"
# exec, so scancel's SIGTERM reaches the runner, which records the run as cancelled
exec python -m ambuild.recipe run "$recipe" --output "$AMBUILD_RUNS_ROOT/$run_id" --run-id "$run_id" \
    ${AMBUILD_BLOBS:+--blobs "$AMBUILD_BLOBS"} ${seed:+--seed "$seed"}
