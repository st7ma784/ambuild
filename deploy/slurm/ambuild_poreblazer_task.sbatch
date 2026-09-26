#!/bin/bash
# One Poreblazer array task: restore the Nth pickle of AMBUILD_PICKLE_LIST and
# run Poreblazer on it as a child run in AMBUILD_RUN_DIR/poreblazer_runs/.
# Ambuild's Poreblazer fork runs its two slowest steps on OpenMP threads, one per
# CPU of the task (docs/benchmarks.md: ~5x faster on 8 threads for dense cells).
# Override the CPUs with sbatch --cpus-per-task, or set AMBUILD_ARRAY_CPUS for
# the fan-out job to pass on.
#SBATCH --job-name=ambuild-poreblazer
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --output=%x-%A_%a.out
set -euo pipefail
: "${AMBUILD_PICKLE_LIST:?}" "${AMBUILD_RUN_DIR:?}" "${POREBLAZER_EXE:?}"
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"

pickle=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "$AMBUILD_PICKLE_LIST")
name=$(basename "$pickle")
name="${name%%.*}"
srun python "$AMBUILD_SLURM_DIR/poreblazer_task.py" "$pickle" \
    "$AMBUILD_RUN_DIR/poreblazer_runs/$name" "$AMBUILD_RUN_DIR/inputs/params"
