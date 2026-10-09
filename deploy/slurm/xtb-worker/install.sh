#!/bin/bash
# Make the xTB worker's environment for a cluster (docs/xtb-spec.md): two conda
# environments under PREFIX, which must be on a filesystem the compute nodes see.
#
#   deploy/slurm/xtb-worker/install.sh PREFIX [--no-gfnff]
#
#   PREFIX/env   tblite and ASE: runs the worker (GFN1-xTB, GFN2-xTB)
#   PREFIX/xtb   the xtb binary, for GFN-FF (left out with --no-gfnff)
#
# It needs micromamba, mamba or conda on the PATH (CONDA_EXE names another), and network
# access to conda-forge: run it on a login node. The worker's code is this clone's
# ambuild/, read through PYTHONPATH, so the worker is always the version the builds run.
# It prints the settings for the jobs' environment (the agent's agent.env, or the shell
# that calls submit_build.sh --xtb), and checks the worker on a benzene ring.
set -euo pipefail

usage="usage: install.sh PREFIX [--no-gfnff]"
prefix="${1:?$usage}"
gfnff=1
[ "${2:-}" = "--no-gfnff" ] && gfnff=0
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"

conda="${CONDA_EXE:-}"
for name in micromamba mamba conda; do
    [ -z "$conda" ] && conda="$(command -v "$name" || true)"
done
[ -n "$conda" ] || { echo "No micromamba, mamba or conda on the PATH (or set CONDA_EXE)" >&2; exit 1; }

mkdir -p "$prefix"
prefix="$(cd "$prefix" && pwd)"
create() {  # environment file, directory
    if [ -d "$2/conda-meta" ]; then
        echo "$2 exists: leaving it (remove it to make it again)"
    elif [ "$(basename "$conda")" = micromamba ]; then
        "$conda" create -y -p "$2" -f "$1"
    else
        "$conda" env create -y -p "$2" -f "$1"
    fi
}
create "$here/environment.yml" "$prefix/env"
[ "$gfnff" = 1 ] && create "$here/xtb-environment.yml" "$prefix/xtb"

worker="env PYTHONPATH=$repo $prefix/env/bin/python -m ambuild.xtb_worker"
export XTB_WORKER="$worker"
[ "$gfnff" = 1 ] && export XTB_EXE="$prefix/xtb/bin/xtb"

# Check it: a benzene ring in a 12 A cell, with each method installed
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
PYTHONPATH="$repo" "$prefix/env/bin/python" - "$work/benzene.xyz" <<'PY'
import math, sys
rows = []
for k in range(6):
    for symbol, r in (("C", 1.395), ("H", 2.48)):
        rows.append("{0} {1:.6f} {2:.6f} 6.000000".format(symbol, 6 + r * math.cos(math.pi / 3 * k), 6 + r * math.sin(math.pi / 3 * k)))
with open(sys.argv[1], "w") as f:
    f.write('12\nLattice="12.0 0.0 0.0 0.0 12.0 0.0 0.0 0.0 12.0" Properties=species:S:1:pos:R:3 pbc="T T T"\n')
    f.write("\n".join(rows) + "\n")
PY
methods="gfn1"
[ "$gfnff" = 1 ] && methods="gfn1 gfnff"
for method in $methods; do
    if ! OMP_NUM_THREADS=1 $worker "$work/benzene.xyz" --out "$work/$method.json" --method "$method" > "$work/$method.log" 2>&1; then
        tail -n 30 "$work/$method.log" >&2
        echo "The worker failed with $method" >&2
        exit 1
    fi
    "$prefix/env/bin/python" -c "import json, sys; d = json.load(open(sys.argv[1])); print('checked', d['method'], d['program'], d['program_version'], 'largest force %.3f eV/A' % d['fmax_eV_A'])" "$work/$method.json"
done

echo
echo "Add to the jobs' environment (agent.env, or export before submit_build.sh --xtb):"
echo "XTB_WORKER=$worker"
[ "$gfnff" = 1 ] && echo "XTB_EXE=$prefix/xtb/bin/xtb"
exit 0
