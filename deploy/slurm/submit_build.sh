#!/bin/bash
# Submit a recorded Ambuild build to Slurm, with its upload and, optionally,
# a Poreblazer fan-out over the pickles it writes.
#
#   submit_build.sh [--poreblazer] BUILD_SCRIPT [extra sbatch options for the build]
#
# Environment:
#   AMBUILD_RUNS_ROOT  shared directory for run directories (required)
#   POREBLAZER_EXE     needed with --poreblazer
#
# Jobs:  build ──afterany──> upload
#          └───afterok───> fanout ──> poreblazer array ──afterany──> upload
set -euo pipefail

poreblazer=0
if [ "${1:-}" = "--poreblazer" ]; then
    poreblazer=1
    shift
fi
script="$(realpath "${1:?usage: submit_build.sh [--poreblazer] BUILD_SCRIPT [sbatch options]}")"
shift
: "${AMBUILD_RUNS_ROOT:?set AMBUILD_RUNS_ROOT to a directory shared by all nodes}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

run_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
run_dir="$AMBUILD_RUNS_ROOT/$run_id"
export AMBUILD_SCRIPT="$script" AMBUILD_RUN_DIR="$run_dir" AMBUILD_RUN_ID="$run_id" \
    AMBUILD_SLURM_DIR="$here"

build=$(sbatch --parsable --export=ALL "$@" "$here/ambuild_build.sbatch")
upload=$(sbatch --parsable --dependency="afterany:$build" --export=ALL "$here/ambuild_upload.sbatch")
echo "run $run_id: build job $build, upload job $upload"
if [ "$poreblazer" -eq 1 ]; then
    : "${POREBLAZER_EXE:?set POREBLAZER_EXE for --poreblazer}"
    fanout=$(sbatch --parsable --dependency="afterok:$build" --export=ALL "$here/ambuild_fanout.sbatch")
    echo "run $run_id: Poreblazer fan-out job $fanout"
fi
echo "$run_dir"
