# Test image with Poreblazer compiled from source, used to generate
# tests/test_data/poreblazer and to run testPoreblazer.testRealPoreblazer:
#
#   docker build -t ambuild-poreblazer -f tests/docker/poreblazer.Dockerfile .
#   docker run --rm -v "$PWD:/ambuild" -w /ambuild/tests -e PYTHONPATH=/ambuild \
#       -e PYTHONHASHSEED=0 ambuild-poreblazer python3 run_tests.py
FROM python:3.11-slim

# Ambuild's fork (upstream 3.0.5 with correct, enabled OpenMP)
ARG POREBLAZER_REPO=https://github.com/st7ma784/poreblazer.git
ARG POREBLAZER_COMMIT=8ed0c7035de32e4737f4aa701ac98507ab5404e9
RUN apt-get update \
 && apt-get install -y --no-install-recommends gfortran make git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# The fork's Makefile compiles with gfortran -O2 -fopenmp
RUN git clone "$POREBLAZER_REPO" /opt/poreblazer \
 && cd /opt/poreblazer && git checkout "$POREBLAZER_COMMIT" \
 && cd src && make
RUN pip install --no-cache-dir numpy

ENV POREBLAZER_EXE=/opt/poreblazer/src/poreblazer.exe
