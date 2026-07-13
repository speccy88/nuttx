#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
BOARD = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb"
APPS = ROOT.parent / "apps"


class PsramSourceTests(unittest.TestCase):
    def test_board_profile_builds_and_registers_the_explicit_service(self):
        kconfig = (BOARD / "Kconfig").read_text()
        makefile = (BOARD / "src/Makefile").read_text()
        boot = (BOARD / "src/p2_ec32mb_boot.c").read_text()
        profile = (BOARD / "configs/psram/defconfig").read_text()

        self.assertIn("config P2_EC32MB_PSRAM", kconfig)
        self.assertIn("depends on P2_SMARTPIN && BOARD_LATE_INITIALIZE", kconfig)
        self.assertIn("CSRCS += p2_ec32mb_psram.c", makefile)
        self.assertIn("ASRCS += p2_ec32mb_psram_service.S", makefile)
        self.assertIn("p2_psram_initialize();", boot)
        for setting in (
            "CONFIG_P2_SMARTPIN=y",
            "CONFIG_P2_EC32MB_PSRAM=y",
            "CONFIG_P2_EC32MB_PSRAM_COG_STACKSIZE=3072",
            "CONFIG_P2_EC32MB_PSRAM_MAX_REQUEST=65536",
            "CONFIG_P2_EC32MB_PSRAM_TIMEOUT_TICKS=500",
            "CONFIG_P2_EC32MB_PSRAM_CANCEL_GRACE_TICKS=100",
            "CONFIG_TESTING_P2PSRAM=y",
            "CONFIG_TESTING_P2PSRAM_RANDOM_COUNT=1024",
            "# CONFIG_SYSTEM_DD is not set",
        ):
            self.assertIn(setting, profile)

    def test_driver_exposes_explicit_service_not_native_memory(self):
        source = (BOARD / "src/p2_ec32mb_psram.c").read_text()
        header = (BOARD / "include/p2_ec32mb_psram.h").read_text()
        self.assertIn('P2_PSRAM_DEVICE_PATH          "/dev/psram0"', header)
        self.assertIn("register_driver(P2_PSRAM_DEVICE_PATH", source)
        self.assertIn("struct p2_psram_request_s", header)
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
        for command in ("0xf5000000", "#0x66", "#0x99", "#0x35", "0xeb000000"):
            self.assertIn(command, source)
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
        self.assertIn("verify_psram_service(sections, symbols)", verifier)
        self.assertIn("def verify_psram_test_hotpath(", verifier)
        self.assertIn(
            "verify_psram_test_hotpath(elf, sections, symbols)", verifier
        )
        self.assertIn(
            "calls slow __mulsi3 in the PSRAM full-pass path", verifier
        )

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
        self.assertIn("times3 = (value << 1) + value;", source)
        self.assertIn("times25 = (times3 << 3) + value;", source)
        self.assertIn("times403 = (times25 << 4) + times3;", source)
        self.assertIn("hash = (value << 24) + times403;", source)
        self.assertEqual(source.count('__asm__ __volatile__("" : "+r"'), 3)
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
        self.assertIn("if (*available_permille == 0)", concurrent)

        timeout = source[
            source.index("static int p2psram_timeout_recovery(") :
            source.index("Public Functions")
        ]
        self.assertIn("P2PSRAM_TIMEOUT_DEADLINE", timeout)
        self.assertIn("sizeof(g_p2psram_buffer)", timeout)
        self.assertIn("P2PSRAM_TIMEOUT_MIN_USEC     24576", source)
        self.assertIn("P2PSRAM:TIMEOUT:PASS:RESULT=", source)
        self.assertIn(":MIN_WIRE_USEC=%d:TICK_USEC=%d", source)

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
            wait.index("flags = p2_psram_task_lock();"),
            wait.index("request->completion_sequence == sequence"),
        )
        self.assertLess(
            wait.index("request->completion_sequence == sequence"),
            wait.index("g_p2_psram.cancel_sequence = sequence;"),
        )
        self.assertLess(
            wait.index("g_p2_psram.cancel_sequence = sequence;"),
            wait.index("p2_psram_task_unlock(flags);"),
        )
        self.assertLess(
            wait.index("p2_psram_stop_failed_cog_locked();"),
            wait.index("p2_psram_task_unlock(flags);"),
        )
        self.assertIn("clock_compare(deadline, now)", wait)
        self.assertIn("clock_compare(grace_deadline, now)", wait)
        self.assertNotIn("(int32_t)(now -", wait)

        worker = source[
            source.index("void p2_psram_service_worker(void)\n{") :
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
