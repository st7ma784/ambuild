# The demo's agent with liminal added, for ion_map and conduction stages (docs/ion-maps.md,
# docs/conduction.md). Built with
# liminal's checkout (the submodule) as the context, on the agent image:
#   docker compose -f deploy/docker-compose.yml --profile web build agent
#   docker build -f deploy/demo/liminal.Dockerfile -t ambuild-agent-liminal external/liminal
# and used by deploy/docker-compose.liminal.yml. Not published while liminal is private.
ARG BASE=ambuild-agent
FROM ${BASE}
USER root
COPY . /tmp/liminal
RUN pip install --no-cache-dir "/tmp/liminal[map,conduct]" && rm -rf /tmp/liminal && liminal --help >/dev/null
USER ambuild
