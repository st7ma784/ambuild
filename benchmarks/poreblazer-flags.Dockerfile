# Poreblazer built with chosen compiler flags, plus Ambuild, for bench_poreblazer.py.
# Build from the repository root, e.g.:
#   docker build -f benchmarks/poreblazer-flags.Dockerfile --build-arg OFLAGS="-O3 -march=native" -t ambuild-bench-pb:O3 .
FROM python:3.11-slim
ARG POREBLAZER_COMMIT=a753c72bf255da58a48a8898170f3307dac6a325
# Optimisation flags; empty keeps the upstream Makefile's gfortran default, "-O2 -unshared"
ARG OFLAGS=""
RUN apt-get update \
 && apt-get install -y --no-install-recommends gfortran make git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/richardjgowers/poreblazer.git /opt/poreblazer \
 && cd /opt/poreblazer && git checkout "$POREBLAZER_COMMIT" \
 && cd src && if [ -n "$OFLAGS" ]; then make OFLAGS="$OFLAGS"; else make; fi  && make -n -B ${OFLAGS:+OFLAGS="$OFLAGS"} | grep -m1 -- "-c " > /opt/poreblazer/FLAGS
COPY pyproject.toml setup.py README.md LICENSE /ambuild/
COPY ambuild /ambuild/ambuild
COPY tests /ambuild/tests
COPY benchmarks /ambuild/benchmarks
RUN pip install --no-cache-dir /ambuild
ENV POREBLAZER_EXE=/opt/poreblazer/src/poreblazer.exe AMBUILD_TESTS_DIR=/ambuild/tests
