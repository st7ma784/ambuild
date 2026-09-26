#!/bin/bash

# Run an Ambuild script in the HOOMD-blue 7 image, using this checkout's ambuild package.
#
# Usage:
#   run_ambuild_docker.sh [docker run options] script.py
#
# Build the image first (see tests/docker/hoomd7.Dockerfile), or set AMBUILD_IMAGE to another.
# Set AMBUILD_GPU=1 to pass the host's GPUs to the container (use the GPU build of the image).
# The current directory is mounted at the same path in the container and used as the working
# directory, so the script and its output live there. AMBUILD_PARAMS_DIR, AMBUILD_BLOCKS_DIR and
# POREBLAZER_EXE are passed through when set; mount those paths too if they are outside the
# current directory, e.g.
#   run_ambuild_docker.sh --volume /data/params:/data/params my_build.py

run_dir="$PWD"
ambuild_dir="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." >/dev/null 2>&1 && pwd )"
script="${*: -1}"
extra_args=("${@:1:$(($# - 1))}")
# Run as the calling user so files written to the mounts are theirs; with rootless Docker
# the container's root user already is the calling user.
user_args=(--user "$(id -u):$(id -g)")
if docker info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q rootless; then
    user_args=()
fi
gpu_args=()
if [ "${AMBUILD_GPU:-0}" = 1 ]; then
    gpu_args=(--gpus all)
fi

docker run \
--rm \
"${gpu_args[@]}" \
"${user_args[@]}" \
--volume "$run_dir":"$run_dir" \
--volume "$ambuild_dir/ambuild":/opt/ambuild/ambuild:ro \
--env PYTHONPATH=/opt/ambuild \
--env AMBUILD_PARAMS_DIR \
--env AMBUILD_BLOCKS_DIR \
--env POREBLAZER_EXE \
--workdir "$run_dir" \
"${extra_args[@]}" \
"${AMBUILD_IMAGE:-ambuild-hoomd7}" \
python "$script"
