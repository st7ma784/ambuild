# Poreblazer built with chosen compiler flags, plus Ambuild, for bench_poreblazer.py and
# profile_poreblazer.py. Build from the repository root, e.g.:
#   docker build -f benchmarks/poreblazer-flags.Dockerfile --build-arg OFLAGS="-O3 -march=native" -t ambuild-bench-pb:O3 .
#   docker build -f benchmarks/poreblazer-flags.Dockerfile --build-arg OFLAGS="-O2 -pg" --build-arg LINKERFLAGS=-pg -t ambuild-bench-pb:gprof .
FROM python:3.11-slim
ARG POREBLAZER_COMMIT=a753c72bf255da58a48a8898170f3307dac6a325
# Optimisation flags; empty keeps the upstream Makefile's gfortran default, "-O2 -unshared"
ARG OFLAGS=""
# Linker flags, e.g. -pg for a gprof build
ARG LINKERFLAGS=""
RUN apt-get update \
 && apt-get install -y --no-install-recommends gfortran make git ca-certificates binutils time \
 && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/richardjgowers/poreblazer.git /opt/poreblazer \
 && cd /opt/poreblazer && git checkout "$POREBLAZER_COMMIT" \
 && cd src \
 && make ${OFLAGS:+OFLAGS="$OFLAGS"} ${LINKERFLAGS:+LINKERFLAGS="$LINKERFLAGS"} \
 && make -n -B ${OFLAGS:+OFLAGS="$OFLAGS"} | grep -m1 -- "-c " > /opt/poreblazer/FLAGS
COPY pyproject.toml setup.py README.md LICENSE /ambuild/
COPY ambuild /ambuild/ambuild
COPY tests /ambuild/tests
COPY benchmarks /ambuild/benchmarks
RUN pip install --no-cache-dir /ambuild
ENV POREBLAZER_EXE=/opt/poreblazer/src/poreblazer.exe AMBUILD_TESTS_DIR=/ambuild/tests
