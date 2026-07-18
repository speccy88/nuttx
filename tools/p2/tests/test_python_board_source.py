#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
BOARD = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb"


class PythonBoardSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (BOARD / "src/p2_ec32mb_python.c").read_text()
        cls.header = (BOARD / "include/board.h").read_text()
        cls.makefile = (BOARD / "src/Makefile").read_text()
        cls.kconfig = (BOARD / "Kconfig").read_text()

    def test_transport_is_external_romfs_only(self):
        self.assertIn(
            "ifeq ($(CONFIG_INTERPRETERS_CPYTHON_EXTERNAL_ROMFS),y)",
            self.makefile,
        )
        self.assertIn("p2_ec32mb_python.c", self.makefile)
        self.assertIn("#ifndef CONFIG_INTERPRETERS_CPYTHON_EXTERNAL_ROMFS", self.source)
        self.assertNotIn("/dev/psram0", self.source)

    def test_backing_window_is_fixed_and_bounded(self):
        self.assertIn("default 2097152", self.kconfig)
        self.assertIn("BOARD_P2_PYTHON_CONTAINER_BASE", self.header)
        self.assertIn("BOARD_P2_PYTHON_CONTAINER_CAPACITY", self.header)
        self.assertIn("!= 2097152", self.source)
        self.assertIn("xbss_end > BOARD_P2_PYTHON_CONTAINER_BASE", self.source)

    def test_group_workspace_includes_resident_group_zero(self):
        expected = "g_p2_python_groups[CONFIG_P2_HUB_OVERLAY_GROUP_COUNT + 1]"
        self.assertIn(expected, self.source)
        self.assertIn(
            "config.group_workspace_count = CONFIG_P2_HUB_OVERLAY_GROUP_COUNT + 1",
            self.source,
        )

    def test_binary_transport_saves_uses_raw_and_restores_console(self):
        defconfig = (BOARD / "configs/python/defconfig").read_text()
        self.assertIn("CONFIG_SERIAL_TERMIOS=y", defconfig)
        self.assertEqual(self.source.count("tcgetattr(fd, &saved_termios)"), 1)
        self.assertEqual(self.source.count("cfmakeraw(&raw_termios)"), 1)
        self.assertEqual(self.source.count("tcsetattr(fd, TCSANOW, &saved_termios)"), 2)
        self.assertLess(
            self.source.index("tcsetattr(fd, TCSANOW, &raw_termios)"),
            self.source.index("P2PY:UPLOAD:READY"),
        )
        self.assertLess(
            self.source.index('stage = "TERMIOS_RESTORE"'),
            self.source.index("p2_python_container_initialize"),
        )

    def test_protocol_has_timeouts_crc_frames_and_acknowledgements(self):
        for token in (
            "P2_PYTHON_HEADER_TIMEOUT",
            "P2_PYTHON_UPLOAD_TIMEOUT",
            "p2_python_crc32_update",
            "P2_PYTHON_UPLOAD_FRAME_SIZE",
            "'P', '2', 'A', 'K'",
            "P2PY:UPLOAD:READY",
            "P2PY:UPLOAD:PASS",
            "P2PY:UPLOAD:FAIL",
        ):
            self.assertIn(token, self.source)

    def test_runtime_contract_wires_fingerprint_slot_groups_and_romfs(self):
        for token in (
            "__p2_python_fingerprint_start",
            "__p2_overlay_slot_start",
            "g_p2_python_groups",
            "p2_python_container_initialize",
            "p2_python_container_get_stdlib",
            "board_cpython_runtime_prepare",
            "board_cpython_romfs_image",
        ):
            self.assertIn(token, self.source)
        self.assertIn("board_cpython_runtime_prepare", self.header)
        self.assertIn("board_cpython_romfs_image", self.header)


if __name__ == "__main__":
    unittest.main()
