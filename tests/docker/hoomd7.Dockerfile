# HOOMD-blue 7 (CPU build from conda-forge; conda-forge publishes no MPI build) with
# Ambuild's test dependencies. Build from the repository root:
#   docker build -f tests/docker/hoomd7.Dockerfile -t ambuild-hoomd7 .
# and run the tests with the checkout mounted, e.g.
#   docker run --rm -v "$PWD:/ambuild" -w /ambuild/tests -e PYTHONPATH=/ambuild ambuild-hoomd7 python run_tests.py
FROM mambaorg/micromamba:2.3.2
ARG HOOMD_VERSION=7.2.0
RUN micromamba install -y -n base -c conda-forge \
        "python=3.12" "hoomd=${HOOMD_VERSION}=cpu*" numpy \
 && micromamba clean -a -y
ENV PATH=/opt/conda/bin:$PATH
