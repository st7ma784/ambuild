#!/bin/bash
# Submit a recorded Ambuild build to Slurm, with its upload and, optionally,
# a Poreblazer fan-out over the pickles it writes.
#
#   submit_build.sh [--poreblazer] BUILD_SCRIPT [extra sbatch options for the build]
#   submit_build.sh [--poreblazer] --recipe RECIPE.json [extra sbatch options]
#
# Environment:
#   AMBUILD_RUNS_ROOT  shared directory for run directories (required)
#   POREBLAZER_EXE     needed with --poreblazer, and for recipes that run Poreblazer
#   AMBUILD_RUN_ID     the run id to use (default: a new one); the web GUI's agent sets it
#   AMBUILD_BLOBS      for recipes: a shared directory of input files named by sha256
#   AMBUILD_SEED       for recipes: overrides the recipe's seed
#
# Jobs:  build ──afterany──> upload
#          └───afterok───> fanout ──> poreblazer array ──afterany──> upload
set -euo pipefail

poreblazer=0
if [ "${1:-}" = "--poreblazer" ]; then
    poreblazer=1
    shift
fi
usage="usage: submit_build.sh [--poreblazer] (BUILD_SCRIPT | --recipe RECIPE.json) [sbatch options]"
script="" recipe=""
if [ "${1:-}" = "--recipe" ]; then
    recipe="$(realpath "${2:?$usage}")"
    shift 2
else
    script="$(realpath "${1:?$usage}")"
    shift
fi
: "${AMBUILD_RUNS_ROOT:?set AMBUILD_RUNS_ROOT to a directory shared by all nodes}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

run_id="${AMBUILD_RUN_ID:-$(python3 -c 'import uuid; print(uuid.uuid4())')}"
run_dir="$AMBUILD_RUNS_ROOT/$run_id"
export AMBUILD_SCRIPT="$script" AMBUILD_RECIPE="$recipe" AMBUILD_RUN_DIR="$run_dir" AMBUILD_RUN_ID="$run_id" \
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
