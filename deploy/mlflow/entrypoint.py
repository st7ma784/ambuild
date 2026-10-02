"""Create MLflow's database and artifact bucket if they don't exist yet (a PostgreSQL volume
made before MLflow was added won't run init scripts again), then start the tracking server.

Environment: MLFLOW_DATABASE_URL (postgresql://user:password@host:port/mlflow),
MLFLOW_ARTIFACTS_DESTINATION (s3://bucket/prefix), MLFLOW_S3_ENDPOINT_URL and the AWS keys,
MLFLOW_WORKERS (default 2)."""
import os
import sys
import time
from urllib.parse import urlparse

import boto3
import psycopg2

db = os.environ["MLFLOW_DATABASE_URL"]
parsed = urlparse(db)
name = parsed.path.lstrip("/")
admin = parsed._replace(path="/postgres").geturl()
for attempt in range(60):
    try:
        conn = psycopg2.connect(admin)
        break
    except psycopg2.OperationalError:
        time.sleep(2)
else:
    sys.exit("PostgreSQL is not reachable")
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
    if cur.fetchone() is None:
        cur.execute('CREATE DATABASE "{0}"'.format(name.replace('"', "")))
        print("created database", name, flush=True)
conn.close()

artifacts = os.environ["MLFLOW_ARTIFACTS_DESTINATION"]
bucket = urlparse(artifacts).netloc
s3 = boto3.client("s3", endpoint_url=os.environ.get("MLFLOW_S3_ENDPOINT_URL"))
if bucket not in [b["Name"] for b in s3.list_buckets().get("Buckets", [])]:
    s3.create_bucket(Bucket=bucket)
    print("created bucket", bucket, flush=True)

os.execvp("mlflow", ["mlflow", "server", "--host", "0.0.0.0", "--port", "5000",
                     "--workers", os.environ.get("MLFLOW_WORKERS", "2"),
                     "--backend-store-uri", db, "--serve-artifacts", "--artifacts-destination", artifacts])
