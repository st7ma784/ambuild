#!/bin/bash
# Fan xTB checks out over the pickles a build wrote (docs/xtb-spec.md): one array task
# per pickle, each recorded as a child run of the build, then one upload of the lot.
# Runs after the build succeeds (afterok); what there is to check is only known once the
# build has written its pickles.
#   AMBUILD_RUN_DIR       the build's run directory
#   XTB_WORKER            optional: the worker's command (default: the jobs' python, if it
#                         has tblite; XTB_EXE names the xtb binary for gfnff)
#   AMBUILD_XTB_STEPS     optional: "last" (default), the build's final pickle only, or "all".
#                         A check costs minutes, and hours to relax, so "all" is for small cells
#   AMBUILD_XTB_METHOD, AMBUILD_XTB_MODE, AMBUILD_XTB_MAX_STEPS, AMBUILD_XTB_FMAX,
#   AMBUILD_XTB_MAX_ATOMS, AMBUILD_XTB_CHARGE
#                         optional: the check's settings (xtb_task.py)
#   AMBUILD_XTB_ARRAY_MAX optional: maximum array tasks running at once (default 20)
#   AMBUILD_XTB_CPUS      optional: CPUs (OpenMP threads) per task (default 4)
#   AMBUILD_XTB_MEM       optional: memory (MB) per task; default: estimated from the last
#                         pickle's atom count (xtb_memory.py); "none": the site's default
#   AMBUILD_XTB_TIME      optional: time limit per task (default: the site's)
#SBATCH --job-name=ambuild-xtb-fanout
#SBATCH --ntasks=1
#SBATCH --time=00:10:00
#SBATCH --output=%x-%j.out
set -euo pipefail
: "${AMBUILD_RUN_DIR:?}"
here="${AMBUILD_SLURM_DIR:?submit with submit_build.sh}"

list="$AMBUILD_RUN_DIR/xtb_pickles.txt"
find "$AMBUILD_RUN_DIR" -maxdepth 1 -name 'step_*.pkl*' | sort -V > "$list"
case "${AMBUILD_XTB_STEPS:-last}" in
    last) tail -n 1 "$list" > "$list.tmp" && mv "$list.tmp" "$list" ;;
    all) ;;
    *) echo "AMBUILD_XTB_STEPS must be last or all, not $AMBUILD_XTB_STEPS" >&2; exit 1 ;;
esac
count=$(wc -l < "$list")
if [ "$count" -eq 0 ]; then
    echo "No pickles in $AMBUILD_RUN_DIR; nothing to check"
    exit 0
fi

mem="${AMBUILD_XTB_MEM:-}"
if [ -z "$mem" ]; then
    # the estimate is the last line (restoring the cell logs to stdout)
    mem=$(python "$here/xtb_memory.py" "$(tail -n 1 "$list")" "$AMBUILD_RUN_DIR/inputs/params" | tail -n 1)
    case "$mem" in
        ''|*[!0-9]*) echo "No xTB memory estimate ($mem); using the site default" >&2; mem=none ;;
    esac
fi
[ "$mem" = none ] && mem=""
array=$(sbatch --parsable --array="0-$((count - 1))%${AMBUILD_XTB_ARRAY_MAX:-20}" \
    --cpus-per-task="${AMBUILD_XTB_CPUS:-4}" ${mem:+--mem="$mem"} ${AMBUILD_XTB_TIME:+--time="$AMBUILD_XTB_TIME"} \
    --export=ALL,AMBUILD_PICKLE_LIST="$list" "$here/ambuild_xtb_task.sbatch")
sbatch --parsable --dependency="afterany:$array" --export=ALL "$here/ambuild_upload.sbatch"
echo "Submitted xTB array $array ($count tasks${mem:+, $mem MB each}) and its upload"
