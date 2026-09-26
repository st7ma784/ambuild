# Lightweight HOOMD-blue 7 runtime (~580 MB) for Ambuild's tests and scripts: the
# conda-forge environment is built with micromamba, pruned, and copied into
# debian-slim. conda-forge publishes CPU and GPU builds of HOOMD-blue but no MPI build.
#
# Build from the repository root:
#   docker build -f tests/docker/hoomd7.Dockerfile -t ambuild-hoomd7 .
#   docker build -f tests/docker/hoomd7.Dockerfile --build-arg HOOMD_VARIANT=gpu -t ambuild-hoomd7-gpu .
# and run the tests with the checkout mounted:
#   docker run --rm -v "$PWD:/ambuild" -w /ambuild/tests -e PYTHONPATH=/ambuild ambuild-hoomd7 python run_tests.py
FROM mambaorg/micromamba:2.3.2 AS build
USER root
ARG HOOMD_VERSION=7.2.0
ARG HOOMD_VARIANT=cpu
RUN micromamba create -y -p /opt/env -c conda-forge \
        "python=3.12" "hoomd=${HOOMD_VERSION}=${HOOMD_VARIANT}*" numpy \
 && micromamba clean -a -f -y \
 && find /opt/env \( -name "*.a" -o -name "*.pyc" -o -name "__pycache__" \) -prune -exec rm -rf {} + \
 && rm -rf /opt/env/include /opt/env/share/doc /opt/env/share/man /opt/env/conda-meta \
           /opt/env/lib/python3.12/site-packages/hoomd/pytest /opt/env/lib/python3.12/test

FROM debian:bookworm-slim
COPY --from=build /opt/env /opt/env
ENV PATH=/opt/env/bin:$PATH
