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
    def _run_transition_engine(self, mode3):
        with tempfile.TemporaryDirectory() as tmpdir:
            executable = pathlib.Path(tmpdir) / "storage-arbiter-test"
            command = [
                "cc",
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                f"-I{BOARD_SRC}",
            ]
            if mode3:
                command.append("-DCONFIG_P2_STORAGE_SD_MODE3=1")
            command.extend(
                [
                    str(BOARD_SRC / "p2_ec32mb_storage_arbiter.c"),
                    str(
                        ROOT
                        / "tools/p2/tests/p2_storage_arbiter_test.c"
                    ),
                    "-o",
                    str(executable),
                ]
            )
            subprocess.run(command, check=True, cwd=ROOT)
            subprocess.run([str(executable)], check=True, cwd=ROOT)

    def test_target_transition_engine_mode0(self):
        self._run_transition_engine(mode3=False)

    def test_target_transition_engine_mode3(self):
        self._run_transition_engine(mode3=True)


if __name__ == "__main__":
    unittest.main()
