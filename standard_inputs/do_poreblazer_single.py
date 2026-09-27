#!/usr/bin/env python
import os
from ambuild import ab_util

paramsDir = ab_util.paramsDir()
mycell = ab_util.cellFromPickle('step_1.pkl.gz', paramsDir=paramsDir)

poreblazerExe = ab_util.poreblazerExe()
mycell.poreblazer(poreblazerExe)
