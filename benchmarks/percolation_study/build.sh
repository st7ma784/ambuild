#!/bin/sh
# Build the percolation labelling study against Poreblazer's own percolation.f90.
#   ./build.sh [FORK_COMMIT]      needs git and gfortran (e.g. in ambuild-bench-pb:fork)
set -eu
commit="${1:-3ce66957b23428ecfbaa11904bf67139aaa4d9c7}"
here=$(cd "$(dirname "$0")" && pwd)
work="$here/build"
rm -rf "$work"
git clone -q https://github.com/st7ma784/poreblazer.git "$work/poreblazer"
git -C "$work/poreblazer" checkout -q "$commit"
cd "$work"
sed 's/\r$//' poreblazer/src/percolation.f90 > percolation.f90
# Poreblazer's labelling, with clusteranalysis exported for the study
sed -i 's/Public :: percolation_calc, percolation_calc_simple$/Public :: percolation_calc, percolation_calc_simple, clusteranalysis/' percolation.f90
# The same module with exact union-find labelling swapped in, renamed percolation_exact
cp percolation.f90 percolation_exact.f90
sed -i 's/, clusteranalysis$//' percolation_exact.f90
python3 "$here/make_exact.py" percolation_exact.f90
sed -i 's/^Module percolation$/Module percolation_exact/; s/^End Module percolation$/End Module percolation_exact/' percolation_exact.f90
sed -i 's/Public :: percolation_calc, percolation_calc_simple$/Public :: percolation_calc, percolation_calc_simple, clusteranalysis_exact/' percolation_exact.f90
gfortran -O2 percolation.f90 percolation_exact.f90 "$here/study.f90" -o study
gfortran -O2 percolation.f90 "$here/labelcheck.f90" -o labelcheck
gfortran -O2 percolation.f90 "$here/perctest.f90" -o perctest
echo "built $work/study, $work/labelcheck and $work/perctest"
