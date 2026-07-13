#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
BOARD_SRC = (
    ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb/src"
)


class StorageArbiterCTests(unittest.TestCase):
    def test_target_transition_engine(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            executable = pathlib.Path(tmpdir) / "storage-arbiter-test"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    f"-I{BOARD_SRC}",
                    str(BOARD_SRC / "p2_ec32mb_storage_arbiter.c"),
                    str(
                        ROOT
                        / "tools/p2/tests/p2_storage_arbiter_test.c"
                    ),
                    "-o",
                    str(executable),
                ],
                check=True,
                cwd=ROOT,
            )
            subprocess.run([str(executable)], check=True, cwd=ROOT)


if __name__ == "__main__":
    unittest.main()
