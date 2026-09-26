#!/bin/sh
# Point Ambuild's images at a published image of Ambuild's Poreblazer fork. By default,
# the newest: the commit of the last successful "Build and test" run on the fork's
# ambuild branch, whose image job pushed ghcr.io/st7ma784/poreblazer:sha-<commit>.
#
#   scripts/update_poreblazer.sh            needs gh
#   scripts/update_poreblazer.sh sha-9d4cbcc
#
# Prints the image. Rebuild the images and run the tests before committing the change.
set -eu
cd "$(dirname "$0")/.."
tag="${1:-}"
if [ -z "$tag" ]; then
    sha=$(gh api "repos/st7ma784/poreblazer/actions/workflows/ci.yml/runs?branch=ambuild&event=push&status=success&per_page=1" \
        --jq '.workflow_runs[0].head_sha')
    [ -n "$sha" ] && [ "$sha" != null ] || { echo "No successful fork build found" >&2; exit 1; }
    tag="sha-$(printf %s "$sha" | cut -c1-7)"
fi
image="ghcr.io/st7ma784/poreblazer:$tag"
for f in Dockerfile tests/docker/poreblazer.Dockerfile deploy/slurm/test/Dockerfile; do
    sed -i "s#^ARG POREBLAZER_IMAGE=.*#ARG POREBLAZER_IMAGE=$image#" "$f"
done
echo "$image"
