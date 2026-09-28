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

# Live uploads while it builds, with AMBUILD_LIVE_UPLOAD_EVERY=N (as ambuild_build.sbatch)
if [ "${AMBUILD_LIVE_UPLOAD_EVERY:-0}" -gt 0 ]; then
    (
        set -a
        # shellcheck disable=SC1090
        source "${AMBUILD_UPLOAD_ENV:-$HOME/.config/ambuild/upload.env}"
        set +a
        while sleep "$AMBUILD_LIVE_UPLOAD_EVERY"; do
            if [ -f "$AMBUILD_RUNS_ROOT/$run_id/run.json" ]; then
                ambuild-upload "$AMBUILD_RUNS_ROOT/$run_id" >/dev/null 2>&1 || true
            fi
        done
    ) &
fi
# exec, so scancel's SIGTERM reaches the runner, which records the run as cancelled
exec python -m ambuild.recipe run "$recipe" --output "$AMBUILD_RUNS_ROOT/$run_id" --run-id "$run_id" \
    ${AMBUILD_BLOBS:+--blobs "$AMBUILD_BLOBS"} ${seed:+--seed "$seed"}
