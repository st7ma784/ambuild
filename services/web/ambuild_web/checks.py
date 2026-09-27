"""Connectivity checks for the status page: each returns a Check, never raises."""
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from ambuild_web.config import redact

OK, WARN, FAIL = "ok", "warn", "fail"

# The tables ambuild-upload --init creates (services/ingest/ambuild_ingest/schema.sql)
RESULT_TABLES = ["runs", "events", "steps", "files", "pore_results"]


@dataclass
class Check:
    name: str
    state: str  # ok, warn or fail
    summary: str  # one line: what is wrong, or that all is well
    facts: dict = field(default_factory=dict)  # details shown on the card
    seconds: float = 0.0

    def asDict(self):
        return asdict(self)


def checkPostgres(settings):
    """Can we reach PostgreSQL, and does it hold Ambuild's tables?"""
    start = time.monotonic()
    if not settings.database_url:
        return Check("PostgreSQL", FAIL, "DATABASE_URL is not set")
    import psycopg

    timeoutMs = int(settings.check_timeout * 1000)
    try:
        conn = psycopg.connect(settings.database_url, connect_timeout=max(1, int(settings.check_timeout)),
                               options="-c statement_timeout={0}".format(timeoutMs))
    except Exception as exc:  # any failure is a result to show, not an error page
        return Check("PostgreSQL", FAIL, "Cannot connect: " + redact(_oneLine(exc)),
                     {}, time.monotonic() - start)
    facts = {}
    try:
        with conn, conn.cursor() as cur:
            facts["host"] = _host(conn)
            cur.execute("SELECT current_setting('server_version'), pg_database_size(current_database())")
            version, size = cur.fetchone()
            facts.update({"server": version, "size": _bytes(size)})
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = ANY(%s)",
                (RESULT_TABLES,),
            )
            present = {row[0] for row in cur.fetchall()}
            missing = [t for t in RESULT_TABLES if t not in present]
            if missing:
                return Check("PostgreSQL", WARN,
                             "Connected, but the Ambuild tables are missing ({0}): run ambuild-upload --init"
                             .format(", ".join(missing)), facts, time.monotonic() - start)
            cur.execute("SELECT status, count(*) FROM runs GROUP BY status ORDER BY status")
            byStatus = dict(cur.fetchall())
            cur.execute("SELECT count(*) FROM pore_results")
            facts["runs"] = sum(byStatus.values())
            facts["runs by status"] = ", ".join("{0} {1}".format(n, s) for s, n in byStatus.items()) or "none"
            facts["Poreblazer results"] = cur.fetchone()[0]
    except Exception as exc:
        return Check("PostgreSQL", FAIL, "Connected, but a query failed: " + redact(_oneLine(exc)),
                     facts, time.monotonic() - start)
    return Check("PostgreSQL", OK, "Connected; {0} runs recorded".format(facts["runs"]),
                 facts, time.monotonic() - start)


def checkS3(settings):
    """Can we reach the bucket, and write, read and delete an object in it?"""
    start = time.monotonic()
    if not settings.s3_bucket:
        return Check("Object storage", FAIL, "AMBUILD_S3_BUCKET is not set")
    facts = {"endpoint": settings.s3_endpoint_url or "AWS", "bucket": settings.s3_bucket}
    try:
        import boto3
        from botocore.config import Config

        # A fresh session each time: boto3's default session caches the credentials it
        # first found, so the check would not notice them change
        client = boto3.session.Session().client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            config=Config(connect_timeout=settings.check_timeout, read_timeout=settings.check_timeout,
                          retries={"max_attempts": 1}),
        )
        client.head_bucket(Bucket=settings.s3_bucket)
        key = "{0}_ambuild-web-probe/{1}".format(settings.s3_prefix, uuid.uuid4())
        body = b"ambuild-web status probe"
        client.put_object(Bucket=settings.s3_bucket, Key=key, Body=body)
        try:
            read = client.get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()
        finally:
            client.delete_object(Bucket=settings.s3_bucket, Key=key)
        if read != body:
            return Check("Object storage", FAIL, "The probe object read back differently", facts,
                         time.monotonic() - start)
        elapsed = time.monotonic() - start
        facts["write, read, delete"] = "{0:.0f} ms".format(elapsed * 1000)
        return Check("Object storage", OK, "Bucket reachable; write, read and delete work", facts, elapsed)
    except Exception as exc:
        return Check("Object storage", FAIL, _s3Reason(exc, settings), facts, time.monotonic() - start)


def runChecks(settings):
    """All the checks, run at the same time"""
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(checkPostgres, settings), pool.submit(checkS3, settings)]
        return [f.result() for f in futures]


def overall(checks):
    states = {c.state for c in checks}
    return FAIL if FAIL in states else WARN if WARN in states else OK


def _s3Reason(exc, settings):
    code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code")
    if code in ("404", "NoSuchBucket"):
        return "Bucket {0} does not exist: run ambuild-upload --init".format(settings.s3_bucket)
    if code in ("403", "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"):
        return "Access denied to bucket {0}: check the access key and secret ({1})".format(settings.s3_bucket, code)
    return "Cannot reach the storage: " + redact(_oneLine(exc))


def _oneLine(exc):
    text = str(exc).strip().splitlines()
    return "{0}: {1}".format(type(exc).__name__, text[0] if text else "")


def _host(conn):
    info = conn.info
    return "{0}:{1}/{2}".format(info.host, info.port, info.dbname)


def _bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "{0:.0f} {1}".format(n, unit) if unit == "B" else "{0:.1f} {1}".format(n, unit)
        n /= 1024.0
