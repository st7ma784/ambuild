#!/bin/bash
# Submit many recipe builds as one Slurm array job, with one upload job after it
# (the web GUI's agent does this for a sweep).
#
#   submit_array.sh TASKS_FILE [extra sbatch options for the array]
#
# TASKS_FILE has a line per array task, "RUN_ID RECIPE [SEED]"; task i builds line i+1
# into $AMBUILD_RUNS_ROOT/RUN_ID.
#
# Environment:
#   AMBUILD_RUNS_ROOT  shared directory for run directories (required)
#   AMBUILD_BLOBS      a shared directory of the recipes' input files, named by sha256
#   AMBUILD_ARRAY_MAX  most tasks running at once (default 50)
#   POREBLAZER_EXE     for recipes that run Poreblazer
#
# Jobs:  array (one task per line) ──afterany──> upload (every run directory)
set -euo pipefail
tasks="$(realpath "${1:?usage: submit_array.sh TASKS_FILE [sbatch options]}")"
shift
: "${AMBUILD_RUNS_ROOT:?set AMBUILD_RUNS_ROOT to a directory shared by all nodes}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

count=$(grep -c . "$tasks" || true)
if [ "$count" -eq 0 ]; then
    echo "No tasks in $tasks" >&2
    exit 1
fi
run_list="${tasks%.*}.rundirs"
awk -v root="$AMBUILD_RUNS_ROOT" 'NF {print root "/" $1}' "$tasks" > "$run_list"
export AMBUILD_TASKS="$tasks" AMBUILD_SLURM_DIR="$here"

array=$(sbatch --parsable --array="0-$((count - 1))%${AMBUILD_ARRAY_MAX:-50}" --export=ALL "$@" \
    "$here/ambuild_build_array.sbatch")
array="${array%%;*}"
upload=$(sbatch --parsable --dependency="afterany:$array" --export=ALL,AMBUILD_RUN_LIST="$run_list" \
    "$here/ambuild_upload.sbatch")
echo "array job $array ($count tasks), upload job ${upload%%;*}"
