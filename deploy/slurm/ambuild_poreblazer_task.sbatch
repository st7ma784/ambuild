#!/bin/bash
# One Poreblazer array task: restore the Nth pickle of AMBUILD_PICKLE_LIST and
# run Poreblazer on it as a child run in AMBUILD_RUN_DIR/poreblazer_runs/.
#SBATCH --job-name=ambuild-poreblazer
#SBATCH --ntasks=1
#SBATCH --output=%x-%A_%a.out
set -euo pipefail
: "${AMBUILD_PICKLE_LIST:?}" "${AMBUILD_RUN_DIR:?}" "${POREBLAZER_EXE:?}"

pickle=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "$AMBUILD_PICKLE_LIST")
name=$(basename "$pickle")
name="${name%%.*}"
srun python "$AMBUILD_SLURM_DIR/poreblazer_task.py" "$pickle" \
    "$AMBUILD_RUN_DIR/poreblazer_runs/$name" "$AMBUILD_RUN_DIR/inputs/params"
