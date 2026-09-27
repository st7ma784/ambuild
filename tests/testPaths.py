"""
Tests for finding Ambuild's data directories and Poreblazer (ab_util.paramsDir and friends)
"""
import os
import tempfile
import unittest
from unittest import mock

from context import ab_util
from context import BLOCKS_DIR, PARAMS_DIR


class Test(unittest.TestCase):
    def testEnvironmentWins(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"AMBUILD_PARAMS_DIR": d, "AMBUILD_BLOCKS_DIR": d}):
                self.assertEqual(ab_util.paramsDir(), d)
                self.assertEqual(ab_util.blocksDir(), d)

    @unittest.skipUnless(
        os.path.isdir(os.path.join(ab_util.AMBUILD_DIR, "tests", "params")),
        "Ambuild is installed without its checkout",
    )
    def testCheckoutFallback(self):
        """Without the variables, the checkout's tests/params and tests/blocks"""
        env = {k: v for k, v in os.environ.items() if k not in ("AMBUILD_PARAMS_DIR", "AMBUILD_BLOCKS_DIR")}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(os.path.realpath(ab_util.paramsDir()), os.path.realpath(PARAMS_DIR))
            self.assertEqual(os.path.realpath(ab_util.blocksDir()), os.path.realpath(BLOCKS_DIR))

    def testMissingDirectoryNamesTheVariable(self):
        with mock.patch.object(ab_util, "AMBUILD_DIR", tempfile.gettempdir()), \
                mock.patch.dict(os.environ, {"AMBUILD_PARAMS_DIR": ""}):
            with self.assertRaisesRegex(RuntimeError, "AMBUILD_PARAMS_DIR"):
                ab_util.paramsDir()

    def testPoreblazerExe(self):
        with mock.patch.dict(os.environ, {"POREBLAZER_EXE": "/somewhere/poreblazer.exe"}):
            self.assertEqual(ab_util.poreblazerExe(), "/somewhere/poreblazer.exe")
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "poreblazer.exe")
            with open(exe, "w") as f:
                f.write("#!/bin/sh\n")
            os.chmod(exe, 0o755)
            with mock.patch.dict(os.environ, {"POREBLAZER_EXE": "", "PATH": d}):
                self.assertEqual(os.path.realpath(ab_util.poreblazerExe()), os.path.realpath(exe))
            with mock.patch.dict(os.environ, {"POREBLAZER_EXE": "", "PATH": os.path.join(d, "empty")}):
                with self.assertRaisesRegex(RuntimeError, "POREBLAZER_EXE"):
                    ab_util.poreblazerExe()


if __name__ == "__main__":
    unittest.main()
