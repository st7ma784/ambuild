"""A recorded build that raises: its run must be uploaded as failed."""
import os

from ambuild import ab_cell

with ab_cell.Cell([20, 20, 20], paramsDir=os.environ["AMBUILD_PARAMS_DIR"],
                  outputDir=os.environ["AMBUILD_RUN_DIR"], recordRun=True,
                  runId=os.environ["AMBUILD_RUN_ID"]):
    raise RuntimeError("deliberate failure")
