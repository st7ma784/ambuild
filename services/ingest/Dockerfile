# Build from the repository root:
#   docker build -f services/ingest/Dockerfile -t ambuild-ingest .
# The default target is the uploader; the "test" target adds Ambuild and the tests.
FROM python:3.12-slim AS uploader
RUN useradd --create-home --uid 1000 ambuild
COPY services/ingest /src/ingest
RUN pip install --no-cache-dir "/src/ingest[mlflow]"
USER ambuild
ENTRYPOINT ["ambuild-upload"]

FROM uploader AS test
USER root
COPY pyproject.toml setup.py README.md LICENSE /ambuild/
COPY ambuild /ambuild/ambuild
COPY tests /ambuild/tests
RUN pip install --no-cache-dir /ambuild "/src/ingest[test,mlflow]"
ENV AMBUILD_TESTS_DIR=/ambuild/tests
WORKDIR /src/ingest
USER ambuild
ENTRYPOINT []
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
