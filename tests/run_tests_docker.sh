#!/bin/bash
# Run the test suite, including the HOOMD-blue tests, in the HOOMD-blue 7 image.
#
#   ./run_tests_docker.sh                                   # the whole suite
#   ./run_tests_docker.sh python -m unittest testCell.Test.testRunMD
#
# Builds the image from tests/docker/hoomd7.Dockerfile if it is missing
# (AMBUILD_IMAGE chooses another image).
set -euo pipefail
tests_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ambuild_dir="$(cd "$tests_dir/.." && pwd -P)"
image="${AMBUILD_IMAGE:-ambuild-hoomd7}"

if ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -f "$tests_dir/docker/hoomd7.Dockerfile" -t "$image" "$ambuild_dir"
fi
# Run as the calling user so files written to the mounts are theirs; with rootless Docker
# the container's root user already is the calling user.
user_args=(--user "$(id -u):$(id -g)")
if docker info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q rootless; then
    user_args=()
fi
args=("$@")
if [ ${#args[@]} -eq 0 ]; then
    args=(python run_tests.py)
fi

docker run --rm \
    "${user_args[@]}" \
    --volume "$ambuild_dir":/ambuild \
    --workdir /ambuild/tests \
    --env PYTHONPATH=/ambuild \
    --env PYTHONHASHSEED=0 \
    "$image" "${args[@]}"
