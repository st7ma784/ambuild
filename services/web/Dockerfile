# Build from the repository root:
#   docker build -f services/web/Dockerfile -t ambuild-web .
# The default target serves the site on port 8000; the "test" target runs the tests.
FROM python:3.12-slim AS web
RUN useradd --create-home --uid 1000 ambuild
COPY services/web /src/web
RUN pip install --no-cache-dir /src/web
USER ambuild
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
ENTRYPOINT ["ambuild-web"]

FROM web AS test
USER root
# The tests upload runs with ambuild-upload's own code, so the web app reads what it writes
COPY services/ingest /src/ingest
RUN pip install --no-cache-dir "/src/web[test]" /src/ingest
WORKDIR /src/web
USER ambuild
ENTRYPOINT []
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
