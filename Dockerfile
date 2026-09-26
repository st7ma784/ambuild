# Ambuild runtime image: Python 3.12, HOOMD-blue 7 and NumPy from conda-forge, with the
# ambuild package installed, in debian-slim. Two targets:
#
#   ambuild             Ambuild with HOOMD-blue
#   ambuild-poreblazer  the same plus Poreblazer (the default target)
#
#   docker build -t ambuild .
#   docker build --target ambuild -t ambuild:no-poreblazer .
#   docker build --build-arg HOOMD_VARIANT=gpu -t ambuild:gpu .
#
# Run a build script from the current directory:
#   docker run --rm -v "$PWD:/work" -w /work --user "$(id -u):$(id -g)" ambuild python build.py
#
# AMBUILD_PARAMS_DIR and AMBUILD_BLOCKS_DIR point at the parameter files and example
# building blocks shipped in the image (tests/params and tests/blocks); POREBLAZER_EXE
# at Poreblazer in the ambuild-poreblazer target.

ARG HOOMD_VERSION=7.2.0
ARG HOOMD_VARIANT=cpu
# Ambuild's fork of Poreblazer: upstream 3.0.5 with correct, enabled OpenMP (FORK.md there)
ARG POREBLAZER_REPO=https://github.com/st7ma784/poreblazer.git
ARG POREBLAZER_COMMIT=24d884e3a5b0f7d674faf9256e809844ad58aab1

# --- conda-forge environment, pruned of headers, static libraries and caches
FROM mambaorg/micromamba:2.3.2 AS hoomd-env
USER root
ARG HOOMD_VERSION
ARG HOOMD_VARIANT
RUN micromamba create -y -p /opt/env -c conda-forge \
        "python=3.12" "hoomd=${HOOMD_VERSION}=${HOOMD_VARIANT}*" numpy \
 && micromamba clean -a -f -y \
 && find /opt/env \( -name "*.a" -o -name "*.pyc" -o -name "__pycache__" \) -prune -exec rm -rf {} + \
 && rm -rf /opt/env/include /opt/env/share/doc /opt/env/share/man /opt/env/conda-meta \
           /opt/env/lib/python3.12/site-packages/hoomd/pytest /opt/env/lib/python3.12/test

# --- Poreblazer (Ambuild's fork), compiled with its Makefile: gfortran -O2 -fopenmp
# (-O2 was the fastest build measured in docs/benchmarks.md)
FROM debian:bookworm-slim AS poreblazer-build
ARG POREBLAZER_REPO
ARG POREBLAZER_COMMIT
RUN apt-get update \
 && apt-get install -y --no-install-recommends gfortran make git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN git clone "$POREBLAZER_REPO" /src/poreblazer \
 && cd /src/poreblazer && git checkout "$POREBLAZER_COMMIT" \
 && cd src && make && strip poreblazer.exe

# --- Ambuild
FROM debian:bookworm-slim AS ambuild
COPY --from=hoomd-env /opt/env /opt/env
ENV PATH=/opt/env/bin:$PATH \
    AMBUILD_PARAMS_DIR=/opt/ambuild/params \
    AMBUILD_BLOCKS_DIR=/opt/ambuild/blocks
COPY pyproject.toml setup.py README.md LICENSE /tmp/ambuild/
COPY ambuild /tmp/ambuild/ambuild
RUN pip install --no-cache-dir --no-deps /tmp/ambuild \
 && rm -rf /tmp/ambuild \
 && find /opt/env -name "__pycache__" -prune -exec rm -rf {} + \
 && useradd --create-home --uid 1000 ambuild
COPY tests/params /opt/ambuild/params
COPY tests/blocks /opt/ambuild/blocks
USER ambuild
WORKDIR /home/ambuild
CMD ["python"]

# --- Ambuild with Poreblazer
FROM ambuild AS ambuild-poreblazer
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgfortran5 libgomp1 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=poreblazer-build /src/poreblazer/src/poreblazer.exe /opt/poreblazer/poreblazer.exe
ENV POREBLAZER_EXE=/opt/poreblazer/poreblazer.exe
USER ambuild
