# Build from the repository root:
#   docker build -f services/campaigns/Dockerfile -t ambuild-campaigns .
# The campaign controller: Optuna (TPE, random, grid, QMC). For the "gp" method, build
# with --build-arg EXTRAS=gp, which adds PyTorch (CPU; about 700 MB).
FROM python:3.12-slim
ARG EXTRAS=""
RUN useradd --create-home --uid 1000 ambuild
# ambuild.campaign needs only the standard library: no NumPy or HOOMD-blue from ambuild
COPY pyproject.toml setup.py README.md LICENSE /src/ambuild/
COPY ambuild /src/ambuild/ambuild
COPY services/campaigns /src/campaigns
RUN pip install --no-cache-dir --no-deps /src/ambuild \
 && if [ -n "$EXTRAS" ]; then pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu "/src/campaigns[$EXTRAS]"; \
    else pip install --no-cache-dir /src/campaigns; fi
USER ambuild
ENTRYPOINT ["ambuild-campaigns"]
