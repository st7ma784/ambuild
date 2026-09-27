"""Settings, from the environment (the same variables as ambuild-upload)."""
import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    s3_bucket: str = ""
    s3_prefix: str = ""
    s3_endpoint_url: str = ""  # empty: AWS itself
    title: str = "Ambuild"
    check_timeout: float = 3.0  # seconds, per dependency check

    @classmethod
    def fromEnvironment(cls):
        return cls(
            database_url=os.environ.get("DATABASE_URL", ""),
            s3_bucket=os.environ.get("AMBUILD_S3_BUCKET", ""),
            s3_prefix=os.environ.get("AMBUILD_S3_PREFIX", ""),
            s3_endpoint_url=os.environ.get("S3_ENDPOINT_URL", ""),
            title=os.environ.get("AMBUILD_WEB_TITLE", "Ambuild"),
            check_timeout=float(os.environ.get("AMBUILD_WEB_CHECK_TIMEOUT", "3")),
        )


_PASSWORD_IN_URL = re.compile(r"(://[^:/@\s]+:)[^@\s]+(@)")
_PASSWORD_PARAM = re.compile(r"(password\s*=\s*)('[^']*'|\S+)", re.IGNORECASE)


def redact(text):
    """text with any password in a connection string replaced by ***"""
    text = _PASSWORD_IN_URL.sub(r"\1***\2", text or "")
    return _PASSWORD_PARAM.sub(r"\1***", text)
