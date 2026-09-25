# Test image with Poreblazer compiled from source, used to generate
# tests/test_data/poreblazer and to run testPoreblazer.testRealPoreblazer:
#
#   docker build -t ambuild-poreblazer -f tests/docker/poreblazer.Dockerfile .
#   docker run --rm -v "$PWD:/ambuild" -w /ambuild/tests -e PYTHONPATH=/ambuild \
#       -e PYTHONHASHSEED=0 ambuild-poreblazer python3 run_tests.py
FROM python:3.11-slim

ARG POREBLAZER_COMMIT=a753c72bf255da58a48a8898170f3307dac6a325
RUN apt-get update \
 && apt-get install -y --no-install-recommends gfortran make git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# The upstream Makefile sets no optimisation flags for gfortran
RUN git clone https://github.com/richardjgowers/poreblazer.git /opt/poreblazer \
 && cd /opt/poreblazer && git checkout "$POREBLAZER_COMMIT" \
 && cd src && make
RUN pip install --no-cache-dir numpy

ENV POREBLAZER_EXE=/opt/poreblazer/src/poreblazer.exe
