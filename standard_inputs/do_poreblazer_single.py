#!/usr/bin/env python
import os
from ambuild import ab_util

paramsDir = os.environ.get("AMBUILD_PARAMS_DIR", "/opt/paramsDir")
mycell = ab_util.cellFromPickle('step_1.pkl.gz', paramsDir=paramsDir)

poreblazerExe = os.environ.get("POREBLAZER_EXE", "/opt/poreblazer/src/poreblazer.exe")
mycell.poreblazer(poreblazerExe)
