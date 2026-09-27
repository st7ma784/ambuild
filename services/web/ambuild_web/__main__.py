"""ambuild-web: serve the web interface, or prepare its database.

    ambuild-web [--host 0.0.0.0] [--port 8000]
    ambuild-web init [--agent NAME:BACKEND[:TOKEN]] ...

`init` creates the web GUI's tables (the site also does this when it starts using them)
and registers agents: each --agent gets TOKEN, or a new token printed once. Registering
an existing name replaces its token.

Configuration comes from the environment, as for ambuild-upload: DATABASE_URL,
AMBUILD_S3_BUCKET, AMBUILD_S3_PREFIX, S3_ENDPOINT_URL and the AWS_* credentials; and
AMBUILD_WEB_TITLE, AMBUILD_WEB_CHECK_TIMEOUT (seconds).
"""
import argparse
import os
import sys


def init(agents):
    import psycopg

    from ambuild_web import queue

    parsed = []
    for spec in agents:
        parts = spec.split(":", 2)
        if len(parts) < 2 or not parts[0] or parts[1] not in queue.BACKENDS:
            sys.exit("--agent needs NAME:BACKEND[:TOKEN], BACKEND one of {0}: {1}".format(
                ", ".join(queue.BACKENDS), spec))
        parsed.append((parts[0], parts[1], parts[2] if len(parts) == 3 and parts[2] else None))
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        queue.applySchema(conn)
        for name, backend, token in parsed:
            if token is None:
                # a new token, printed once: it is stored only as a hash
                token = queue.newToken()
                print("{0}: {1}".format(name, token))
            else:
                print("{0}: registered".format(name))
            queue.registerAgent(conn, name, backend, token)
        conn.commit()
    print("web tables ready")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=os.environ.get("AMBUILD_WEB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AMBUILD_WEB_PORT", "8000")))
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("init", help="create the tables and register agents")
    p.add_argument("--agent", action="append", default=[], metavar="NAME:BACKEND[:TOKEN]")
    args = parser.parse_args()
    if args.command == "init":
        return init(args.agent)
    import uvicorn

    uvicorn.run("ambuild_web.app:app", host=args.host, port=args.port, proxy_headers=True)


if __name__ == "__main__":
    main()
