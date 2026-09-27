#!/bin/sh
# Lint the Helm chart and keep it far inside Fleet's and Helm's ~1 MiB limits.
#
#   scripts/check_chart_size.sh [CHART_DIR]          needs helm
#
# Fleet stores each bundle (every file under the GitRepo path) in etcd, and Helm stores
# each release (the packaged chart plus values and manifests) in a Secret; both objects
# are capped at about 1 MiB, and going over can fail without a clear error. Budgets, a
# fraction of that (override with the environment variables):
#   packaged chart      MAX_PACKAGE_BYTES   (default 64 KiB)
#   rendered manifests  MAX_RENDERED_BYTES  (default 256 KiB)
#   Fleet bundle files  MAX_BUNDLE_BYTES    (default 128 KiB)
set -eu
chart="${1:-deploy/helm/ambuild}"
maxPackage="${MAX_PACKAGE_BYTES:-65536}"
maxRendered="${MAX_RENDERED_BYTES:-262144}"
maxBundle="${MAX_BUNDLE_BYTES:-131072}"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

helm lint --strict "$chart" >"$tmp/lint.txt" || { cat "$tmp/lint.txt"; exit 1; }
helm package "$chart" --destination "$tmp" >/dev/null
package=$(cat "$tmp"/*.tgz | wc -c)
# Every optional template on, so the largest rendering is measured
helm template budget "$chart" --set ingress.enabled=true --set ingress.tlsSecret=tls >"$tmp/rendered.yaml"
rendered=$(wc -c <"$tmp/rendered.yaml")
bundle=$(find "$chart" -type f -exec cat {} + | wc -c)

status=0
check() {  # name size budget
    percent=$(( $2 * 100 / $3 ))
    if [ "$2" -gt "$3" ]; then
        printf 'FAIL  %-20s %8d bytes  over its budget of %d (%d%%)\n' "$1" "$2" "$3" "$percent"
        status=1
    else
        printf 'ok    %-20s %8d bytes  (%d%% of the %d-byte budget)\n' "$1" "$2" "$percent" "$3"
    fi
}
check "packaged chart" "$package" "$maxPackage"
check "rendered manifests" "$rendered" "$maxRendered"
check "Fleet bundle files" "$bundle" "$maxBundle"
exit $status
