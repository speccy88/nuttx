#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import os
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
BOARD = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb"
APPS = pathlib.Path(os.environ.get("NUTTX_APPS_DIR", ROOT.parent / "apps"))


class PsramSourceTests(unittest.TestCase):
    def test_board_profile_builds_and_registers_the_explicit_service(self):
        kconfig = (BOARD / "Kconfig").read_text()
        makefile = (BOARD / "src/Makefile").read_text()
        boot = (BOARD / "src/p2_ec32mb_boot.c").read_text()
        profile = (BOARD / "configs/psram/defconfig").read_text()
        app_kconfig = (APPS / "testing/p2psram/Kconfig").read_text()

        self.assertIn("config P2_EC32MB_PSRAM", kconfig)
        self.assertIn("depends on P2_SMARTPIN && BOARD_LATE_INITIALIZE", kconfig)
        self.assertIn("CSRCS += p2_ec32mb_psram.c", makefile)
        self.assertIn("ASRCS += p2_ec32mb_psram_service.S", makefile)
        self.assertIn("p2_psram_initialize();", boot)
        for setting in (
            "CONFIG_P2_SMARTPIN=y",
            "CONFIG_P2_EC32MB_PSRAM=y",
            "CONFIG_P2_EC32MB_PSRAM_FAULT_INJECT_TIMEOUT=y",
            "CONFIG_P2_EC32MB_PSRAM_COG_STACKSIZE=3072",
            "CONFIG_P2_EC32MB_PSRAM_MAX_REQUEST=65536",
            "CONFIG_P2_EC32MB_PSRAM_TIMEOUT_TICKS=500",
            "CONFIG_P2_EC32MB_PSRAM_CANCEL_GRACE_TICKS=100",
            "CONFIG_TESTING_P2PSRAM=y",
            "CONFIG_TESTING_P2PSRAM_RANDOM_COUNT=1024",
            "# CONFIG_SYSTEM_DD is not set",
        ):
            self.assertIn(setting, profile)
        self.assertIn(
            "depends on P2_EC32MB_PSRAM_FAULT_INJECT_TIMEOUT",
            app_kconfig,
        )

    def test_driver_exposes_explicit_service_not_native_memory(self):
        source = (BOARD / "src/p2_ec32mb_psram.c").read_text()
        header = (BOARD / "include/p2_ec32mb_psram.h").read_text()
        self.assertIn('P2_PSRAM_DEVICE_PATH          "/dev/psram0"', header)
        self.assertIn("register_driver(P2_PSRAM_DEVICE_PATH", source)
        self.assertIn("struct p2_psram_request_s", header)
        self.assertIn("int p2_psram_arm_timeout_stall(void);", header)
        for field in (
            "sequence",
            "operation",
            "external_address",
            "hub_buffer",
            "length",
            "status",
            "timeout_ticks",
            "completion",
        ):
            self.assertIn(field, header)
        for forbidden in ("kmm_addregion", "umm_addregion", "up_addregion"):
            self.assertNotIn(forbidden, source)

    def test_timing_leaf_has_fixed_board_protocol_and_refresh_measurement(self):
        source = (BOARD / "src/p2_ec32mb_psram_service.S").read_text()
        wire = (BOARD / "src/p2_ec32mb_psram_wire.h").read_text()
        verifier = (ROOT / "tools/p2/verify-elf.py").read_text()
        for token in (
            "P2_PSRAM_DATA_FIRST_PIN          40",
            "P2_PSRAM_DATA_LAST_PIN           55",
            "P2_PSRAM_CLOCK_PIN               56",
            "P2_PSRAM_CE_PIN                  57",
            "P2_PSRAM_CE_LOW_LIMIT_CYCLES     1440",
        ):
            self.assertIn(token, wire)
        for command in (
            "0xf5000000",
            "#0x66",
            "#0x99",
            "#0x35",
            "0xc0000000",
            "0xeb000000",
        ):
            self.assertIn(command, source)
        recover = source.index(".Lrecover:")
        sequence = tuple(
            source.index(token, recover)
            for token in (
                "##0xf5000000",
                "#0x66",
                "#0x99",
                "#0x35",
                "##0xc0000000",
            )
        )
        self.assertEqual(sequence, tuple(sorted(sequence)))
        self.assertEqual(source.count("calla   #\\.Lsend_qpi_command"), 2)
        self.assertIn(".Lsend_qpi_command:", source)
        self.assertIn("C0 has\n         * no address or data phase", source)
        self.assertIn("getct   r5", source)
        self.assertIn("P2_PSRAM_WIRE_CE_CYCLES_OFFSET", source)
        self.assertIn("8 + 5 + 2 = 15 clocks total", source)
        self.assertIn(
            "mov     r12, #5\n.Lread_dummy:\n"
            "        p2_psram_clock_dummy",
            source,
        )
        self.assertIn(
            "first clock-and-sample is the sixth wait clock", source
        )
        self.assertNotIn("read keeps CE low for 16", source)
        self.assertNotIn("six dummy clocks", source)
        self.assertIn("mov     r0, #0x30", source)
        self.assertIn("g_p2_psram_service_stack", source)
        self.assertIn("def verify_psram_service(", verifier)
        self.assertIn("verify_psram_service(elf, sections, symbols)", verifier)
        self.assertIn("def verify_psram_test_hotpath(", verifier)
        self.assertIn(
            "verify_psram_test_hotpath(elf, sections, symbols)", verifier
        )
        self.assertIn(
            "calls slow __mulsi3 in the PSRAM full-pass path", verifier
        )

    def test_cog_streamer_is_aligned_fragmented_and_below_tcem(self):
        source = (BOARD / "src/p2_ec32mb_psram.c").read_text()
        assembly = (BOARD / "src/p2_ec32mb_psram_service.S").read_text()
        wire = (BOARD / "src/p2_ec32mb_psram_wire.h").read_text()
        verifier = (ROOT / "tools/p2/verify-elf.py").read_text()

        def integer_define(name):
            match = re.search(
                rf"^#define\s+{name}\s+(\d+)\b", wire, re.MULTILINE
            )
            self.assertIsNotNone(match, name)
            return int(match.group(1))

        minimum = integer_define("P2_PSRAM_STREAM_MIN_BYTES")
        chip_wrap = integer_define("P2_PSRAM_STREAM_CHIP_WRAP_BYTES")
        fragment = integer_define("P2_PSRAM_STREAM_FRAGMENT_LONGS")
        lut_longs = integer_define("P2_PSRAM_STREAM_LUT_TABLE_LONGS")
        cog_longs = integer_define("P2_PSRAM_STREAM_COG_IMAGE_LONGS")
        cycles_per_long = integer_define("P2_PSRAM_STREAM_CYCLES_PER_LONG")
        read_clocks = integer_define("P2_PSRAM_STREAM_READ_QPI_CLOCKS")
        guard = integer_define("P2_PSRAM_STREAM_CE_GUARD_CYCLES")
        required_margin = integer_define("P2_PSRAM_STREAM_CE_MARGIN_CYCLES")
        stream_clock = integer_define("P2_PSRAM_STREAM_QPI_CLOCK_HZ")
        tcem_limit = integer_define("P2_PSRAM_CE_LOW_LIMIT_CYCLES")
        self.assertEqual(minimum, 32)
        self.assertEqual(chip_wrap, 32)
        self.assertEqual(fragment, chip_wrap)
        self.assertEqual(lut_longs, 16)
        self.assertEqual(cog_longs, 128)
        self.assertIn("P2_PSRAM_STREAM_FRAGMENT_BYTES == 128", source)
        self.assertEqual(stream_clock, 90_000_000)

        # The exact-board streamer consumes one logical long every four
        # system clocks.  The read is the slower direction: charge all 13 QPI
        # command/latency clocks at sysclk/2 plus a deliberately broad guard
        # for CE, XINIT, WYPIN, POLLXFI detection, and instruction latency.

        conservative_read_cycles = (
            fragment * cycles_per_long + read_clocks * 2 + guard
        )
        self.assertEqual(conservative_read_cycles, 218)
        self.assertGreaterEqual(
            tcem_limit - conservative_read_cycles, required_margin
        )
        self.assertEqual(1024 % fragment, 0)

        install = assembly[
            assembly.index("p2_psram_stream_install:") :
            assembly.index(".size p2_psram_stream_install")
        ]
        self.assertIn("p2_psram_stream_lut_table", install)
        self.assertIn("p2_psram_stream_cog_image", install)
        self.assertIn("setq2   #(P2_PSRAM_STREAM_LUT_TABLE_LONGS-1)", install)
        self.assertIn("rdlong  $0, r1", install)
        self.assertIn("setq    #(P2_PSRAM_STREAM_COG_IMAGE_LONGS-1)", install)
        self.assertIn("rdlong  $64, r1", install)
        self.assertEqual(install.count("augs    #0"), 2)

        wrapper = assembly[
            assembly.index("p2_psram_stream_transfer:") :
            assembly.index(".size p2_psram_stream_transfer")
        ]
        self.assertIn("setq    #15", wrapper)
        self.assertIn("calla   #\\P2_PSRAM_STREAM_COG_ENTRY", wrapper)

        table = assembly[
            assembly.index("p2_psram_stream_lut_table:") :
            assembly.index("p2_psram_stream_cog_image:")
        ]
        self.assertIn("0x00000000, 0x00001111", table)
        self.assertIn("0x0000eeee, 0x0000ffff", table)

        streamer = assembly[
            assembly.index("p2_psram_stream_cog_image:") :
            assembly.index("p2_psram_stream_cog_image_end:")
        ]
        for token in (
            "wrpin   #0x4a, #P2_PSRAM_CLOCK_PIN",
            "wxpin   #1, #P2_PSRAM_CLOCK_PIN",
            "rdfast  r0, r2",
            "wrfast  r0, r2",
            "0xb0d00000",
            "0xf0d00000",
            "xinit   ##0x20d00008, r9",
            "mov     r5, ##P2_PSRAM_STREAM_READ_DELAY_COMMAND",
            "setq    r0                       /* precise one-clock read delay */",
            "xcont   r5, #0                   /* sysclk-specific capture offset */",
            "pollxfi                         wc",
            "getct   r13",
            "P2_PSRAM_WIRE_CE_CYCLES_OFFSET",
        ):
            self.assertIn(token, streamer)
        read_launch = streamer[streamer.index(".Lstream_read_launch:") :]
        self.assertLess(read_launch.index("wypin   r11"),
                        read_launch.index("setq    r0"))
        self.assertLess(read_launch.index("setq    r0"),
                        read_launch.index("xcont   r5, #0"))
        self.assertNotIn("cmp     r6", read_launch[
            read_launch.index("wypin   r11") :
            read_launch.index("xcont   r5, #0")
        ])
        self.assertEqual(integer_define("P2_PSRAM_STREAM_READ_OFFSET"), 22)

        # Streamer output is ORed with OUTB, so stale scalar traffic must be
        # cleared with CE still high and before data direction/XINIT.  Keep
        # the proven read turnaround late: after the capture XCONT is queued.

        setup = streamer[
            streamer.index("outh    #P2_PSRAM_CE_PIN") :
            streamer.index(".Lstream_fragment:")
        ]
        self.assertLess(setup.index("andn    outb, r8"),
                        setup.index("or      dirb, r8"))
        self.assertLess(setup.index("andn    outb, r8"),
                        streamer.index("xinit   ##0x20d00008, r9"))
        self.assertLess(streamer.index("andn    outb, r8"),
                        streamer.index("drvl    #P2_PSRAM_CE_PIN"))
        read_capture = streamer[streamer.index(".Lstream_read_launch:") :]
        self.assertIn(
            "xcont   r10, #0\n        andn    dirb, r8", read_capture
        )
        self.assertIn(
            "and     r5, #(P2_PSRAM_STREAM_FRAGMENT_LONGS-1)", streamer
        )
        self.assertIn("sub     r3, r4", streamer)
        self.assertNotIn(".Lread_burst:", assembly)
        self.assertNotIn(".Lwrite_burst:", assembly)

        execute = source[
            source.index("static int p2_psram_execute(") :
            source.index("static bool p2_psram_take_request(")
        ]
        self.assertIn(
            "((address | (uintptr_t)buffer) & UINT32_C(3)) == 0", execute
        )
        self.assertIn("remaining >= P2_PSRAM_STREAM_MIN_BYTES", execute)
        self.assertIn("stream_bytes = remaining & ~UINT32_C(3)", execute)
        self.assertIn("p2_psram_stream_transfer(", execute)
        self.assertIn("__p2_xmem_psram_record_ce_cycles(", execute)
        self.assertEqual(execute.count("__p2_xmem_psram_hub_copy("), 2)
        self.assertNotIn("memcpy(", execute)
        self.assertNotIn("p2_psram_read_burst(", source)
        self.assertNotIn("p2_psram_write_burst(", source)
        transfer = execute.index("p2_psram_stream_transfer(")
        stack_check = execute.index("if (!p2_psram_stack_valid())", transfer)
        timing_check = execute.index(
            "__p2_xmem_psram_record_ce_cycles(", stack_check
        )
        self.assertLess(transfer, stack_check)
        self.assertLess(stack_check, timing_check)

        worker = source[
            source.index("void __p2_xmem_psram_service_worker(void)") :
            source.index("int p2_psram_initialize(")
        ]
        self.assertLess(
            worker.index("p2_psram_stream_install();"),
            worker.index("do\n    {"),
        )

        wire_execute = source[
            source.index(
                "static noinline_function int __p2_xmem_psram_wire_execute("
            ) :
            source.index("static int p2_psram_wire_operation(")
        ]
        self.assertLess(
            wire_execute.index("p2_psram_timing_leaf();"),
            wire_execute.index("if (!p2_psram_stack_valid())"),
        )
        self.assertLess(
            wire_execute.index("if (!p2_psram_stack_valid())"),
            wire_execute.index("ret = g_p2_psram_wire.status;"),
        )

        for symbol in (
            "p2_psram_stream_install",
            "p2_psram_stream_transfer",
            "p2_psram_stream_lut_table",
            "p2_psram_stream_cog_image",
            "p2_psram_stream_cog_image_end",
        ):
            self.assertIn(f'"{symbol}"', verifier)
        self.assertIn("0xFDC00040", verifier)
        self.assertIn("image_end - image_start != 512", verifier)

    def test_target_app_covers_required_fault_and_integrity_stages(self):
        source = (APPS / "testing/p2psram/p2psram_main.c").read_text()
        for marker in (
            "P2PSRAM:PROFILE:MAX_REQUEST=",
            "P2PSRAM:DIAG:WALKING:BIT=",
            "P2PSRAM:WALKING:PASS",
            "P2PSRAM:ADDRESS:PASS",
            "P2PSRAM:BOUNDARY:PASS",
            "P2PSRAM:RANDOM:PASS",
            "P2PSRAM:FULL:PASS",
            "P2PSRAM:THROUGHPUT",
            "P2PSRAM:CONCURRENT:PASS",
            "P2PSRAM:TIMEOUT:PASS",
            "P2PSRAM:RECOVERY:PASS",
            "P2PSRAM:CE_TIMING:PASS",
        ):
            self.assertIn(marker, source)
        self.assertIn("P2_PSRAM_SIZE_BYTES", source)
        self.assertIn("p2_psram_transfer", source)
        self.assertIn("(address >> 24) * 0x5bu", source)
        self.assertIn("struct p2psram_pattern_state_s", source)
        self.assertIn("p2psram_pattern_next(&state)", source)
        self.assertIn("state->address & UINT32_C(0xffffff)", source)
        self.assertIn("static inline_function uint8_t p2psram_pattern_next", source)
        self.assertIn("static noinline_function uint32_t", source)
        self.assertIn("times3 = value << 1;", source)
        self.assertIn("times3 += value;", source)
        self.assertIn("times25 = (times3 << 3) + value;", source)
        self.assertIn("times403 = (times25 << 4) + times3;", source)
        self.assertIn("hash = (value << 24) + times403;", source)
        self.assertEqual(source.count('__asm__ __volatile__("" : "+r"'), 4)
        self.assertNotIn("hash *= P2PSRAM_FNV_PRIME", source)
        self.assertNotIn("static uint8_t p2psram_pattern_byte", source)
        self.assertIn(
            "write_ticks += clock_systime_ticks() - start;", source
        )
        self.assertIn(
            "read_ticks += clock_systime_ticks() - start;", source
        )
        self.assertIn("base_actual != base_expected", source)
        self.assertIn("p2psram_buffer[index] !=", source)
        self.assertIn(
            'printf("P2PSRAM:PROGRESS:SEQUENCE=%08" PRIX32', source
        )
        self.assertIn(
            "geometry.max_request_bytes != P2PSRAM_MAX_REQUEST", source
        )
        self.assertIn("geometry.qpi_clock_hz != P2PSRAM_QPI_CLOCK_HZ", source)
        self.assertIn(
            "geometry.bulk_qpi_clock_hz != P2PSRAM_BULK_QPI_CLOCK_HZ",
            source,
        )
        self.assertIn("BULK_QPI_HZ=%d", source)

    def test_target_concurrency_and_timeout_evidence_are_bounded(self):
        source = (APPS / "testing/p2psram/p2psram_main.c").read_text()

        concurrent = source[
            source.index("static int p2psram_concurrent(") :
            source.index("static int p2psram_timeout_recovery(")
        ]
        self.assertIn("service_before = g_p2psram_workload_count;", concurrent)
        self.assertIn(
            "service_work = g_p2psram_workload_count - service_before;",
            concurrent,
        )
        self.assertIn("baseline_before = g_p2psram_workload_count;", concurrent)
        self.assertIn(
            "baseline_work = g_p2psram_workload_count - baseline_before;",
            concurrent,
        )
        self.assertIn(
            "for (request = 0; request < P2PSRAM_CONCURRENT_REQUESTS;",
            concurrent,
        )
        self.assertIn("service_cycles = p2psram_counter() - start_cycles;", concurrent)
        self.assertIn("baseline_cycles = p2psram_counter() - start_cycles;", concurrent)
        self.assertIn("P2PSRAM_CONCURRENT_MAX_CYCLES", concurrent)
        self.assertIn("service_rate =", concurrent)
        self.assertIn("baseline_rate =", concurrent)
        self.assertNotIn("clock_systime_ticks()", concurrent)
        self.assertIn("if (*available_permille == 0)", concurrent)
        self.assertIn("P2PSRAM_CONCURRENT_REQUESTS  64", source)
        self.assertIn('__asm__ __volatile__("getct %0"', source)
        self.assertIn(":ELAPSED_CYCLES=%", source)
        self.assertIn(":BASELINE_WORK=%", source)
        self.assertIn(":BASELINE_CYCLES=%", source)
        self.assertIn(":COUNTER_HZ=%d", source)

        timeout = source[
            source.index("static int p2psram_timeout_recovery(") :
            source.index("Public Functions")
        ]
        self.assertIn("P2PSRAM_TIMEOUT_DEADLINE", timeout)
        self.assertIn("sizeof(g_p2psram_buffer)", timeout)
        self.assertIn("p2_psram_arm_timeout_stall();", timeout)
        self.assertLess(
            timeout.index("p2_psram_arm_timeout_stall();"),
            timeout.index("p2_psram_transfer("),
        )
        self.assertIn(
            'P2PSRAM_TIMEOUT_FAULT        "COOPERATIVE_STALL"', source
        )
        self.assertNotIn("P2PSRAM_TIMEOUT_MIN_USEC", source)
        self.assertIn("P2PSRAM:TIMEOUT:PASS:RESULT=", source)
        self.assertIn(":FAULT=%s:TICK_USEC=%d", source)

    def test_service_cog_lifecycle_and_request_races_fail_closed(self):
        source = (BOARD / "src/p2_ec32mb_psram.c").read_text()
        assembly = (BOARD / "src/p2_ec32mb_psram_service.S").read_text()
        pins = (BOARD / "src/p2_ec32mb_pins.c").read_text()

        self.assertIn("coginit r0, r1                  wc", assembly)
        self.assertIn("mov     r31, r0", assembly)
        for augmented in (
            "augs    #0\n        mov     r1, ##p2_psram_cog_entry",
            "augs    #0\n        mov     ptra, ##g_p2_psram_service_stack",
            "augs    #0\n        mov     r15, ##g_p2_psram_wire",
        ):
            self.assertIn(augmented, assembly)
        cog_start = assembly[
            assembly.index("p2_psram_cog_start:") :
            assembly.index(".size p2_psram_cog_start")
        ]
        self.assertIn("setq    #1\n        wrlong  r0, ptra++", cog_start)
        self.assertIn("setq    #1\n        rdlong  r0, --ptra", cog_start)
        self.assertIn("ret >= P2_PIN_COG_COUNT", source)
        self.assertIn("p2_psram_park_failed_cog();", source)
        self.assertIn("p2_pin_transfer_claims(P2_PIN_OWNER_PSRAM", source)
        self.assertIn("p2_pin_stop_and_forget_cog(cog, P2_PIN_OWNER_PSRAM", source)
        self.assertIn("lockrel %0\\n\\tlockret %0", source)

        self.assertNotIn(
            "completion_sequence != request->sequence", source
        )
        self.assertIn("request->completion_sequence = 0;", source)
        self.assertIn("g_p2_psram.cancel_sequence = 0;", source)

        wait = source[
            source.index("static int p2_psram_wait(") :
            source.index("static ssize_t p2_psram_file_transfer(")
        ]
        self.assertLess(
            wait.index("locked = p2_psram_raw_trylock();"),
            wait.index("request->completion_sequence == sequence"),
        )
        self.assertLess(
            wait.index("request->completion_sequence == sequence"),
            wait.index("g_p2_psram.cancel_sequence = sequence;"),
        )
        self.assertLess(
            wait.index("g_p2_psram.cancel_sequence = sequence;"),
            wait.index("p2_psram_raw_unlock();"),
        )
        self.assertNotIn("p2_psram_raw_lock();", wait)
        self.assertNotIn("p2_psram_task_lock();", wait)
        self.assertIn("p2_psram_stop_failed_cog();", wait)
        self.assertIn("clock_compare(deadline, now)", wait)
        self.assertIn("clock_compare(grace_deadline, now)", wait)
        self.assertNotIn("(int32_t)(now -", wait)
        self.assertNotIn("inject_timeout_stall", wait)

        stop = source[
            source.index("static void p2_psram_stop_failed_cog(void)") :
            source.index("static int p2_psram_track_pin(")
        ]
        self.assertNotIn("p2_psram_raw_lock();", stop)
        self.assertNotIn("p2_psram_task_lock();", stop)
        self.assertLess(
            stop.index("p2_pin_stop_and_forget_cog("),
            stop.index("p2_psram_lockfree();"),
        )
        self.assertLess(
            stop.index("p2_psram_lockfree();"),
            stop.index("g_p2_psram.failed = true;"),
        )

        unified = source[
            source.index("int p2_psram_unified_transfer(") :
            source.index(
                "#endif", source.index("int p2_psram_unified_transfer(")
            )
        ]
        self.assertGreaterEqual(
            unified.count("p2_psram_raw_trylock()"), 2
        )
        self.assertEqual(unified.count("p2_psram_stop_failed_cog();"), 2)
        self.assertIn("p2_psram_counter() - deadline", unified)
        self.assertIn("now - grace_deadline", unified)
        self.assertNotIn("p2_psram_raw_lock();", unified)

        worker = source[
            source.index(
                "void __p2_xmem_psram_service_worker(void)\n{"
            ) :
            source.index("int p2_psram_initialize(")
        ]
        self.assertIn("g_p2_psram.start_allowed", worker)
        self.assertIn(
            "if (!p2_psram_take_request(&request))", worker
        )
        self.assertIn('__asm__ __volatile__("waitx #200");', worker)
        self.assertLess(
            worker.index('__asm__ __volatile__("waitx #200");'),
            worker.index("continue;"),
        )
        self.assertNotIn("p2_psram_claim_pins();", worker)
        self.assertNotIn("p2_pin_release(", worker)

        cancelled = source[
            source.index("static bool p2_psram_cancelled(") :
            source.index("static int p2_psram_execute(")
        ]
        self.assertIn("one aligned volatile Hub long", cancelled)
        self.assertEqual(cancelled.count("p2_psram_compiler_barrier();"), 2)
        self.assertNotIn("p2_psram_raw_lock();", cancelled)
        self.assertNotIn("p2_psram_raw_unlock();", cancelled)
        self.assertIn(
            "PSRAM cancellation word must remain long-aligned", source
        )

        self.assertIn(
            "CONFIG_P2_EC32MB_PSRAM_UNIFIED_FAULT_INJECT_RAW_LOCK",
            source,
        )
        self.assertIn("inject_raw_lock_stall", source)
        self.assertLess(
            source.index("inject_raw_lock_stall = 0;"),
            source.index("p2_psram_park_failed_cog();", source.index(
                "inject_raw_lock_stall = 0;"
            )),
        )

        self.assertIn(
            "CONFIG_P2_EC32MB_PSRAM_FAULT_INJECT_TIMEOUT", source
        )
        take = source[
            source.index("static bool p2_psram_take_request(") :
            source.index("static void p2_psram_complete(")
        ]
        self.assertLess(
            take.index("g_p2_psram.inject_timeout_stall = 0;"),
            take.index("request->completion = P2_PSRAM_COMPLETION_ACTIVE;"),
        )
        self.assertIn("worker->inject_timeout_stall", take)

        arm = source[
            source.index("int p2_psram_arm_timeout_stall(void)") :
            source.index(
                "#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED",
                source.index("int p2_psram_arm_timeout_stall(void)"),
            )
        ]
        self.assertIn("nxmutex_lock(&g_p2_psram.mutex)", arm)
        self.assertIn("flags = p2_psram_task_lock();", arm)
        self.assertIn("ret = -EBUSY;", arm)
        self.assertIn("g_p2_psram.inject_timeout_stall = 1;", arm)

        injected = worker[
            worker.index("if (request.inject_timeout_stall)") :
            worker.index("if (ret == -EIO)")
        ]
        self.assertIn(
            "while (!p2_psram_cancelled(request.sequence))", injected
        )
        self.assertIn('__asm__ __volatile__("waitx #200");', injected)
        self.assertIn("ret = -ECANCELED;", injected)
        self.assertIn("ret = p2_psram_execute(&request);", injected)

        startup_failure = worker[
            worker.index("if (ret < 0)") :
            worker.index("g_p2_psram.ready = 1;")
        ]
        self.assertNotIn("p2_psram_release_pins", startup_failure)

        stop_and_forget = pins[
            pins.index("int p2_pin_stop_and_forget_cog(") :
            pins.index("int p2_pin_get_state(")
        ]
        self.assertLess(
            stop_and_forget.index("flags = p2_pin_lock();"),
            stop_and_forget.index("cogstop %0"),
        )
        self.assertLess(
            stop_and_forget.index("cogstop %0"),
            stop_and_forget.index("make_safe();"),
        )
        self.assertLess(
            stop_and_forget.index("make_safe();"),
            stop_and_forget.index("p2_pin_clear_claim(state);"),
        )
        self.assertLess(
            stop_and_forget.index("p2_pin_clear_claim(state);"),
            stop_and_forget.index("p2_pin_unlock(flags);"),
        )

        initialize = source[
            source.index("int p2_psram_initialize(") :
            source.index("int p2_psram_get_geometry(")
        ]
        self.assertLess(
            initialize.index("nxmutex_lock(&g_p2_psram.mutex)"),
            initialize.index("if (g_p2_psram.failed)"),
        )
        self.assertLess(
            initialize.index("if (g_p2_psram.failed)"),
            initialize.index("if (g_p2_psram.registered)"),
        )


if __name__ == "__main__":
    unittest.main()
