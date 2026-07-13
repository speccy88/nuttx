#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

import build_artifact


class BuildArtifactTests(unittest.TestCase):
    def test_finalize_validate_and_detect_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for name in build_artifact.PASS_REQUIRED_FILES:
                path = root / name
                if name == "config":
                    path.write_text(
                        "CONFIG_P2_SYSCLK_HZ=180000000\n", encoding="utf-8"
                    )
                elif name == "nuttx.bin":
                    path.write_bytes(b"aligned P2 image")
                elif name in ("nuttx-source-status.txt",
                               "apps-source-status.txt"):
                    path.write_text("", encoding="utf-8")
                else:
                    path.write_text(name + "\n", encoding="utf-8")

            environment = {
                "P2_BUILD_ARTIFACT": str(root),
                "P2_BUILD_STATUS": "PASS",
                "P2_BUILD_EXIT_CODE": "0",
                "P2_BUILD_PROFILE": "flashboot",
                "P2_BUILD_STARTED_UTC": "2026-07-13T11:58:00Z",
                "P2_BUILD_ENDED_UTC": "2026-07-13T11:59:00Z",
                "P2_BUILD_COMMAND": "tools/p2/build.sh flashboot",
                "P2_BUILD_NUTTX_BRANCH": "codex/test",
                "P2_BUILD_NUTTX_COMMIT": "1" * 40,
                "P2_BUILD_NUTTX_COMMIT_AFTER": "1" * 40,
                "P2_BUILD_APPS_PATH": "/tmp/apps",
                "P2_BUILD_APPS_BRANCH": "codex/test",
                "P2_BUILD_APPS_COMMIT": "2" * 40,
                "P2_BUILD_APPS_COMMIT_AFTER": "2" * 40,
                "P2_BUILD_NUTTX_CLEAN": "1",
                "P2_BUILD_APPS_CLEAN": "1",
                "P2_BUILD_P2LLVM_ROOT": "/tmp/p2llvm",
                "P2_BUILD_COMPILER": "fixture clang",
                "P2_BUILD_JOBS": "1",
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                build_artifact.finalize_from_environment()

            result = build_artifact.load(
                root, image=root / "nuttx.bin", require_clean=True
            )
            self.assertEqual(result.profile, "flashboot")
            self.assertEqual(result.board_clock_hz, 180000000)
            self.assertTrue(result.source_clean)

            (root / "nuttx.bin").write_bytes(b"tampered")
            with self.assertRaisesRegex(
                build_artifact.BuildArtifactError, "size changed"
            ):
                build_artifact.load(root, require_clean=True)


if __name__ == "__main__":
    unittest.main()
