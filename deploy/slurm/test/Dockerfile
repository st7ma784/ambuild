# Single-node Slurm cluster for testing the deploy/slurm scripts end to end.
# Built and run by deploy/docker-compose.yml (profile slurm-test).
# Ambuild's Poreblazer fork, as built and tested by its CI
ARG POREBLAZER_IMAGE=ghcr.io/st7ma784/poreblazer:sha-d70fa08
FROM ${POREBLAZER_IMAGE} AS poreblazer

FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        slurm-wlm slurmrestd slurm-wlm-jwt-plugin munge python3 python3-pip python3-venv libgfortran5 libgomp1 procps util-linux \
 && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY --from=poreblazer /opt/poreblazer/poreblazer.exe /opt/poreblazer/poreblazer.exe
COPY pyproject.toml setup.py README.md LICENSE /ambuild/
COPY ambuild /ambuild/ambuild
COPY tests /ambuild/tests
COPY services/ingest /src/ingest
COPY services/agent /src/agent
COPY services/campaigns /src/campaigns
RUN pip install --no-cache-dir /ambuild /src/ingest /src/agent /src/campaigns
COPY deploy/slurm /opt/ambuild-slurm
COPY deploy/demo /opt/ambuild-demo
COPY deploy/slurm/test/slurm.conf deploy/slurm/test/cgroup.conf /etc/slurm/
# ambuild: the cluster user whose jobs the slurmrestd agent submits (with a JWT);
# slurmrest: runs slurmrestd, which refuses to run as root
RUN mkdir -p /var/spool/slurmctld /var/spool/slurmd /var/log/slurm /run/munge /shared/runs /shared/rest-runs \
 && useradd --create-home --uid 1000 ambuild && useradd --system --no-create-home slurmrest \
 && chown ambuild:ambuild /shared/rest-runs && chmod 700 /shared/rest-runs \
 && chown -R munge:munge /run/munge /etc/munge \
 && chmod +x /opt/ambuild-slurm/*.sh /opt/ambuild-slurm/*.sbatch /opt/ambuild-slurm/test/*.sh
ENTRYPOINT ["/opt/ambuild-slurm/test/entrypoint.sh"]
CMD ["/opt/ambuild-slurm/test/run_test.sh"]
