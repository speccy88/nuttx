#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
BOARD = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb"
PROFILE = BOARD / "configs/unified/defconfig"
HIL_PROFILE = BOARD / "configs/unified-hil/defconfig"
BOARD_HEADER = BOARD / "include/board.h"
HEADER = BOARD / "include/p2_ec32mb_psram.h"
LINKER_SCRIPT = BOARD / "scripts/ld.script"
PSRAM_SOURCE = BOARD / "src/p2_ec32mb_psram.c"
XMEM_SOURCE = BOARD / "src/p2_ec32mb_xmem.c"
XMEM_SELFTEST = BOARD / "src/p2_ec32mb_xmem_selftest.c"
COMPILER_PATCH = ROOT / "tools/p2/patches/p2llvm-unified-memory.patch"
CLOUD_BOOTSTRAP = ROOT / "tools/p2/bootstrap-cloud.sh"
LOCAL_BOOTSTRAP = ROOT / "tools/p2/bootstrap-local.sh"
BUILD_SCRIPT = ROOT / "tools/p2/build.sh"
ABI_PROBES = ROOT / "tools/p2/run-abi-probes.sh"
COMPILER_BUILTINS = '"$P2LLVM_ROOT/libp2/lib/libcompiler_builtins.a"'

LOCKED_LLVM_TOOLS = (
    "clang",
    "clang++",
    "ld.lld",
    "llc",
    "llvm-ar",
    "llvm-nm",
    "llvm-objcopy",
    "llvm-objdump",
    "llvm-readelf",
    "llvm-readobj",
    "llvm-size",
    "llvm-strip",
    "p2-overlay-link.py",
)

HELPERS = (
    "__p2_xmem_load8",
    "__p2_xmem_load16",
    "__p2_xmem_load32",
    "__p2_xmem_load64",
    "__p2_xmem_store8",
    "__p2_xmem_store16",
    "__p2_xmem_store32",
    "__p2_xmem_store64",
    "__p2_xmem_memcpy",
    "__p2_xmem_memmove",
    "__p2_xmem_memset",
)


def config_block(kconfig: str, symbol: str) -> str:
    match = re.search(
        rf"^config {re.escape(symbol)}\s*$"
        rf"(?P<body>.*?)"
        rf"(?=^config |^choice\s*$|\Z)",
        kconfig,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing Kconfig symbol: {symbol}")
    return match.group("body")


def preprocessor_guards_at(source: str, needle: str) -> tuple[str, ...]:
    """Return the active textual preprocessor conditions at one source line."""

    guards = []
    for line in source.splitlines():
        if needle in line:
            return tuple(guards)

        directive = re.match(
            r"\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)", line
        )
        if directive is None:
            continue

        kind = directive.group(1)
        condition = directive.group(2).strip()
        if kind in ("if", "ifdef", "ifndef"):
            guards.append(f"{kind} {condition}".strip())
        elif kind == "elif":
            if not guards:
                raise AssertionError("unbalanced #elif before registration")
            guards[-1] = f"elif {condition}"
        elif kind == "else":
            if not guards:
                raise AssertionError("unbalanced #else before registration")
            guards[-1] = f"else ({guards[-1]})"
        else:
            if not guards:
                raise AssertionError("unbalanced #endif before registration")
            guards.pop()

    raise AssertionError(f"missing source text: {needle}")


class UnifiedMemorySourceTests(unittest.TestCase):
    def test_profile_selects_two_region_user_heap_without_legacy_device(self):
        profile = PROFILE.read_text()
        for setting in (
            "CONFIG_BUILD_FLAT=y",
            "CONFIG_P2_SMARTPIN=y",
            "CONFIG_P2_EC32MB_PSRAM_UNIFIED=y",
            "CONFIG_MM_KERNEL_HEAP=y",
            "CONFIG_MM_REGIONS=2",
        ):
            self.assertIn(setting, profile)

        self.assertNotIn("CONFIG_P2_EC32MB_PSRAM=y", profile)
        self.assertNotIn("CONFIG_TESTING_P2PSRAM=y", profile)
        self.assertNotIn("/dev/psram0", profile)
        for checked_profile in (profile, HIL_PROFILE.read_text()):
            self.assertNotIn(
                "CONFIG_P2_EC32MB_PSRAM_UNIFIED_FAULT_INJECT_RAW_LOCK=y",
                checked_profile,
            )

    def test_kconfig_separates_legacy_device_from_unified_service(self):
        kconfig = (BOARD / "Kconfig").read_text()
        legacy = config_block(kconfig, "P2_EC32MB_PSRAM")
        unified = config_block(kconfig, "P2_EC32MB_PSRAM_UNIFIED")

        self.assertIn("select P2_EC32MB_PSRAM_SERVICE", legacy)
        self.assertIn("/dev/psram0", legacy)
        for token in (
            "depends on BUILD_FLAT",
            "depends on !SMP",
            "depends on !P2_EC32MB_PSRAM",
            "select ARCH_HAVE_EXTRA_HEAPS",
            "select MM_KERNEL_HEAP",
            "select P2_EC32MB_PSRAM_SERVICE",
            "does not register",
            "/dev/psram0",
        ):
            self.assertIn(token, unified)

    def test_tag_window_and_complete_helper_abi_are_public(self):
        header = HEADER.read_text()
        self.assertRegex(
            header,
            r"#define\s+P2_PSRAM_UNIFIED_BASE\s+UINT32_C\(0x10000000\)",
        )
        self.assertRegex(
            header,
            r"#define\s+P2_PSRAM_UNIFIED_SIZE\s+P2_PSRAM_SIZE_BYTES",
        )
        self.assertIn("P2_PSRAM_UNIFIED_END", header)
        for helper in HELPERS:
            self.assertRegex(header, rf"\b{re.escape(helper)}\s*\(")

    def test_board_build_enables_runtime_and_compiler_pass_only_for_profile(self):
        makefile = (BOARD / "src/Makefile").read_text()
        definitions = (BOARD / "scripts/Make.defs").read_text()
        self.assertIn("ifeq ($(CONFIG_P2_EC32MB_PSRAM_UNIFIED),y)", makefile)
        self.assertIn("CSRCS += p2_ec32mb_xmem.c", makefile)
        self.assertIn("ifeq ($(CONFIG_P2_EC32MB_PSRAM_UNIFIED),y)", definitions)
        self.assertIn("-mllvm -p2-unified-memory", definitions)
        self.assertIn("$(P2_UNIFIED_MEMORY_FLAGS)", definitions)

    def test_unified_selftest_links_the_pinned_compiler_runtime(self):
        definitions = (BOARD / "scripts/Make.defs").read_text()

        self.assertIn("$(CONFIG_P2_EC32MB_PSRAM_UNIFIED_SELFTEST)", definitions)
        self.assertIn("P2_COMPILER_BUILTINS =", definitions)
        self.assertIn("libcompiler_builtins.a", definitions)
        self.assertIn("EXTRA_LIBS += $(P2_COMPILER_BUILTINS)", definitions)

    def test_runtime_adds_only_the_tagged_user_region(self):
        source = XMEM_SOURCE.read_text()
        self.assertIn("up_extraheaps_init", source)
        self.assertIn("p2_psram_initialize", source)
        self.assertIn("kumm_addregion", source)
        self.assertIn("P2_PSRAM_UNIFIED_BASE", source)
        self.assertIn("P2_PSRAM_UNIFIED_SIZE", source)
        self.assertNotIn("kmm_addregion", source)
        self.assertLess(
            source.index("p2_psram_initialize"), source.index("kumm_addregion")
        )
        for helper in HELPERS:
            self.assertRegex(source, rf"\b{re.escape(helper)}\s*\(")

    def test_runtime_bounds_bulk_overlap_and_failure_paths_are_explicit(self):
        source = XMEM_SOURCE.read_text()
        for token in (
            "start < hub_end && length <= hub_end - start",
            "start >= P2_PSRAM_UNIFIED_BASE",
            "start < P2_PSRAM_UNIFIED_END",
            "length <= P2_PSRAM_UNIFIED_END - start",
            "There are exactly two legal data spaces",
            "P2_XMEM_BOUNCE_SIZE",
            "__p2_xmem_copy_forward",
            "__p2_xmem_copy_backward",
            "backward = dest > src && dest < src + length",
            "PANIC()",
        ):
            self.assertIn(token, source)

        bulk_functions = (
            "__p2_xmem_memcpy",
            "__p2_xmem_memmove",
            "__p2_xmem_memset",
        )
        for index, function in enumerate(bulk_functions):
            start = source.index(f"void {function}(")
            if index + 1 < len(bulk_functions):
                end = source.index(f"void {bulk_functions[index + 1]}(")
            else:
                end = source.index("void up_extraheaps_init(")
            body = source[start:end]
            self.assertLess(
                body.index("if (length == 0)"),
                body.index("__p2_xmem_classify("),
            )

        transfer = PSRAM_SOURCE.read_text()
        self.assertIn(
            "(uintptr_t)hub_buffer >= BOARD_P2_HUB_USABLE_END", transfer
        )
        self.assertIn(
            "length > BOARD_P2_HUB_USABLE_END - (uintptr_t)hub_buffer",
            transfer,
        )

        unified_start = transfer.index("int p2_psram_unified_transfer(")
        unified_end = transfer.index("#endif", unified_start)
        unified = transfer[unified_start:unified_end]
        self.assertLess(
            unified.index(
                "length > BOARD_P2_HUB_USABLE_END - (uintptr_t)hub_buffer"
            ),
            unified.index("request->hub_buffer = (uintptr_t)hub_buffer;"),
        )

        worker_copy = transfer[
            transfer.index("static int p2_psram_execute(") :
            transfer.index("static bool p2_psram_take_request(")
        ]
        self.assertIn("FAR uint8_t *buffer = req->buffer;", worker_copy)
        self.assertIn("uint8_t word[4];", worker_copy)

        hub_fast_path = source[
            source.index("void __p2_xmem_memcpy(") :
            source.index("void __p2_xmem_memmove(")
        ]
        fast_start = hub_fast_path.index(
            "if (dest_region == P2_XMEM_REGION_HUB"
        )
        fast_end = hub_fast_path.index("else", fast_start)
        self.assertIn("__p2_xmem_hub_copy_forward", hub_fast_path[
            fast_start:fast_end
        ])
        self.assertNotIn("__p2_xmem_transfer", hub_fast_path[
            fast_start:fast_end
        ])

    def test_runtime_fault_emits_raw_uart_marker_before_panic(self):
        source = XMEM_SOURCE.read_text()
        start = source.index("static void __p2_xmem_fault(void)\n{")
        end = source.index("\n}\n", start)
        fault = source[start:end]

        self.assertIn('#define P2_XMEM_FAULT_MARKER "P2XMEM:FAULT"', source)
        self.assertIn(
            '#define P2_XMEM_TIMEOUT_MARKER "P2XMEM:TIMEOUT"', source
        )
        self.assertIn(
            'void p2_boot_trace(FAR const char *message)\n'
            '  __asm__("__p2_xmem_boot_trace");',
            source,
        )
        self.assertIn("p2_boot_trace(P2_XMEM_FAULT_MARKER);", fault)
        self.assertIn("FAR const char *marker = P2_XMEM_FAULT_MARKER;", fault)
        self.assertIn("up_putc(*marker++);", fault)
        self.assertLess(
            fault.index("p2_boot_trace(P2_XMEM_FAULT_MARKER);"),
            fault.index("PANIC();"),
        )

    def test_unified_timeout_is_reported_before_cog_recovery(self):
        xmem = XMEM_SOURCE.read_text()
        transfer = PSRAM_SOURCE.read_text()
        start = transfer.index("int p2_psram_unified_transfer(")
        end = transfer.index("int p2_psram_get_cache_stats(", start)
        unified = transfer[start:end]

        self.assertIn("void __p2_xmem_timeout_trace(void)", xmem)
        self.assertEqual(unified.count("__p2_xmem_timeout_trace();"), 2)
        for occurrence in re.finditer(
            r"__p2_xmem_timeout_trace\(\);", unified
        ):
            recovery = unified.index(
                "p2_psram_stop_failed_cog();", occurrence.end()
            )
            self.assertLess(occurrence.start(), recovery)

    def test_runtime_hub_limit_matches_linker_loader_contract(self):
        board_header = BOARD_HEADER.read_text()
        linker_script = LINKER_SCRIPT.read_text()
        stack = (ROOT / "arch/p2/src/common/p2_stack.c").read_text()
        xmem = XMEM_SOURCE.read_text()
        transfer = PSRAM_SOURCE.read_text()

        self.assertRegex(
            board_header,
            r"#define\s+BOARD_P2_HUB_USABLE_END\s+0x0007c000\b",
        )
        self.assertRegex(
            linker_script, r"P2_HUB_SIZE\s*=\s*0x0007c000\s*;"
        )
        self.assertRegex(
            linker_script,
            r"P2_HUB_END\s*=\s*P2_HUB_ORIGIN\s*\+\s*P2_HUB_SIZE\s*;",
        )
        for source in (stack, xmem, transfer):
            self.assertIn("BOARD_P2_HUB_USABLE_END", source)

    def test_full_hil_covers_page_and_exact_device_end_boundaries(self):
        source = XMEM_SELFTEST.read_text()
        self.assertIn("P2XMEM:BOUNDARY:PASS", source)
        self.assertIn("P2_XMEM_PAGE_BOUNDARY - 1u", source)
        self.assertIn("P2_XMEM_PAGE_BOUNDARY - 2u", source)
        self.assertIn("P2_XMEM_PAGE_BOUNDARY - 3u", source)
        for width, final_offset in ((8, 1), (16, 2), (32, 4), (64, 8)):
            self.assertIn(
                f"P2_PSRAM_UNIFIED_END - {final_offset}u", source
            )
            self.assertIn(f"__p2_xmem_store{width}(address", source)
            self.assertIn(f"__p2_xmem_load{width}(address", source)

        self.assertLess(
            source.index("ret = p2_xmem_boundary_test();"),
            source.index("for (address = 0; address < P2_PSRAM_SIZE_BYTES;"),
        )
        xmem = XMEM_SOURCE.read_text()
        self.assertLess(
            xmem.index("ret = p2_psram_unified_fulltest();"),
            xmem.index("kumm_addregion"),
        )

    def test_fragmentation_hil_verifies_live_head_and_tail_content(self):
        source = XMEM_SELFTEST.read_text()
        self.assertIn("p2_xmem_fragment_fill", source)
        self.assertIn("p2_xmem_fragment_verify", source)
        self.assertIn("size - P2_XMEM_FRAGMENT_EDGE", source)
        self.assertIn("patterns[index] = (uint8_t)(0x30u + index)", source)
        self.assertIn("patterns[index] = (uint8_t)(0x70u + index)", source)
        self.assertGreaterEqual(source.count("p2_xmem_fragment_verify("), 4)

    def test_concurrency_hil_rechecks_both_completed_worker_ranges(self):
        source = XMEM_SELFTEST.read_text()
        self.assertIn("Immediate worker readback catches transaction errors", source)
        self.assertIn(
            "for (worker_index = 0; worker_index < 2; worker_index++)", source
        )
        self.assertIn(
            "workers[worker_index].words[index] != expected", source
        )

    def test_geometry_copy_releases_service_locks_before_tagged_result(self):
        source = PSRAM_SOURCE.read_text()
        start = source.index("int p2_psram_get_geometry(")
        end = source.index("ssize_t p2_psram_transfer(", start)
        geometry = source[start:end]

        self.assertIn("struct p2_psram_geometry_s snapshot;", geometry)
        self.assertLess(
            geometry.index("p2_psram_task_unlock(flags);"),
            geometry.index("nxmutex_unlock(&g_p2_psram.mutex);"),
        )
        self.assertLess(
            geometry.index("nxmutex_unlock(&g_p2_psram.mutex);"),
            geometry.index("*geometry = snapshot;"),
        )

        selftest = XMEM_SELFTEST.read_text()
        self.assertIn("p2_psram_get_geometry(geometry)", selftest)
        self.assertIn("P2XMEM:GEOMETRY:PASS", selftest)

    def test_raw_lock_fault_injection_proves_bounded_terminal_failure(self):
        kconfig = (BOARD / "Kconfig").read_text()
        transfer = PSRAM_SOURCE.read_text()
        selftest = XMEM_SELFTEST.read_text()

        self.assertIn(
            "config P2_EC32MB_PSRAM_UNIFIED_FAULT_INJECT_RAW_LOCK",
            kconfig,
        )
        self.assertIn("\tselect BOARDCTL\n", kconfig)
        self.assertIn("select BOARDCTL_RESET", kconfig)
        self.assertIn("inject_raw_lock_stall", transfer)
        self.assertIn("while (!p2_psram_raw_trylock())", transfer)
        self.assertIn("p2_psram_stop_failed_cog();", transfer)
        self.assertIn("P2XMEM:FAULT_RAW_LOCK:ARMED", selftest)
        self.assertIn(
            "P2XMEM:FAULT_RAW_LOCK:PASS:TERMINAL", selftest
        )
        self.assertIn("ret != -ETIMEDOUT", selftest)
        self.assertIn("ret != -ENODEV", selftest)
        self.assertIn("board_reset(0);", selftest)

    def test_scalar_cache_is_clean_write_through_and_fail_closed(self):
        source = PSRAM_SOURCE.read_text()
        logic = (BOARD / "src/p2_ec32mb_psram_logic.h").read_text()
        assembly = (BOARD / "src/p2_ec32mb_psram_service.S").read_text()
        header = HEADER.read_text()
        verifier = (ROOT / "tools/p2/verify-elf.py").read_text()

        for token in (
            "P2_PSRAM_CACHE_LINE_SIZE",
            "P2_PSRAM_CACHE_SET_COUNT",
            "P2_PSRAM_CACHE_WAY_COUNT",
            "P2_PSRAM_CACHE_LINE_COUNT",
            "P2_PSRAM_CACHE_SCALAR_MAX",
            "return p2_psram_cache_line_number(address) + 1u;",
            "__p2_xmem_psram_cache_find",
            "__p2_xmem_psram_cache_select",
            "__p2_xmem_psram_cache_touch",
        ):
            self.assertIn(token, logic)

        for field in ("hits", "misses", "fills", "writes", "bypasses"):
            self.assertRegex(header, rf"uint64_t\s+{field};")
            self.assertIn(f"stats.{field} = 0;", source)
        self.assertIn("p2_psram_get_cache_stats(", header)

        start = source.index("int p2_psram_unified_transfer(")
        end = source.index("int p2_psram_get_cache_stats(", start)
        transfer = source[start:end]
        self.assertLess(
            transfer.index("g_p2_psram.ready != 1"),
            transfer.index("p2_psram_cacheable_read("),
        )
        self.assertLess(
            transfer.index("__p2_xmem_psram_cache_prepare_fill("),
            transfer.index("request->completion_sequence = 0;"),
        )
        self.assertNotIn("g_p2_psram_cache.", transfer)

        prepare = source[
            source.index(
                "static noinline_function unsigned int "
                "__p2_xmem_psram_cache_prepare_fill("
            ) :
            source.index(
                "static noinline_function FAR void "
                "*__p2_xmem_psram_cache_fill_buffer("
            )
        ]
        self.assertLess(
            prepare.index("__p2_xmem_psram_cache_select("),
            prepare.index("g_p2_psram_cache.tags[index] = 0;"),
        )

        fill_guard = transfer.index(
            "else if (cache_fill && status >= 0 && !timed_out)"
        )
        publish_call = transfer.index(
            "__p2_xmem_psram_cache_publish_fill(", fill_guard
        )
        self.assertLess(fill_guard, publish_call)

        publish = source[
            source.index(
                "static noinline_function void "
                "__p2_xmem_psram_cache_publish_fill("
            ) :
            source.index(
                "static noinline_function void "
                "__p2_xmem_psram_cache_count_bypass("
            )
        ]
        self.assertLess(
            publish.index("g_p2_psram_cache.tags[index] ="),
            publish.index("g_p2_psram_cache.stats.fills++;"),
        )

        complete = transfer[transfer.index("if (complete)") :]
        success = complete.index("if (status >= 0)")
        record = complete.index("__p2_xmem_psram_cache_record_write(")
        self.assertLess(success, record)
        self.assertGreaterEqual(
            complete.count("__p2_xmem_psram_cache_invalidate_write("), 2
        )

        record_write = source[
            source.index(
                "static noinline_function void "
                "__p2_xmem_psram_cache_record_write("
            ) :
            source.index(
                "static noinline_function void "
                "__p2_xmem_psram_cache_invalidate_write("
            )
        ]
        self.assertLess(
            record_write.index("__p2_xmem_psram_cache_update_write("),
            record_write.index("g_p2_psram_cache.stats.writes++;"),
        )

        initialize = source[
            source.index("int p2_psram_initialize(") :
            source.index("int p2_psram_unified_arm_raw_lock_stall(")
        ]
        self.assertLess(
            initialize.index("__p2_xmem_psram_cache_reset();"),
            initialize.index("p2_psram_cog_start();"),
        )
        self.assertNotIn("memset(&g_p2_psram_cache", source)

        fault = source[
            source.index("int p2_psram_unified_arm_raw_lock_stall(") : start
        ]
        self.assertLess(
            fault.index("__p2_xmem_psram_cache_invalidate_all();"),
            fault.index("g_p2_psram.inject_raw_lock_stall = 1;"),
        )

        stats = source[end : source.index("#endif", end)]
        self.assertIn("struct p2_psram_cache_stats_s snapshot;", stats)
        self.assertIn("uint32_t external_address;", stats)
        self.assertIn("bool tagged;", stats)
        self.assertIn(
            "external_address = (uint32_t)(uintptr_t)stats - "
            "P2_PSRAM_UNIFIED_BASE;",
            stats,
        )
        self.assertIn(
            "tagged = external_address < P2_PSRAM_UNIFIED_SIZE;", stats
        )
        self.assertIn(
            "__p2_xmem_psram_cache_snapshot(tagged ? &snapshot : stats);",
            stats,
        )
        self.assertIn("return p2_psram_unified_transfer(", stats)
        self.assertIn("P2_PSRAM_OPERATION_WRITE", stats)
        self.assertNotIn("*stats = snapshot;", stats)
        self.assertNotIn("__p2_xmem_memcpy", stats)
        self.assertLess(
            stats.index("up_irq_restore(flags);"),
            stats.index("return p2_psram_unified_transfer("),
        )

        self.assertIn(
            "calla   #\\__p2_xmem_psram_service_worker", assembly
        )
        for runtime in (
            "__p2_xmem_psram_service_worker",
            "__p2_xmem_psram_record_ce_cycles",
            "__p2_xmem_psram_wire_execute",
        ):
            self.assertIn(runtime, source)
            self.assertIn(runtime, verifier)
        for runtime in (
            "p2_psram_stream_install",
            "p2_psram_stream_transfer",
        ):
            self.assertIn(runtime, assembly)
            self.assertIn(runtime, verifier)
        self.assertIn("recursively calls unified-memory helpers", verifier)

        required_cache = verifier[
            verifier.index("cache_runtime = (") :
            verifier.index(")\n    if \"p2_psram_unified_transfer\"", verifier.index(
                "cache_runtime = ("
            ))
        ]
        self.assertNotIn("__p2_xmem_psram_cache_snapshot", required_cache)
        self.assertIn(
            'if "p2_psram_get_cache_stats" in symbols:', verifier
        )
        self.assertIn(
            '"__p2_xmem_psram_cache_snapshot" not in symbols', verifier
        )
        stats_root = verifier.index(
            'roots.append("p2_psram_get_cache_stats")'
        )
        unified_root = verifier.index(
            'if "p2_psram_unified_transfer" in symbols:'
        )
        self.assertGreater(stats_root, unified_root)
        self.assertNotIn(
            'roots.append("__p2_xmem_psram_cache_snapshot")', verifier
        )

    def test_unified_hil_selftest_proves_exact_scalar_cache_behavior(self):
        source = XMEM_SELFTEST.read_text()
        kconfig = (BOARD / "Kconfig").read_text()
        cache = source[
            source.index("static int p2_xmem_cache_test(") :
            source.index("static int p2_xmem_bulk_test(")
        ]
        main = source[source.index("int p2_psram_unified_selftest(") :]

        self.assertIn("arena + 0x10000", cache)
        self.assertIn("P2_XMEM_CACHE_ALIGNMENT", cache)
        for offset in (128, 256, 160, 288):
            self.assertIn(f"target + {offset}u", cache)
        self.assertLess(
            cache.rindex("target + 288u"),
            cache.index("p2_psram_get_cache_stats(&before)"),
        )

        self.assertEqual(cache.count("p2_psram_get_cache_stats("), 2)
        self.assertEqual(cache.count("P2_PSRAM_OPERATION_WRITE"), 2)
        self.assertEqual(cache.count("P2_PSRAM_OPERATION_READ"), 1)
        self.assertIn("external_address + 28u", cache)
        self.assertIn("patch, sizeof(patch)", cache)
        self.assertIn("p2_xmem_buffers_equal(readback, expected", cache)
        self.assertIn("p2_xmem_hub_le32(expected + 28u)", cache)
        self.assertIn("p2_xmem_hub_le32(expected + 32u)", cache)
        self.assertIn("p2_xmem_hub_le32(expected + 40u)", cache)
        self.assertIn("return -EIO;", cache)
        self.assertIn("return -ERANGE;", cache)

        for name, value in (
            ("HITS", 5),
            ("MISSES", 2),
            ("FILLS", 2),
            ("WRITES", 2),
            ("BYPASSES", 1),
        ):
            self.assertRegex(
                source,
                rf"#define P2_XMEM_CACHE_{name}\s+UINT64_C\({value}\)",
            )
            self.assertIn(
                f"after.{name.lower()} - before.{name.lower()} != "
                f"P2_XMEM_CACHE_{name}",
                cache,
            )

        scalar = main.index('p2_xmem_marker("P2XMEM:SCALAR:PASS")')
        cache_call = main.index("ret = p2_xmem_cache_test(arena);")
        cache_marker = main.index('p2_xmem_marker("P2XMEM:CACHE:PASS:')
        bulk_call = main.index("ret = p2_xmem_bulk_test(arena);")
        self.assertLess(scalar, cache_call)
        self.assertLess(cache_call, cache_marker)
        self.assertLess(cache_marker, bulk_call)
        self.assertIn('p2_xmem_fail("CACHE", "COHERENCE", ret)', main)
        self.assertIn(
            'P2XMEM:CACHE:PASS:HITS=5:MISSES=2:FILLS=2:', main
        )
        self.assertIn('WRITES=2:BYPASSES=1', main)

        selftest_help = config_block(
            kconfig, "P2_EC32MB_PSRAM_UNIFIED_SELFTEST"
        )
        self.assertIn("scalar-cache coherence/counter", selftest_help)
        self.assertIn("write which crosses two resident lines", selftest_help)

    def test_unified_hil_selftest_covers_every_pinned_softfloat_helper(self):
        source = XMEM_SELFTEST.read_text()
        softfloat_start = source.index(
            "static noinline_function int p2_xmem_softfloat_zero_test("
        )
        softfloat = source[
            softfloat_start : source.index(
                "static void p2_xmem_boundary_diag", softfloat_start
            )
        ]

        for declaration in (
            "extern double __floatdidf(int64_t value);",
            "extern double __floatunsidf(unsigned int value);",
            "extern double __muldf3(double lhs, double rhs);",
            "extern double __adddf3(double lhs, double rhs);",
            "extern float __truncdfsf2(double value);",
            "extern int64_t __fixdfdi(double value);",
        ):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, source)

        for marker in (
            "P2XMEM:FLOATUNSIDF:PASS:ZERO",
            "P2XMEM:FLOATUNSIDF:PASS:ONE",
            "P2XMEM:MULDF3:PASS:ZERO",
            "P2XMEM:ADDDF3:PASS:ZERO",
            "P2XMEM:MULDF3:PASS:NONZERO",
            "P2XMEM:ADDDF3:PASS:NONZERO",
            "P2XMEM:TRUNCDFSF2:PASS",
            "P2XMEM:FIXDFDI:PASS",
            "P2XMEM:SOFTFLOAT:PASS:ALL",
            "P2XMEM:FLOATDIDF:ALL:PASS",
        ):
            with self.subTest(marker=marker):
                self.assertEqual(source.count(marker), 1)

        for exact_bits in (
            "UINT32_C(0x3ff80000)",  # 1.5
            "UINT32_C(0x40000000)",  # 2.0
            "UINT32_C(0x40080000)",  # 3.0
            "UINT32_C(0x40020000)",  # 2.25
            "UINT32_C(0x400e0000)",  # 3.75
            "UINT32_C(0x3fc00000)",  # 1.5f
        ):
            with self.subTest(exact_bits=exact_bits):
                self.assertIn(exact_bits, softfloat)

        self.assertLess(
            softfloat.index("P2XMEM:SOFTFLOAT:PASS:ZERO"),
            softfloat.index("P2XMEM:SOFTFLOAT:PASS:NONZERO"),
        )
        self.assertLess(
            softfloat.index("P2XMEM:SOFTFLOAT:PASS:NONZERO"),
            softfloat.index("P2XMEM:TRUNCDFSF2:BEGIN"),
        )
        self.assertLess(
            softfloat.index("P2XMEM:TRUNCDFSF2:PASS"),
            softfloat.index("P2XMEM:FIXDFDI:BEGIN"),
        )

    def test_kernel_heap_and_every_created_task_stack_remain_in_hub(self):
        heap = (ROOT / "arch/p2/src/common/p2_allocateheap.c").read_text()
        stack = (ROOT / "arch/p2/src/common/p2_stack.c").read_text()
        for token in (
            "CONFIG_MM_KERNEL_HEAP",
            "p2_kernel_heap_end",
            "void up_allocate_kheap",
            "__p2_initial_user_heap_min_size",
            "heap_end - end < user_min",
        ):
            self.assertIn(token, heap)
        linker = LINKER_SCRIPT.read_text()
        self.assertIn("__p2_initial_user_heap_min_size = 0x00000400", linker)
        self.assertIn(
            "less than 1024 bytes for the initial Hub user heap", linker
        )
        self.assertIn("CONFIG_P2_EC32MB_PSRAM_UNIFIED", stack)
        self.assertIn("stack = kmm_malloc(stack_size);", stack)
        self.assertIn("stack = kmm_memalign(TLS_STACK_ALIGN, stack_size);", stack)
        self.assertIn("BOARD_P2_HUB_USABLE_END", stack)
        self.assertIn("stack_size > hub_end - base", stack)
        self.assertIn("return -ENOTSUP;", stack)

    def test_unified_build_compiles_out_character_device_registration(self):
        source = PSRAM_SOURCE.read_text()
        guards = "\n".join(
            preprocessor_guards_at(
                source, "register_driver(P2_PSRAM_DEVICE_PATH"
            )
        )
        negative_unified = (
            "ifndef CONFIG_P2_EC32MB_PSRAM_UNIFIED" in guards
            or "!defined(CONFIG_P2_EC32MB_PSRAM_UNIFIED)" in guards
        )
        positive_legacy = (
            "ifdef CONFIG_P2_EC32MB_PSRAM" in guards
            or "defined(CONFIG_P2_EC32MB_PSRAM)" in guards
        )
        self.assertTrue(
            negative_unified or positive_legacy,
            f"device registration has no legacy-only guard:\n{guards}",
        )

        selftest = XMEM_SELFTEST.read_text()
        self.assertIn("errno = 0;", selftest)
        self.assertIn("if (errno != ENOENT)", selftest)

    @unittest.skipUnless(
        COMPILER_PATCH.is_file(), "tracked compiler patch not imported yet"
    )
    def test_tracked_compiler_patch_matches_runtime_abi_and_opt_in_flag(self):
        patch = COMPILER_PATCH.read_text()
        for token in (
            "p2-unified-memory",
            "AtomicRMWInst",
            "AtomicCmpXchgInst",
            "InlineAsm",
            "__atomic_",
            "__sync_",
        ):
            self.assertIn(token, patch)
        for helper in HELPERS:
            self.assertIn(helper, patch)

    def test_cloud_bootstrap_pins_compiler_source_and_writes_exact_lock(self):
        source = CLOUD_BOOTSTRAP.read_text()

        for token in (
            "P2LLVM_REF=${P2LLVM_REF:-bdcefcce7860b2232c06f35726fea679a3a7309c}",
            "LLVM_PROJECT_REF=${LLVM_PROJECT_REF:-72a9bb1ef2656d9953d1f41a8196d425ff2ab0b1}",
            "ensure_p2llvm_checkout()",
            '[[ "$outer_head" == "$P2LLVM_REF" ]]',
            '[[ "$gitlink_head" == "$LLVM_PROJECT_REF" ]]',
            '[[ "$llvm_head" == "$LLVM_PROJECT_REF" ]]',
            "p2llvm_patch_state_valid ||",
            "parse_p2llvm_compiler_version_commit",
            "p2llvm_compiler_version_commit=$compiler_version_commit",
            '"$P2LLVM_ROOT/bin/clang"',
            '"$P2LLVM_ROOT/bin/ld.lld"',
            '"$P2LLVM_ROOT/bin/llc"',
            'printf \'sha256=%s  %s\\n\' "$digest" "$file"',
            "export P2_TOOLCHAIN_LOCK=%q",
        ):
            self.assertIn(token, source)

        self.assertLess(
            source.index("ensure_p2llvm_checkout\n"),
            source.index("apply_p2llvm_patches\n"),
        )
        self.assertNotIn("git -C \"$P2LLVM_SRC/llvm-project\" reset", source)

    def test_lock_separates_source_head_from_compiler_version_revision(self):
        revision_re = (
            "(^|[^[:xdigit:]])([[:xdigit:]]{40})([^[:xdigit:]]|$)"
        )
        for bootstrap in (CLOUD_BOOTSTRAP, LOCAL_BOOTSTRAP):
            source = bootstrap.read_text()
            self.assertIn("parse_p2llvm_compiler_version_commit()", source)
            self.assertIn(revision_re, source)
            self.assertIn(
                'echo "p2llvm_llvm_project_commit=', source, bootstrap.name
            )
            self.assertIn(
                'echo "p2llvm_compiler_version_commit=$compiler_version_commit"',
                source,
                bootstrap.name,
            )
            self.assertIn('echo "compiler=$compiler_version"', source)

        probes = ABI_PROBES.read_text()
        new_field = probes.index("s/^p2llvm_compiler_version_commit=//p")
        fallback = probes.index('if [[ -z "$expected_compiler_commit" ]]')
        old_field = probes.index("s/^p2llvm_llvm_project_commit=//p", fallback)
        version_check = probes.index(
            '"$compiler_version" != *"$expected_compiler_commit"*'
        )
        self.assertLess(new_field, fallback)
        self.assertLess(fallback, old_field)
        self.assertLess(old_field, version_check)
        self.assertIn(
            '"$expected_compiler_commit" =~ ^[[:xdigit:]]{40}$', probes
        )

    def test_unified_lock_pins_every_llvm_tool_used_by_the_build(self):
        for script in (CLOUD_BOOTSTRAP, LOCAL_BOOTSTRAP, BUILD_SCRIPT):
            source = script.read_text()
            for tool in LOCKED_LLVM_TOOLS:
                self.assertIn(
                    f'"$P2LLVM_ROOT/bin/{tool}"',
                    source,
                    f"{script.name} does not pin {tool}",
                )

    def test_unified_lock_pins_the_compiler_runtime_archive(self):
        for script in (CLOUD_BOOTSTRAP, LOCAL_BOOTSTRAP, BUILD_SCRIPT):
            self.assertIn(COMPILER_BUILTINS, script.read_text())


if __name__ == "__main__":
    unittest.main()
