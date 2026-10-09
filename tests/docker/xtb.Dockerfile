# The xTB worker's environment (docs/xtb-spec.md), on top of what Ambuild's tests need:
# tblite (GFN1-xTB and GFN2-xTB) and ASE beside HOOMD-blue 7, and the xtb binary (GFN-FF)
# in an environment of its own, as conda-forge resolves it to a 2022 version that fails
# on periodic cells when it is installed beside tblite.
#
# Build from the repository root:
#   docker build -f tests/docker/xtb.Dockerfile -t ambuild-xtb .
# and run the tests with the checkout mounted:
#   AMBUILD_IMAGE=ambuild-xtb tests/run_tests_docker.sh python -m unittest testXtb
FROM mambaorg/micromamba:2.3.2 AS build
USER root
ARG HOOMD_VERSION=7.2.0
ARG XTB_VERSION=6.7.1
RUN micromamba create -y -p /opt/env -c conda-forge \
        "python=3.12" "hoomd=${HOOMD_VERSION}=cpu*" numpy tblite-python ase \
 && micromamba create -y -p /opt/xtb -c conda-forge "xtb=${XTB_VERSION}" \
 && micromamba clean -a -f -y \
 && find /opt/env /opt/xtb \( -name "*.a" -o -name "*.pyc" -o -name "__pycache__" \) -prune -exec rm -rf {} + \
 && rm -rf /opt/env/include /opt/env/share/doc /opt/env/share/man /opt/env/conda-meta \
           /opt/xtb/include /opt/xtb/share/doc /opt/xtb/share/man /opt/xtb/conda-meta \
           /opt/env/lib/python3.12/site-packages/hoomd/pytest /opt/env/lib/python3.12/test

FROM debian:bookworm-slim
COPY --from=build /opt/env /opt/env
COPY --from=build /opt/xtb /opt/xtb
ENV PATH=/opt/env/bin:$PATH XTB_EXE=/opt/xtb/bin/xtb
