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
        cls.boot = (BOARD / "src/p2_ec32mb_boot.c").read_text()
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
        self.assertIn("default 3145728", self.kconfig)
        self.assertIn("BOARD_P2_PYTHON_CONTAINER_BASE", self.header)
        self.assertIn("BOARD_P2_PYTHON_CONTAINER_CAPACITY", self.header)
        self.assertIn("!= 3145728", self.source)
        self.assertIn("!= 16777216", self.source)
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

    def test_binary_transport_purges_both_rx_layers_before_restore(self):
        self.assertIn("#define P2_PYTHON_RX_PURGE_TICKS          2", self.source)
        self.assertEqual(self.source.count("tcflush(fd, TCIFLUSH)"), 2)
        self.assertIn(
            "nxsched_usleep(P2_PYTHON_RX_PURGE_TICKS * USEC_PER_TICK)",
            self.source,
        )

        purge_calls = []
        cursor = 0
        while True:
            cursor = self.source.find("p2_python_purge_input(fd)", cursor)
            if cursor < 0:
                break
            purge_calls.append(cursor)
            cursor += 1

        restores = []
        cursor = 0
        restore_call = "tcsetattr(fd, TCSANOW, &saved_termios)"
        while True:
            cursor = self.source.find(restore_call, cursor)
            if cursor < 0:
                break
            restores.append(cursor)
            cursor += 1

        self.assertEqual(len(purge_calls), 2)
        self.assertEqual(len(restores), 2)
        self.assertLess(purge_calls[0], restores[0])
        self.assertLess(purge_calls[1], restores[1])
        self.assertIn("if (purge < 0 && ret >= 0)", self.source)
        self.assertIn('stage = "INPUT_PURGE"', self.source)

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
        self.assertIn("P2_PYTHON_FRAME_TIMEOUT", self.source)
        self.assertIn("frame_started", self.source)

    def test_protocol_v2_has_fixed_frames_and_bounded_nack_retries(self):
        self.assertIn("#define P2_PYTHON_UPLOAD_PROTOCOL         2", self.source)
        self.assertIn("#define P2_PYTHON_UPLOAD_FRAME_SIZE       1024", self.source)
        self.assertIn(
            "#define P2_PYTHON_UPLOAD_RETRANSMISSIONS  3", self.source
        )
        self.assertIn("'P', '2', 'N', 'K'", self.source)
        self.assertIn("uint8_t ack[8]", self.source)
        self.assertIn("uint8_t nack[8]", self.source)
        self.assertIn(
            "retransmissions <= P2_PYTHON_UPLOAD_RETRANSMISSIONS",
            self.source,
        )
        self.assertIn(
            "retransmissions == P2_PYTHON_UPLOAD_RETRANSMISSIONS",
            self.source,
        )
        self.assertIn("p2_python_putle32(nack + 4, received)", self.source)
        self.assertIn("p2_python_putle32(ack + 4, received)", self.source)
        self.assertIn(
            "only an explicit NACK at\n"
            "       * this committed offset authorizes the host to retransmit",
            self.source,
        )
        nack_write = self.source.index(
            "p2_python_write_all(STDOUT_FILENO, nack, sizeof(nack))"
        )
        retries_exhausted = self.source.index(
            "retransmissions == P2_PYTHON_UPLOAD_RETRANSMISSIONS"
        )
        self.assertLess(nack_write, retries_exhausted)

    def test_protocol_v2_binds_baud_and_proves_zero_rx_drops(self):
        self.assertIn("#if CONFIG_UART0_BAUD != 230400", self.source)
        self.assertIn('"FRAME=%u:BAUD=%u\\r\\n"', self.source)
        self.assertIn("P2_PYTHON_UPLOAD_FRAME_SIZE, CONFIG_UART0_BAUD", self.source)
        self.assertIn(
            "extern volatile uint32_t g_p2_uart_rx_dropped", self.source
        )
        self.assertEqual(
            self.source.count("if (g_p2_uart_rx_dropped != 0)"), 2
        )
        self.assertIn('stage = "RX_BASELINE"', self.source)
        self.assertIn('stage = "RX_DROPS"', self.source)
        self.assertIn("CRC=%08lX:RXDROPS=0", self.source)

    def test_protocol_v2_drains_known_payload_before_validation(self):
        receive = self.source[
            self.source.index("static int p2_python_receive("):
            self.source.index(
                "/********", self.source.index("static int p2_python_receive(")
            )
        ]
        payload_read = receive.index(
            "p2_python_read_exact(fd, buffer, expected_size,"
        )
        header_decode = receive.index(
            "frame_offset = p2_python_getle32(frame_header)"
        )
        target_write = receive.index("p2_python_target_write(")
        rejection = receive.index("if (!valid_header || calculated_crc != frame_crc)")
        nack_write = receive.index(
            "p2_python_write_all(STDOUT_FILENO, nack, sizeof(nack))"
        )
        ack_write = receive.index(
            "p2_python_write_all(STDOUT_FILENO, ack, sizeof(ack))"
        )

        self.assertLess(payload_read, header_decode)
        self.assertLess(header_decode, rejection)
        self.assertLess(rejection, nack_write)
        self.assertLess(nack_write, target_write)
        self.assertLess(target_write, ack_write)
        self.assertNotIn("p2_python_read_exact(fd, buffer, frame_size", receive)
        self.assertIn("calculated_crc != frame_crc", receive)

    def test_protocol_v2_never_publishes_a_failed_upload(self):
        prepare = self.source[
            self.source.index("int board_cpython_runtime_prepare("):
            self.source.index("int board_cpython_tmpfs_validate(")
        ]
        receive = prepare.index("ret = p2_python_receive(")
        initialize = prepare.index("ret = p2_python_container_initialize(")
        publish_ready = prepare.index("p2_python_publish(true)")
        failure_label = prepare.index("fail:")
        publish_empty = prepare.index("p2_python_publish(false)", failure_label)

        self.assertLess(receive, initialize)
        self.assertLess(initialize, publish_ready)
        self.assertLess(publish_ready, failure_label)
        self.assertGreater(publish_empty, failure_label)
        transfer_failure = prepare[
            receive:prepare.index('stage = "TERMIOS_RESTORE"')
        ]
        self.assertIn("if (ret < 0)", transfer_failure)
        self.assertIn("goto fail;", transfer_failure)

    def test_runtime_contract_wires_fingerprint_slot_groups_and_romfs(self):
        for token in (
            "__p2_python_fingerprint_start",
            "__p2_overlay_slot_start",
            "g_p2_python_groups",
            "p2_python_container_initialize",
            "p2_python_container_get_stdlib",
            "board_cpython_runtime_prepare",
            "board_cpython_tmpfs_validate",
            "board_cpython_romfs_image",
            "board_cpython_romdisk_register",
        ):
            self.assertIn(token, self.source)
        self.assertIn("board_cpython_runtime_prepare", self.header)
        self.assertIn("board_cpython_tmpfs_validate", self.header)
        self.assertIn("board_cpython_romfs_image", self.header)
        self.assertIn("board_cpython_romdisk_register", self.header)

    def test_external_romfs_is_buffered_not_fake_xip(self):
        ramdisk_header = (ROOT / "include/nuttx/drivers/ramdisk.h").read_text()
        ramdisk_source = (ROOT / "drivers/misc/ramdisk.c").read_text()
        defconfig = (BOARD / "configs/python/defconfig").read_text()

        self.assertIn("RDFLAG_NO_XIP", ramdisk_header)
        self.assertIn("RDFLAG_IS_NO_XIP", ramdisk_source)
        self.assertIn("return -ENOTTY", ramdisk_source)
        self.assertIn("RDFLAG_NO_XIP", self.source)
        self.assertIn("CONFIG_INTERPRETERS_CPYTHON_ROMFS_SECTORSIZE=512", defconfig)

    def test_python_tmpfs_is_automatic_and_fail_closed(self):
        defconfig = (BOARD / "configs/python/defconfig").read_text()
        self.assertIn(
            'nx_mount(NULL, CONFIG_LIBC_TMPDIR, "tmpfs", 0, NULL)',
            self.boot,
        )
        self.assertIn("P2PY:TMPFS:READY", self.boot)
        self.assertIn("PANIC();", self.boot)
        self.assertIn("statfs(CONFIG_LIBC_TMPDIR", self.source)
        self.assertIn("filesystem.f_type != TMPFS_MAGIC", self.source)
        self.assertIn("CONFIG_FS_HEAPSIZE=1048576", defconfig)
        self.assertIn("CONFIG_FS_HEAP_USER_BUFFER=y", defconfig)
        self.assertIn("CONFIG_UART0_RXBUFSIZE=2048", defconfig)

    def test_fingerprint_has_a_real_noncode_input_section(self):
        self.assertIn("g_p2_python_build_fingerprint[32]", self.source)
        self.assertIn('section(".p2.python.fingerprint")', self.source)
        linker = (BOARD / "scripts/ld.script").read_text()
        self.assertIn("KEEP(*(.p2.python.fingerprint))", linker)


if __name__ == "__main__":
    unittest.main()
