# The MLflow tracking server for the Ambuild stack (docs/mlflow.md): runs in PostgreSQL
# (database "mlflow"), artifacts in the S3 store, served through the tracking server.
#   docker build -f deploy/mlflow/Dockerfile -t ambuild-mlflow deploy/mlflow
FROM python:3.12-slim
# SQLAlchemy 2.1 removed a pool class MLflow 2.22 imports
RUN pip install --no-cache-dir "mlflow==2.22.0" "sqlalchemy>=2,<2.1" "psycopg2-binary>=2.9" "boto3>=1.28" \
 && useradd --create-home --uid 1000 mlflow
COPY entrypoint.py /usr/local/bin/mlflow-entrypoint.py
USER mlflow
EXPOSE 5000
HEALTHCHECK --interval=10s --timeout=5s --retries=30 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=4)"
ENTRYPOINT ["python", "/usr/local/bin/mlflow-entrypoint.py"]
