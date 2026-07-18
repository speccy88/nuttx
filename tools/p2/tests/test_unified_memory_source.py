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

    def test_kernel_heap_and_every_created_task_stack_remain_in_hub(self):
        heap = (ROOT / "arch/p2/src/common/p2_allocateheap.c").read_text()
        stack = (ROOT / "arch/p2/src/common/p2_stack.c").read_text()
        for token in (
            "CONFIG_MM_KERNEL_HEAP",
            "p2_kernel_heap_end",
            "void up_allocate_kheap",
        ):
            self.assertIn(token, heap)
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

    def test_unified_lock_pins_every_llvm_tool_used_by_the_build(self):
        for script in (CLOUD_BOOTSTRAP, LOCAL_BOOTSTRAP, BUILD_SCRIPT):
            source = script.read_text()
            for tool in LOCKED_LLVM_TOOLS:
                self.assertIn(
                    f'"$P2LLVM_ROOT/bin/{tool}"',
                    source,
                    f"{script.name} does not pin {tool}",
                )


if __name__ == "__main__":
    unittest.main()
