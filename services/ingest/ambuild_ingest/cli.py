"""ambuild-upload: load recorded Ambuild runs into PostgreSQL and object storage.

Examples:
    # After a Slurm build job (run as an afterany dependency), including child runs
    ambuild-upload --finalise --recursive /scratch/runs/$RUN_ID

    # K3s CronJob fallback: every run under /runs that has finished, or has not
    # changed for a day (its job presumably died), and was not uploaded since
    ambuild-upload --scan /runs --stale-after 86400

    # Log every run already in the database to MLflow (those not yet logged)
    ambuild-upload --mlflow-backfill

Configuration comes from the environment: DATABASE_URL (a libpq connection string),
AMBUILD_S3_BUCKET, AMBUILD_S3_PREFIX, S3_ENDPOINT_URL, AWS_ACCESS_KEY_ID and
AWS_SECRET_ACCESS_KEY. With MLFLOW_TRACKING_URI set, finished runs are also logged to MLflow
(ambuild_ingest.mlflow_log; AMBUILD_PUBLIC_URL adds a link back to each run's page), and
whenever any were logged, ambuild-review picks the runs worth looking at (AMBUILD_REVIEW=0:
don't; AMBUILD_REVIEW_CRITERIA: a criteria file).
"""
import argparse
import logging
import os
import sys

from ambuild_ingest.rundir import (
    DEFAULT_EXCLUDES,
    RunDirectory,
    findRunDirectories,
    isRunDirectory,
)

logger = logging.getLogger("ambuild_ingest")

# Written into a run directory after a successful upload of a finished run, so
# that --scan skips it. Holds the run's status when it was uploaded.
MARKER_FILE = ".ambuild-uploaded"


def parseArgs(argv):
    parser = argparse.ArgumentParser(
        prog="ambuild-upload",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("rundirs", nargs="*", help="run directories to upload")
    parser.add_argument(
        "--recursive", action="store_true", help="also upload run directories below each RUNDIR"
    )
    parser.add_argument(
        "--scan",
        metavar="DIR",
        help="upload every run below DIR that has finished and is not yet uploaded",
    )
    parser.add_argument(
        "--stale-after",
        type=float,
        metavar="SECONDS",
        help="with --scan, also upload (as incomplete) runs still marked running that "
        "have not changed for SECONDS",
    )
    parser.add_argument(
        "--finalise",
        action="store_true",
        help="record runs still marked running as incomplete: their process has ended",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        metavar="PATTERN",
        help="file name pattern not to upload (default: {0})".format(
            " ".join(DEFAULT_EXCLUDES)
        ),
    )
    parser.add_argument(
        "--init", action="store_true", help="create the database tables and the bucket, and exit"
    )
    parser.add_argument(
        "--mlflow-backfill", action="store_true",
        help="log every run in the database to MLflow (MLFLOW_TRACKING_URI) that isn't logged yet",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if not (args.rundirs or args.scan or args.init or args.mlflow_backfill):
        parser.error("give RUNDIR arguments, --scan DIR, --init or --mlflow-backfill")
    if args.stale_after is not None and not args.scan:
        parser.error("--stale-after needs --scan")
    return args


def selectRuns(args):
    """Return [(RunDirectory, finalise)] to upload"""
    excludes = args.exclude if args.exclude else None
    selected = []
    for path in args.rundirs:
        if not isRunDirectory(path):
            raise SystemExit("Not a recorded run directory: {0}".format(path))
        paths = findRunDirectories(path) if args.recursive else [path]
        selected += [(RunDirectory(p, excludes), args.finalise) for p in paths]
    if args.scan:
        for path in findRunDirectories(args.scan):
            rundir = RunDirectory(path, excludes)
            if os.path.isfile(os.path.join(path, MARKER_FILE)):
                continue
            if rundir.status != "running":
                selected.append((rundir, args.finalise))
            elif args.stale_after is not None and rundir.secondsSinceUpdate() > args.stale_after:
                selected.append((rundir, True))
    return selected


def mlflowLogger():
    """An MLflow logger, or None: without MLFLOW_TRACKING_URI, or with the server unreachable
    (a warning: uploads go on without it)"""
    from ambuild_ingest import mlflow_log

    uri = mlflow_log.trackingUri()
    if not uri:
        return None
    if not mlflow_log.reachable(uri):
        logger.warning("MLflow at %s is not reachable: runs are not logged there this time", uri)
        return None
    try:
        return mlflow_log.Logger(uri)
    except ImportError:
        logger.warning("MLFLOW_TRACKING_URI is set but mlflow-skinny is not installed: "
                       "pip install 'ambuild-ingest[mlflow]'")
        return None


def main(argv=None):
    args = parseArgs(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    import psycopg

    from ambuild_ingest.ingest import applySchema, uploadRun
    from ambuild_ingest.store import ObjectStore

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        applySchema(conn)
        store = ObjectStore()
        if args.init:
            store.ensureBucket()
            return 0
        tracker = mlflowLogger()
        if args.mlflow_backfill:
            if tracker is None:
                raise SystemExit("--mlflow-backfill needs MLFLOW_TRACKING_URI and a reachable MLflow server")
            from ambuild_ingest.mlflow_log import backfill

            logged, skipped, failed = backfill(conn, store, tracker)
            print("mlflow: logged {0}, already logged {1}, failed {2}".format(logged, skipped, failed))
            reviewAfter(tracker)
            if not (args.rundirs or args.scan):
                return 1 if failed else 0
        failures = 0
        loggedAny = False
        for rundir, finalise in selectRuns(args):
            try:
                summary = uploadRun(rundir, store, conn, finalise=finalise)
            except Exception:
                logger.exception("Failed to upload %s", rundir.path)
                conn.rollback()
                failures += 1
                continue
            if summary["status"] != "running":
                with open(os.path.join(rundir.path, MARKER_FILE), "w") as f:
                    f.write(summary["status"] + "\n")
                if tracker is not None:
                    from ambuild_ingest.mlflow_log import readRecipe

                    run = dict(rundir.run, status=summary["status"])
                    try:
                        loggedAny = tracker.log(run, rundir.events(), readRecipe(rundir)) is not None or loggedAny
                    except Exception:
                        logger.exception("Uploaded %s, but could not log it to MLflow", rundir.path)
            print("{run_id} {status} events={events} files={files} uploaded={uploaded}".format(**summary))
        if loggedAny:
            reviewAfter(tracker)
    return 1 if failures else 0


def reviewAfter(tracker):
    """Refresh the MLflow review (ambuild_ingest.review) after logging; never fails the upload"""
    if tracker is None or os.environ.get("AMBUILD_REVIEW", "1") == "0":
        return
    import contextlib
    import io

    from ambuild_ingest import review

    try:
        with contextlib.redirect_stdout(io.StringIO()):
            review.run(tracker.client, review.loadCriteria(os.environ.get("AMBUILD_REVIEW_CRITERIA") or None),
                       os.environ.get("AMBUILD_MLFLOW_URL", ""))
    except Exception:
        logger.exception("Could not refresh the MLflow review")


if __name__ == "__main__":
    sys.exit(main())
