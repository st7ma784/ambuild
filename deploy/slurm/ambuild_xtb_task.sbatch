#!/bin/bash
# One xTB array task: restore the Nth pickle of AMBUILD_PICKLE_LIST and check it with xTB
# as a child run in AMBUILD_RUN_DIR/xtb_runs/ (docs/xtb-spec.md). The worker runs on
# OpenMP threads, one per CPU of the task. The fan-out job sets the CPUs
# (AMBUILD_XTB_CPUS), the memory and the time limit.
#SBATCH --job-name=ambuild-xtb
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --output=%x-%A_%a.out
set -euo pipefail
: "${AMBUILD_PICKLE_LIST:?}" "${AMBUILD_RUN_DIR:?}"
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"

pickle=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "$AMBUILD_PICKLE_LIST")
name=$(basename "$pickle")
name="${name%%.*}"
srun python "$AMBUILD_SLURM_DIR/xtb_task.py" "$pickle" \
    "$AMBUILD_RUN_DIR/xtb_runs/$name" "$AMBUILD_RUN_DIR/inputs/params"
