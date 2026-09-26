# Single-node Slurm cluster for testing the deploy/slurm scripts end to end.
# Built and run by deploy/docker-compose.yml (profile slurm-test).
FROM python:3.11-slim-bookworm AS poreblazer
RUN apt-get update && apt-get install -y --no-install-recommends gfortran make git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/st7ma784/poreblazer.git /src/poreblazer \
 && cd /src/poreblazer && git checkout 24d884e3a5b0f7d674faf9256e809844ad58aab1 \
 && cd src && make

FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        slurm-wlm munge python3 python3-pip python3-venv libgfortran5 libgomp1 procps \
 && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY --from=poreblazer /src/poreblazer/src/poreblazer.exe /opt/poreblazer/poreblazer.exe
COPY pyproject.toml setup.py README.md LICENSE /ambuild/
COPY ambuild /ambuild/ambuild
COPY tests /ambuild/tests
COPY services/ingest /src/ingest
RUN pip install --no-cache-dir /ambuild /src/ingest
COPY deploy/slurm /opt/ambuild-slurm
COPY deploy/slurm/test/slurm.conf deploy/slurm/test/cgroup.conf /etc/slurm/
RUN mkdir -p /var/spool/slurmctld /var/spool/slurmd /var/log/slurm /run/munge /shared/runs \
 && chown -R munge:munge /run/munge /etc/munge \
 && chmod +x /opt/ambuild-slurm/*.sh /opt/ambuild-slurm/*.sbatch /opt/ambuild-slurm/test/*.sh
ENTRYPOINT ["/opt/ambuild-slurm/test/entrypoint.sh"]
CMD ["/opt/ambuild-slurm/test/run_test.sh"]
