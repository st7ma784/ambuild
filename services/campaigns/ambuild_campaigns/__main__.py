"""ambuild-campaigns: steer the Ambuild web GUI's campaigns.

    AMBUILD_API_URL=http://ambuild-web:8000 AMBUILD_CAMPAIGNS_TOKEN=... ambuild-campaigns

Each pass, for each active campaign, it stops the campaign when its goal is met or its
budget spent, or proposes the next round's points (Optuna: tpe, gp, random; or grid, qmc)
and queues them. It keeps no state: stop and start it at any time.

Environment:
    AMBUILD_API_URL            the web GUI
    AMBUILD_CAMPAIGNS_TOKEN    its token: an agent of kind "campaigns" (the Agents page, or
                               ambuild-web init --agent NAME:campaigns)
    AMBUILD_CAMPAIGNS_POLL     seconds between passes (default 10)
"""
import argparse
import logging
import os

from ambuild_campaigns.controller import Api, Controller


def main():
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    url, token = os.environ.get("AMBUILD_API_URL"), os.environ.get("AMBUILD_CAMPAIGNS_TOKEN")
    if not url or not token:
        raise SystemExit("Set AMBUILD_API_URL and AMBUILD_CAMPAIGNS_TOKEN")
    Controller(Api(url, token), poll=float(os.environ.get("AMBUILD_CAMPAIGNS_POLL", "10"))).run()


if __name__ == "__main__":
    main()
