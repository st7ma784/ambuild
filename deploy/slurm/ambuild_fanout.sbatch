#!/bin/bash
# Fan Poreblazer out over the pickles a build wrote: one array task per pickle,
# each recorded as a child run of the build, then one upload of the lot.
# Runs after the build succeeds (afterok); the number of array tasks is only
# known once the build has written its pickles.
#   AMBUILD_RUN_DIR   the build's run directory
#   POREBLAZER_EXE    the Poreblazer executable on the compute nodes
#   AMBUILD_ARRAY_MAX optional: maximum array tasks running at once (default 20)
#   AMBUILD_ARRAY_CPUS optional: CPUs (OpenMP threads) per Poreblazer task (default 2)
#   AMBUILD_ARRAY_MEM optional: memory (MB) per Poreblazer task; default: estimated
#                      from the cell size (poreblazer_memory.py); "none": the site's default
#SBATCH --job-name=ambuild-fanout
#SBATCH --ntasks=1
#SBATCH --time=00:10:00
#SBATCH --output=%x-%j.out
set -euo pipefail
: "${AMBUILD_RUN_DIR:?}" "${POREBLAZER_EXE:?}"
here="${AMBUILD_SLURM_DIR:?submit with submit_build.sh}"

list="$AMBUILD_RUN_DIR/poreblazer_pickles.txt"
find "$AMBUILD_RUN_DIR" -maxdepth 1 -name 'step_*.pkl*' | sort -V > "$list"
count=$(wc -l < "$list")
if [ "$count" -eq 0 ]; then
    echo "No pickles in $AMBUILD_RUN_DIR; nothing to fan out"
    exit 0
fi

mem="${AMBUILD_ARRAY_MEM:-}"
if [ -z "$mem" ]; then
    mem=$(python "$here/poreblazer_memory.py" "$(head -n 1 "$list")" "$AMBUILD_RUN_DIR/inputs/params")
fi
[ "$mem" = none ] && mem=""
array=$(sbatch --parsable --array="0-$((count - 1))%${AMBUILD_ARRAY_MAX:-20}" \
    ${AMBUILD_ARRAY_CPUS:+--cpus-per-task="$AMBUILD_ARRAY_CPUS"} ${mem:+--mem="$mem"} \
    --export=ALL,AMBUILD_PICKLE_LIST="$list" "$here/ambuild_poreblazer_task.sbatch")
sbatch --parsable --dependency="afterany:$array" --export=ALL "$here/ambuild_upload.sbatch"
echo "Submitted Poreblazer array $array ($count tasks${mem:+, $mem MB each}) and its upload"
