#!/bin/bash

# Run an Ambuild script in the HOOMD-Blue 2 container, using this checkout's ambuild package.
#
# Usage:
#   run_ambuild_docker.sh [docker run options] script.py
#
# The current directory is mounted at the same path in the container and used as the working
# directory, so the script and its output live there. AMBUILD_PARAMS_DIR, AMBUILD_BLOCKS_DIR and
# POREBLAZER_EXE are passed through when set; mount those paths too if they are outside the
# current directory, e.g.
#   run_ambuild_docker.sh --volume /data/params:/data/params my_build.py

# Get root dir and script arguments
run_dir="$PWD"
ambuild_dir="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." >/dev/null 2>&1 && pwd )"
script="${*: -1}"
extra_args=("${@:1:$(($# - 1))}")

# Run
docker run \
--rm \
--runtime=nvidia \
--volume "$run_dir":"$run_dir" \
--volume "$ambuild_dir/ambuild":/opt/ambuild/ambuild:ro \
--env PYTHONPATH=/opt/ambuild \
--env AMBUILD_PARAMS_DIR \
--env AMBUILD_BLOCKS_DIR \
--env POREBLAZER_EXE \
--workdir "$run_dir" \
"${extra_args[@]}" \
glotzerlab/software:2020.11.18-cuda10 \
python3 "$script"
