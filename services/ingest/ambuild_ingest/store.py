"""Upload run files to S3-compatible object storage (MinIO, S3)."""
import logging
import os

logger = logging.getLogger(__name__)


class ObjectStore:
    """Objects are stored as s3://<bucket>/<prefix>runs/<run_id>/<path>.

    Configured from the environment: AMBUILD_S3_BUCKET, AMBUILD_S3_PREFIX (optional),
    S3_ENDPOINT_URL (for MinIO) and the usual AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY.
    """

    def __init__(self, bucket=None, prefix=None, endpointUrl=None, client=None):
        self.bucket = bucket or os.environ["AMBUILD_S3_BUCKET"]
        self.prefix = prefix if prefix is not None else os.environ.get("AMBUILD_S3_PREFIX", "")
        if client is None:
            import boto3

            client = boto3.client(
                "s3", endpoint_url=endpointUrl or os.environ.get("S3_ENDPOINT_URL")
            )
        self.client = client

    def ensureBucket(self):
        """Create the bucket if it does not exist"""
        existing = [b["Name"] for b in self.client.list_buckets().get("Buckets", [])]
        if self.bucket not in existing:
            self.client.create_bucket(Bucket=self.bucket)

    def key(self, runId, relpath):
        return "{0}runs/{1}/{2}".format(self.prefix, runId, relpath)

    def uri(self, runId, relpath):
        return "s3://{0}/{1}".format(self.bucket, self.key(runId, relpath))

    def _storedSha256(self, key):
        from botocore.exceptions import ClientError

        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return head.get("Metadata", {}).get("sha256")

    def upload(self, runId, relpath, localPath, sha256):
        """Upload a file unless an object with the same sha256 is already stored.

        Returns True if the file was uploaded.
        """
        key = self.key(runId, relpath)
        if self._storedSha256(key) == sha256:
            return False
        self.client.upload_file(
            localPath, self.bucket, key, ExtraArgs={"Metadata": {"sha256": sha256}}
        )
        logger.debug("Uploaded %s", key)
        return True
