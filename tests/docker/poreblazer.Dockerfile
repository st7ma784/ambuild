# Test image with Ambuild's Poreblazer fork (from its published image), used to generate
# tests/test_data/poreblazer and to run testPoreblazer.testRealPoreblazer:
#
#   docker build -t ambuild-poreblazer -f tests/docker/poreblazer.Dockerfile .
#   docker run --rm -v "$PWD:/ambuild" -w /ambuild/tests -e PYTHONPATH=/ambuild \
#       -e PYTHONHASHSEED=0 ambuild-poreblazer python3 run_tests.py
# Ambuild's fork, as built and tested by its CI (FORK.md there)
ARG POREBLAZER_IMAGE=ghcr.io/st7ma784/poreblazer:sha-d70fa08
FROM ${POREBLAZER_IMAGE} AS poreblazer

FROM python:3.11-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgfortran5 libgomp1 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=poreblazer /opt/poreblazer/poreblazer.exe /opt/poreblazer/poreblazer.exe
RUN pip install --no-cache-dir numpy

ENV POREBLAZER_EXE=/opt/poreblazer/poreblazer.exe
