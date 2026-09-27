"""ambuild-web: serve the web interface.

    ambuild-web [--host 0.0.0.0] [--port 8000]

Configuration comes from the environment, as for ambuild-upload: DATABASE_URL,
AMBUILD_S3_BUCKET, AMBUILD_S3_PREFIX, S3_ENDPOINT_URL and the AWS_* credentials; and
AMBUILD_WEB_TITLE, AMBUILD_WEB_CHECK_TIMEOUT (seconds).
"""
import argparse
import os


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=os.environ.get("AMBUILD_WEB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AMBUILD_WEB_PORT", "8000")))
    args = parser.parse_args()
    import uvicorn

    uvicorn.run("ambuild_web.app:app", host=args.host, port=args.port, proxy_headers=True)


if __name__ == "__main__":
    main()
