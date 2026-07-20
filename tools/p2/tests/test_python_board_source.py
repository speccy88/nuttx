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
        cls.arch_kconfig = (ROOT / "arch/p2/Kconfig").read_text()
        cls.internal = (ROOT / "arch/p2/src/common/p2_internal.h").read_text()
        cls.common_make = (ROOT / "arch/p2/src/common/Make.defs").read_text()
        cls.serial = (ROOT / "arch/p2/src/common/p2_serial.c").read_text()
        cls.serial_api = (ROOT / "arch/p2/include/serial.h").read_text()
        cls.serial_rx = (ROOT / "arch/p2/src/common/p2_serial_rx.S").read_text()
        cls.defconfig = (BOARD / "configs/python/defconfig").read_text()

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

    def test_psram_container_transfers_yield_between_bounded_requests(self):
        transfer = self.source[
            self.source.index("static int p2_python_psram_transfer("):
            self.source.index("static int p2_python_source_read(")
        ]
        request = transfer.index("p2_psram_unified_transfer(")
        error = transfer.index("if (ret < 0)", request)
        yield_call = transfer.index("sched_yield();", error)
        advance = transfer.index("offset += chunk;", yield_call)
        self.assertLess(request, error)
        self.assertLess(error, yield_call)
        self.assertLess(yield_call, advance)

    def test_group_workspace_reuses_unpublished_overlay_slot(self):
        self.assertNotIn("g_p2_python_groups", self.source)
        for token in (
            "p2_python_stage_groups_in_overlay_slot",
            "CONFIG_P2_HUB_OVERLAY_GROUP_COUNT + 1u",
            "capacity < required_count",
            "return -ENOSPC",
            "(FAR struct p2_overlay_group_s *)__p2_overlay_slot_start",
            "config->group_workspace_count = required_count",
            "stats.ready || stats.transition || stats.current_depth != 0",
            'stage = "WORKSPACE"',
        ):
            self.assertIn(token, self.source)

        defconfig = (BOARD / "configs/python/defconfig").read_text()
        self.assertIn("CONFIG_P2_HUB_OVERLAY_GROUP_COUNT=256", defconfig)

        prepare = self.source[
            self.source.index("int board_cpython_runtime_prepare("):
            self.source.index("int board_cpython_tmpfs_validate(")
        ]
        workspace = prepare.index("p2_python_stage_groups_in_overlay_slot")
        raw_console = prepare.index('stage = "TERMIOS_RAW"')
        initialize = prepare.index("p2_python_container_initialize")
        publish = prepare.index("p2_python_publish(true)")
        self.assertLess(workspace, raw_console)
        self.assertLess(workspace, initialize)
        self.assertLess(initialize, publish)

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

    def test_failed_binary_transport_drains_before_releasing_raw_receive(self):
        self.assertIn("p2_python_drain_rxraw_frame", self.source)
        self.assertIn("p2_python_drain_rxraw_idle", self.source)
        self.assertIn("g_p2_uart_rx_dropped - dropped_base", self.source)
        self.assertIn("P2_PYTHON_RX_DRAIN_IDLE_TICKS", self.source)

        prepare = self.source[
            self.source.index("int board_cpython_runtime_prepare("):
            self.source.index("int board_cpython_tmpfs_validate(")
        ]
        failure = prepare.index("fail:")
        drain = prepare.index("p2_python_drain_rxraw_idle()", failure)
        release = prepare.index("p2_uart_rxraw_end()", drain)
        purge = prepare.index("p2_python_purge_input(fd)", release)
        restore = prepare.index("tcsetattr(fd, TCSANOW, &saved_termios)", purge)
        self.assertLess(drain, release)
        self.assertLess(release, purge)
        self.assertLess(purge, restore)

        receive = self.source[
            self.source.index("static int p2_python_receive("):
            self.source.index(
                "/********", self.source.index("static int p2_python_receive(")
            )
        ]
        self.assertEqual(receive.count("p2_python_drain_rxraw_frame("), 2)
        self.assertIn("header_consumed + payload_consumed", receive)

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

    def test_upload_crc_uses_the_resident_hub_accelerator(self):
        self.assertIn("#include <arch/hub_crc32.h>", self.source)
        self.assertIn(
            "return p2_hub_crc32_update(crc, data, size);", self.source
        )
        self.assertNotIn("P2_PYTHON_CRC_POLYNOMIAL", self.source)
        self.assertIn("calculated_crc != frame_crc", self.source)
        self.assertIn(
            "return (crc ^ UINT32_C(0xffffffff)) == expected_crc ? "
            "0 : -EBADMSG;",
            self.source,
        )

    def test_preamble_and_blocks_share_the_exclusive_raw_reader(self):
        self.assertNotIn("static int p2_python_read_exact(", self.source)
        self.assertNotIn("#include <poll.h>", self.source)
        prepare = self.source[
            self.source.index("int board_cpython_runtime_prepare("):
            self.source.index("int board_cpython_tmpfs_validate(")
        ]
        begin = prepare.index("ret = p2_uart_rxraw_begin()")
        ready = prepare.index("P2PY:UPLOAD:READY")
        header = prepare.index(
            "p2_python_read_rxraw_exact(header, sizeof(header)"
        )
        accept = prepare.index("P2PY:UPLOAD:ACCEPT")
        self.assertLess(begin, ready)
        self.assertLess(ready, header)
        self.assertLess(header, accept)

    def test_protocol_v3_has_64k_blocks_and_bounded_nack_retries(self):
        self.assertIn("#define P2_PYTHON_UPLOAD_PROTOCOL         3", self.source)
        self.assertIn("#define P2_PYTHON_UPLOAD_FRAME_SIZE       65536", self.source)
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

    def test_protocol_v3_binds_baud_and_proves_zero_rx_drops(self):
        self.assertIn("#if CONFIG_UART0_BAUD != 2000000", self.source)
        self.assertIn('"FRAME=%u:BAUD=%u\\r\\n"', self.source)
        self.assertIn("P2_PYTHON_UPLOAD_FRAME_SIZE, CONFIG_UART0_BAUD", self.source)
        self.assertIn(
            "extern volatile uint32_t g_p2_uart_rx_dropped", self.source
        )
        self.assertEqual(
            self.source.count("if (g_p2_uart_rx_dropped != 0)"), 3
        )
        self.assertIn('stage = "RX_BASELINE"', self.source)
        self.assertIn('stage = "RX_DROPS"', self.source)
        self.assertIn("CRC=%08lX:RXDROPS=0", self.source)

    def test_two_megabaud_stream_uses_exclusive_lower_ring_without_new_buffer(self):
        self.assertIn("#define BOARD_UART0_BAUD CONFIG_UART0_BAUD", self.header)
        self.assertIn("#include <nuttx/config.h>", self.internal)
        self.assertIn(
            "CONFIG_P2_SYSCLK_HZ + CONFIG_UART0_BAUD / 2",
            self.internal,
        )
        self.assertNotIn("BOARD_UART0_BAUD / 2", self.internal)
        self.assertIn("config P2_UART_RX_RING_SIZE", self.arch_kconfig)
        self.assertIn("range 256 2048", self.arch_kconfig)
        self.assertIn("default 256", self.arch_kconfig)
        self.assertIn(
            "CONFIG_P2_UART_RX_RING_SIZE must be a power of two",
            self.internal,
        )
        self.assertIn(
            "#define P2_UART_RX_RING_SIZE CONFIG_P2_UART_RX_RING_SIZE",
            self.internal,
        )
        self.assertIn("cmp     r2, ##P2_UART_RX_RING_SIZE wz", self.serial_rx)
        self.assertIn("and     r2, ##P2_UART_RX_RING_MASK", self.serial_rx)
        self.assertIn(
            "wrpin   #P2_UART_ASYNC_RX_MODE, #BOARD_CONSOLE_RX_PIN",
            self.serial_rx,
        )
        self.assertIn("mov     r5, ##P2_UART_RX_CONFIG", self.serial_rx)
        self.assertIn("wxpin   r5, #BOARD_CONSOLE_RX_PIN", self.serial_rx)
        self.assertIn("rdpin   r3, #BOARD_CONSOLE_RX_PIN", self.serial_rx)
        self.assertNotIn("rcr     r3, #1", self.serial_rx)
        self.assertNotIn("P2_UART_RX_BIT_TICKS - 8", self.serial_rx)
        self.assertIn("CONFIG_P2_UART_RX_RING_SIZE=1024", self.defconfig)
        self.assertIn("CONFIG_UART0_RXBUFSIZE=1280", self.defconfig)
        self.assertIn("CONFIG_UART0_BAUD=2000000", self.defconfig)
        self.assertIn("CONFIG_P2_HUB_OVERLAY_SLOT_SIZE=90112", self.defconfig)
        self.assertIn(
            "CONFIG_INTERPRETERS_CPYTHON_P2_OVERLAY_TELEMETRY_INTERVAL_MS=60000",
            self.defconfig,
        )
        self.assertIn(
            "A P2 Python logical upload block must fit in the Hub overlay slot",
            self.source,
        )
        self.assertIn("#include <arch/serial.h>", self.source)
        for name in (
            "p2_uart_rxraw_begin",
            "p2_uart_rxraw_read",
            "p2_uart_rxraw_end",
        ):
            self.assertIn(name, self.serial_api)
            self.assertIn(name, self.serial)
            self.assertIn(name, self.source)

        service_start = self.serial.index("static void p2_uart_service(void)\n{")
        service = self.serial[
            service_start:
            self.serial.index("#endif /* USE_SERIALDRIVER */", service_start)
        ]
        self.assertIn("receive = !g_p2_uart_priv.rxexclusive", service)
        begin = self.serial[
            self.serial.index("int p2_uart_rxraw_begin(void)"):
            self.serial.index("ssize_t p2_uart_rxraw_read(")
        ]
        self.assertIn("g_p2_uart_rx_tail != g_p2_uart_rx_head", begin)
        self.assertIn("g_p2_uart_priv.rxexclusive = true", begin)
        self.assertIn(
            "if (!g_p2_uart_priv.rxcog || g_p2_uart_rx_alive == 0)",
            begin,
        )
        self.assertNotIn("!g_p2_uart_priv.rxenabled", begin)
        raw_read = self.serial[
            self.serial.index("ssize_t p2_uart_rxraw_read("):
            self.serial.index("void p2_uart_rxraw_end(void)")
        ]
        self.assertIn("address >= P2_HUB_RAM_SIZE", raw_read)
        self.assertIn("size > P2_HUB_RAM_SIZE - address", raw_read)
        copy = raw_read.index("destination[index] =")
        commit = raw_read.index("g_p2_uart_rx_tail = tail + index + 1")
        self.assertLess(copy, commit)
        self.assertIn("available > P2_UART_RX_RING_SIZE", raw_read)

        prepare = self.source[
            self.source.index("int board_cpython_runtime_prepare("):
            self.source.index("int board_cpython_tmpfs_validate(")
        ]
        begin_call = prepare.index("ret = p2_uart_rxraw_begin()")
        ready = prepare.index("P2PY:UPLOAD:READY")
        accept = prepare.index("P2PY:UPLOAD:ACCEPT")
        receive = prepare.index("ret = p2_python_receive(")
        restore = prepare.index("p2_uart_rxraw_end()", receive)
        initialize = prepare.index("p2_python_container_initialize", restore)
        self.assertLess(begin_call, ready)
        self.assertLess(ready, accept)
        self.assertLess(accept, receive)
        self.assertLess(receive, restore)
        self.assertLess(restore, initialize)
        self.assertIn("if (rxraw)", prepare)
        self.assertEqual(1024 + 1280, 256 + 2048)
        self.assertGreater(65536, 1024)
        self.assertLessEqual(65536, 90112)
        self.assertIn(
            "p2_serial$(OBJEXT): P2_UNIFIED_MEMORY_FLAGS =",
            self.common_make,
        )

    def test_rx_cog_launch_returns_zero_only_on_coginit_success(self):
        launch = self.serial_rx[
            self.serial_rx.index("p2_uart_rx_cog_start:"):
            self.serial_rx.index(
                ".size p2_uart_rx_cog_start",
                self.serial_rx.index("p2_uart_rx_cog_start:"),
            )
        ]

        # COGINIT's automatic-new-cog mode clears C on success and sets C
        # when no free cog exists.  p2_serialinit() compares this assembly
        # return value with zero, so copying C (WRC), rather than !C (WRNC),
        # is part of the raw-reader availability contract.

        self.assertIn("coginit r0, r1                  wc", launch)
        self.assertIn("wrc     r31", launch)
        self.assertNotIn("wrnc", launch)
        self.assertIn("setq    #1\n        wrlong  r0, ptra++", launch)
        self.assertIn("setq    #1\n        rdlong  r0, --ptra", launch)
        self.assertLess(launch.index("wrlong  r0, ptra++"),
                        launch.index("mov     r0, #0x10"))
        self.assertLess(launch.index("wrc     r31"),
                        launch.index("rdlong  r0, --ptra"))
        self.assertIn("p2_uart_rx_cog_start() == 0", self.serial)
        self.assertIn("rxcog && g_p2_uart_rx_alive == 0", self.serial)
        self.assertIn(
            "g_p2_uart_priv.rxcog = rxcog && g_p2_uart_rx_alive != 0",
            self.serial,
        )

    def test_rx_cog_branches_use_absolute_cog_long_addresses(self):
        worker = self.serial_rx[
            self.serial_rx.index("p2_uart_rx_cog:"):
            self.serial_rx.index(
                ".size p2_uart_rx_cog",
                self.serial_rx.index("p2_uart_rx_cog:"),
            )
        ]

        # The worker is linked in byte-addressed Hub .text and copied to cog
        # RAM, whose PC advances in longs.  Ordinary local-label branches
        # retain Hub-relative byte displacements and jump to the wrong cog
        # instructions after relocation.

        wait = "#\\((.Lrx_wait - p2_uart_rx_cog) / 4)"
        drop = "#\\((.Lrx_drop - p2_uart_rx_cog) / 4)"
        self.assertEqual(3, worker.count(wait))
        self.assertEqual(1, worker.count(drop))
        self.assertNotIn("jmp     #.Lrx_wait", worker)
        self.assertNotIn("jmp     #.Lrx_drop", worker)

    def test_64k_upload_staging_does_not_enlarge_bss_or_zero_stack_scratch(self):
        self.assertNotIn("uint8_t buffer[P2_PYTHON_UPLOAD_FRAME_SIZE]", self.source)
        self.assertIn(
            "(FAR uint8_t *)config.group_workspace,\n"
            "                          config.contract.overlay_slot_size",
            self.source,
        )
        self.assertIn("#define P2_PYTHON_ZERO_SIZE               1000", self.source)
        self.assertIn("uint8_t zeroes[P2_PYTHON_ZERO_SIZE]", self.source)
        self.assertNotIn("uint8_t zeroes[P2_PYTHON_UPLOAD_FRAME_SIZE]", self.source)

    def test_uploaded_container_uses_exact_in_place_backing_contract(self):
        for token in (
            "config.source_is_backing = true",
            "config.source_backing_address = source.base",
            "config.source_backing_size = source.size",
            "P2PY:UPLOAD:RECEIVED",
            "P2PY:INIT:START:MODE=INPLACE",
            "P2PY:INIT:PASS:MODE=INPLACE",
        ):
            self.assertIn(token, self.source)

        prepare = self.source[
            self.source.index("int board_cpython_runtime_prepare("):
            self.source.index("int board_cpython_tmpfs_validate(")
        ]
        restored = prepare.index("raw_console = false")
        received = prepare.index("P2PY:UPLOAD:RECEIVED")
        init_start = prepare.index("P2PY:INIT:START:MODE=INPLACE")
        initialize = prepare.index("ret = p2_python_container_initialize(")
        publish = prepare.index("p2_python_publish(true)")
        init_pass = prepare.index("P2PY:INIT:PASS:MODE=INPLACE")
        upload_pass = prepare.index("P2PY:UPLOAD:PASS")
        self.assertLess(restored, received)
        self.assertLess(received, init_start)
        self.assertLess(init_start, initialize)
        self.assertLess(initialize, publish)
        self.assertLess(publish, init_pass)
        self.assertLess(init_pass, upload_pass)

    def test_protocol_v3_drains_known_payload_before_validation(self):
        receive = self.source[
            self.source.index("static int p2_python_receive("):
            self.source.index(
                "/********", self.source.index("static int p2_python_receive(")
            )
        ]
        payload_read = receive.index(
            "p2_python_read_rxraw_exact(buffer, expected_size,"
        )
        scheduler_lock = receive.index("sched_lock();")
        scheduler_unlock = receive.index("sched_unlock();", payload_read)
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

        self.assertLess(scheduler_lock, header_decode)
        self.assertLess(header_decode, payload_read)
        self.assertLess(payload_read, scheduler_unlock)
        self.assertLess(payload_read, rejection)
        self.assertLess(rejection, nack_write)
        self.assertLess(nack_write, target_write)
        self.assertLess(target_write, ack_write)
        self.assertNotIn("p2_python_read_rxraw_exact(buffer, frame_size", receive)
        self.assertIn("calculated_crc != frame_crc", receive)

    def test_protocol_v3_never_publishes_a_failed_upload(self):
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
            "p2_python_stage_groups_in_overlay_slot",
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
        self.assertIn("CONFIG_UART0_RXBUFSIZE=1280", defconfig)

    def test_python_profile_skips_automatic_site_processing(self):
        self.assertIn(
            "CONFIG_INTERPRETERS_CPYTHON_P2_DEFAULT_NO_SITE=y",
            self.defconfig,
        )

    def test_python_profile_uses_the_fixed_target_path_contract(self):
        self.assertIn(
            "CONFIG_INTERPRETERS_CPYTHON_P2_FIXED_PATH_CONFIG=y",
            self.defconfig,
        )
        self.assertIn(
            'CONFIG_INTERPRETERS_CPYTHON_PYTHONPATH="/tmp"',
            self.defconfig,
        )

    def test_python_heap_budget_reserves_user_allocator_bootstrap(self):
        defconfig = (BOARD / "configs/python/defconfig").read_text()
        self.assertIn("CONFIG_NSH_DISABLE_HELP=y", defconfig)
        prefix = "CONFIG_MM_KERNEL_HEAPSIZE="
        settings = [
            line for line in defconfig.splitlines() if line.startswith(prefix)
        ]
        self.assertEqual(settings, ["CONFIG_MM_KERNEL_HEAPSIZE=63232"])

        baseline = 65536
        predicted_runtime_resident_growth = 1700
        conservative_nsh_help_reclaim = 1860
        conservative_overlay_table_reclaim = 2048
        configured = int(settings[0].removeprefix(prefix))
        heap_reclaim = baseline - configured
        self.assertEqual(heap_reclaim, 2304)
        self.assertGreaterEqual(
            heap_reclaim
            + conservative_nsh_help_reclaim
            + conservative_overlay_table_reclaim
            - predicted_runtime_resident_growth,
            1024,
        )

    def test_fingerprint_has_a_real_noncode_input_section(self):
        self.assertIn("g_p2_python_build_fingerprint[32]", self.source)
        self.assertIn('section(".p2.python.fingerprint")', self.source)
        linker = (BOARD / "scripts/ld.script").read_text()
        self.assertIn("KEEP(*(.p2.python.fingerprint))", linker)


if __name__ == "__main__":
    unittest.main()
