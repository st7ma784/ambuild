#!/bin/bash

# Run ambuild/ab_util.py with the given arguments in the HOOMD-blue 7 image, in the current directory.
#
# Usage:
#   run_ambuild_util_docker.sh [ab_util.py arguments]
#
# AMBUILD_IMAGE chooses the image (default ambuild-hoomd7, see tests/docker/hoomd7.Dockerfile);
# AMBUILD_GPU=1 passes the host's GPUs to the container.

ambuild_dir="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." >/dev/null 2>&1 && pwd )"
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
-it \
--rm \
"${gpu_args[@]}" \
"${user_args[@]}" \
--volume "$PWD":/work \
--volume "$ambuild_dir/ambuild":/opt/ambuild/ambuild:ro \
--env PYTHONPATH=/opt/ambuild \
--workdir /work \
"${AMBUILD_IMAGE:-ambuild-hoomd7}" \
python /opt/ambuild/ambuild/ab_util.py "$@"
