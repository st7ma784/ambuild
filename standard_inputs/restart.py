#!/usr/bin/env python
import os, sys

# This imports the builder cell module - this is the only module that should be required
from ambuild import ab_util

mycell = ab_util.cellFromPickle("step_628.pkl")
