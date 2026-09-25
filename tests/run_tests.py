#!/usr/bin/env python3
"""
Created on 14 May 2016

@author: jmht
"""
import os
import random
import sys
import unittest

# Ambuild must be installed first, e.g. `pip install -e .` from the repository root

# Many tests build random structures. Reseed before every test so that each test's
# random numbers do not depend on which tests ran before it. Set PYTHONHASHSEED too,
# as set iteration order also feeds the random choices.
SEED = int(os.environ.get("AMBUILD_TEST_SEED", "1"))


class SeededTestResult(unittest.TextTestResult):
    def startTest(self, test):
        random.seed(SEED)
        super(SeededTestResult, self).startTest(test)


sys.stderr.write("AMBUILD_TEST_SEED={0} PYTHONHASHSEED={1}{2}".format(SEED, os.environ.get("PYTHONHASHSEED"), os.linesep))

TEST_DIR = "."
VERBOSITY = 2
suite = unittest.TestLoader().discover(TEST_DIR)
if int(suite.countTestCases()) <= 0:
    msg = (
        "Could not find any tests to run in directory: {0}".format(TEST_DIR)
        + os.linesep
    )
    sys.stderr.write(msg)
    sys.exit(1)


result = unittest.TextTestRunner(
    verbosity=VERBOSITY, buffer=False, resultclass=SeededTestResult
).run(suite)
if result.wasSuccessful():
    sys.exit(0)
else:
    sys.exit(-1)
