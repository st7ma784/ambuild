"""A recorded build that never finishes: the test cancels it, and its run
must be uploaded as incomplete."""
import os
import time

from ambuild import ab_cell

cell = ab_cell.Cell([20, 20, 20], paramsDir=os.environ["AMBUILD_PARAMS_DIR"],
                    outputDir=os.environ["AMBUILD_RUN_DIR"], recordRun=True,
                    runId=os.environ["AMBUILD_RUN_ID"])
time.sleep(3600)
