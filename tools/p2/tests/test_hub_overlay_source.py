#!/usr/bin/env python3
#
# SPDX-License-Identifier: Apache-2.0

"""Static and optional P2 code-generation checks for Hub overlays."""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
KCONFIG = ROOT / "arch/p2/Kconfig"
HEADER = ROOT / "arch/p2/include/overlay.h"
COMMON_DEFS = ROOT / "arch/p2/src/common/Make.defs"
RUNTIME = ROOT / "arch/p2/src/common/p2_overlay.c"
VENEER = ROOT / "arch/p2/src/common/p2_overlay_veneer.S"
LINKER = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb/scripts/ld.script"
BOARD_DEFS = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb/scripts/Make.defs"
PROBE = ROOT / "tools/p2/probes/hub-overlay-stubs.S"


def tool(name: str) -> pathlib.Path | None:
    root = os.environ.get("P2LLVM_ROOT")
    if not root:
        return None

    path = pathlib.Path(root) / "bin" / name
    return path if path.is_file() else None


class HubOverlaySourceTest(unittest.TestCase):
    def test_feature_is_gated_and_disabled_in_existing_profiles(self) -> None:
        kconfig = KCONFIG.read_text()
        defs = COMMON_DEFS.read_text()

        self.assertRegex(
            kconfig,
            r"config P2_HUB_OVERLAYS\n"
            r"\s+bool .*\n\s+default n\n"
            r"\s+depends on P2_EC32MB_PSRAM_UNIFIED\n"
            r"\s+depends on !SMP",
        )
        self.assertIn("ifeq ($(CONFIG_P2_HUB_OVERLAYS),y)", defs)
        self.assertIn("p2_overlay_veneer.S", defs)
        self.assertIn("p2_overlay.c", defs)
        self.assertIn("p2_overlay$(OBJEXT): P2_UNIFIED_MEMORY_FLAGS =", defs)

        for defconfig in ROOT.glob("boards/p2/**/configs/*/defconfig"):
            if defconfig.parent.name == "python":
                continue

            self.assertNotIn(
                "CONFIG_P2_HUB_OVERLAYS=y",
                defconfig.read_text(),
                str(defconfig),
            )

    def test_metadata_abi_and_relocation_publish_are_explicit(self) -> None:
        header = HEADER.read_text()
        runtime = RUNTIME.read_text()

        self.assertIn("P2_OVERLAY_ABI_VERSION", header)
        self.assertIn("P2_OVERLAY_RESIDENT_GROUP", header)
        self.assertIn("P2_OVERLAY_GROUP_FLAGS_PACKED_V1", header)
        self.assertIn("P2_OVERLAY_GROUP_FLAG_REQUIRED", header)
        self.assertIn("P2_OVERLAY_GROUP_FLAG_READ_ONLY", header)
        self.assertIn("P2_OVERLAY_GROUP_FLAG_EXECUTABLE", header)
        self.assertIn("P2_OVERLAY_GROUP_FLAG_FIXED_ADDRESS", header)
        self.assertIn("struct p2_overlay_entry_s", header)
        self.assertIn("struct p2_overlay_group_s", header)
        self.assertIn("p2_overlay_relocate_groups", header)
        self.assertIn("p2_overlay_register_loader", header)
        self.assertIn("descriptor->source += tagged_base", runtime)
        self.assertIn("g_p2_overlay_relocated = true", runtime)
        self.assertIn("p2_overlay_validate_tables()", runtime)
        self.assertIn("descriptor->image_crc32", runtime)
        self.assertIn("P2_OVERLAY_CRC_POLYNOMIAL", runtime)

    def test_dispatcher_is_fail_closed_and_has_no_storage_io(self) -> None:
        runtime = RUNTIME.read_text()

        self.assertIn(
            "static void p2_overlay_fail(int error) noreturn_function", runtime
        )
        self.assertIn("g_p2_overlay_ready = false", runtime)
        self.assertIn("PANIC();", runtime)
        self.assertIn("g_p2_overlay_owner != task", runtime)
        self.assertIn("g_p2_overlay_transition", runtime)
        self.assertIn("P2_OVERLAY_RESIDENT_GROUP", runtime)
        self.assertIn("owning task may be\n * preempted", runtime)
        self.assertIn(
            "PSRAM copy deliberately occurs with interrupts\n * and scheduling enabled",
            runtime,
        )
        self.assertNotRegex(runtime, r"\b(open|mount|read|lseek|ioctl)\s*\(")

    def test_copy_and_crc_failures_cannot_publish_a_group(self) -> None:
        runtime = RUNTIME.read_text()
        load_start = runtime.index("static int p2_overlay_load_group")
        load_end = runtime.index("static void p2_overlay_fail", load_start)
        load = runtime[load_start:load_end]

        self.assertRegex(load, r"ret = loader\([\s\S]*?if \(ret != 0\)")
        self.assertIn("return ret < 0 ? ret : -EIO;", load)
        self.assertRegex(
            load,
            r"p2_overlay_crc32\([\s\S]*?!=\s*"
            r"descriptor->image_crc32\)[\s\S]*?return -EILSEQ;",
        )

        enter_start = runtime.index("uintptr_t p2_overlay_dispatch_enter")
        exit_start = runtime.index("uint32_t p2_overlay_dispatch_exit", enter_start)
        enter = runtime[enter_start:exit_start]
        load_call = enter.index("ret = p2_overlay_load_group(group);")
        fatal = enter.index("p2_overlay_fail(ret);", load_call)
        publish = enter.index("g_p2_overlay_loaded_group = group;", fatal)
        self.assertLess(load_call, fatal)
        self.assertLess(fatal, publish)

    def test_veneer_preserves_return_pair_and_uses_shadow_resume(self) -> None:
        veneer = VENEER.read_text()

        self.assertIn("setq    #29", veneer)
        self.assertIn("calla   #\\p2_overlay_dispatch_enter", veneer)
        self.assertIn("mov     r31, ##__p2_overlay_exit", veneer)
        self.assertIn("jmp     pb", veneer)
        self.assertIn("wrlong  r30, ptra++", veneer)
        self.assertIn("wrlong  r31, ptra++", veneer)
        self.assertIn("calla   #\\p2_overlay_dispatch_exit", veneer)
        self.assertIn("rdlong  r31, --ptra", veneer)
        self.assertIn("rdlong  r30, --ptra", veneer)
        self.assertRegex(veneer, r"wrlong\s+pb, ptra\+\+\n\s+reta")

    def test_linker_reserves_fixed_slot_and_checks_table_cardinality(self) -> None:
        linker = LINKER.read_text()
        board_defs = BOARD_DEFS.read_text()

        self.assertIn("__p2_overlay_config_slot_size", board_defs)
        self.assertIn(".p2.overlay.stubs", linker)
        self.assertIn(".p2.overlay.entries", linker)
        self.assertIn(".p2.overlay.groups", linker)
        self.assertIn(".p2.overlay.slot P2_HUB_RUNTIME_END (NOLOAD)", linker)
        self.assertIn("_eheap == __p2_overlay_slot_start", linker)
        self.assertRegex(
            linker,
            re.escape("__p2_overlay_entries_end - __p2_overlay_entries_start")
            + r"\) ==\s*\n?\s*\(\(__p2_overlay_stubs_end",
        )

    @unittest.skipUnless(tool("clang"), "set P2LLVM_ROOT for P2 codegen checks")
    def test_probe_stubs_compile_to_exactly_one_calla_each(self) -> None:
        clang = tool("clang")
        readelf = tool("llvm-readelf")
        self.assertIsNotNone(clang)
        self.assertIsNotNone(readelf)

        with tempfile.TemporaryDirectory() as directory:
            obj = pathlib.Path(directory) / "overlay-probe.o"
            subprocess.run(
                [str(clang), "--target=p2", "-c", str(PROBE), "-o", str(obj)],
                check=True,
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            result = subprocess.run(
                [str(readelf), "-S", "-s", "-r", str(obj)],
                check=True,
                capture_output=True,
                text=True,
            )

        output = result.stdout
        self.assertRegex(output, r"\.p2\.overlay\.stubs\s+PROGBITS.*000008")
        self.assertRegex(output, r"\b4 FUNC\s+GLOBAL.*p2_overlay_probe_first")
        self.assertRegex(output, r"\b4 FUNC\s+GLOBAL.*p2_overlay_probe_second")
        self.assertEqual(output.count("R_P2_20"), 2)
        self.assertEqual(output.count("__p2_overlay_enter + 0"), 2)

    @unittest.skipUnless(tool("clang"), "set P2LLVM_ROOT for P2 codegen checks")
    def test_resident_runtime_and_veneer_compile_without_xmem_recursion(self) -> None:
        clang = tool("clang")
        nm = tool("llvm-nm")
        self.assertIsNotNone(clang)
        self.assertIsNotNone(nm)

        defines = (
            "-D__NuttX__",
            "-DCONFIG_P2_HUB_OVERLAYS=1",
            "-DCONFIG_P2_HUB_OVERLAY_SLOT_SIZE=131072",
            "-DCONFIG_P2_HUB_OVERLAY_SHADOW_DEPTH=64",
        )
        includes = (
            f"-I{ROOT / 'include'}",
            f"-I{ROOT / 'arch/p2/src/common'}",
            f"-I{ROOT / 'sched'}",
        )

        with tempfile.TemporaryDirectory() as directory:
            runtime_obj = pathlib.Path(directory) / "p2_overlay.o"
            veneer_obj = pathlib.Path(directory) / "p2_overlay_veneer.o"
            subprocess.run(
                [
                    str(clang),
                    "--target=p2",
                    "-fno-builtin",
                    "-Os",
                    "-Wall",
                    "-Wextra",
                    "-Wshadow",
                    "-Wundef",
                    "-Wstrict-prototypes",
                    "-Werror",
                    *defines,
                    *includes,
                    "-c",
                    str(RUNTIME),
                    "-o",
                    str(runtime_obj),
                ],
                check=True,
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    str(clang),
                    "--target=p2",
                    "-D__ASSEMBLY__",
                    f"-I{ROOT / 'include'}",
                    "-c",
                    str(VENEER),
                    "-o",
                    str(veneer_obj),
                ],
                check=True,
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            undefined = subprocess.run(
                [str(nm), "-u", str(runtime_obj)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout

        self.assertNotIn("__p2_xmem_", undefined)


if __name__ == "__main__":
    unittest.main()
