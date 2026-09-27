"""Reading run files from object storage. Files are streamed through this service rather
than handed out as presigned URLs, because the storage's address is usually internal to
the cluster (http://seaweedfs:8333) and users' browsers cannot reach it."""
import mimetypes
import posixpath

CHUNK = 256 * 1024

# Text types shown in the browser rather than downloaded (?inline=1)
TEXT_SUFFIXES = {".json", ".jsonl", ".csv", ".log", ".txt", ".xyz", ".car", ".cml", ".py", ".dat", ".atoms"}


def parseUri(uri):
    """(bucket, key) of an s3://bucket/key URI"""
    if not uri.startswith("s3://") or "/" not in uri[5:]:
        raise ValueError("Not an s3:// URI: {0}".format(uri))
    bucket, key = uri[5:].split("/", 1)
    return bucket, key


def client(settings):
    import boto3
    from botocore.config import Config

    return boto3.session.Session().client(
        "s3", endpoint_url=settings.s3_endpoint_url or None,
        config=Config(connect_timeout=settings.check_timeout, read_timeout=30, retries={"max_attempts": 2}))


def openObject(settings, uri):
    """(chunk iterator, size) for the object at uri"""
    bucket, key = parseUri(uri)
    body = client(settings).get_object(Bucket=bucket, Key=key)
    stream = body["Body"]

    def chunks():
        try:
            for chunk in stream.iter_chunks(CHUNK):
                yield chunk
        finally:
            stream.close()

    return chunks(), body.get("ContentLength")


def contentType(path, inline):
    if inline and posixpath.splitext(path)[1].lower() in TEXT_SUFFIXES:
        return "text/plain; charset=utf-8"
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def putObject(settings, key, data):
    """Store data (bytes) at key in the configured bucket; returns its s3:// URI"""
    client(settings).put_object(Bucket=settings.s3_bucket, Key=key, Body=data)
    return "s3://{0}/{1}".format(settings.s3_bucket, key)
