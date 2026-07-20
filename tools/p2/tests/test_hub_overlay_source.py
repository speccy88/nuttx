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

from elftools.elf.elffile import ELFFile

ROOT = pathlib.Path(__file__).resolve().parents[3]
KCONFIG = ROOT / "arch/p2/Kconfig"
HEADER = ROOT / "arch/p2/include/overlay.h"
COMMON_DEFS = ROOT / "arch/p2/src/common/Make.defs"
RUNTIME = ROOT / "arch/p2/src/common/p2_overlay.c"
VENEER = ROOT / "arch/p2/src/common/p2_overlay_veneer.S"
LINKER = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb/scripts/ld.script"
BOARD_DEFS = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb/scripts/Make.defs"
ARCH_MAKE = ROOT / "arch/p2/src/Makefile"
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
        self.assertRegex(
            kconfig,
            r"config P2_HUB_OVERLAY_GROUP_COUNT[\s\S]*?range 1 1024",
        )
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

        python = (
            ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb/configs/python/defconfig"
        ).read_text()
        group_count = int(
            re.search(r"CONFIG_P2_HUB_OVERLAY_GROUP_COUNT=(\d+)", python).group(1)
        )
        slot_size = int(
            re.search(r"CONFIG_P2_HUB_OVERLAY_SLOT_SIZE=(\d+)", python).group(1)
        )
        self.assertGreaterEqual(group_count, 256)
        self.assertEqual(slot_size & 3, 0)
        self.assertGreaterEqual(slot_size, 4096)
        self.assertLessEqual(slot_size, 262144)

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
        self.assertIn(".p2.xdata.ro.overlay.entries", header)
        self.assertIn("struct p2_overlay_entry_s", header)
        self.assertIn("struct p2_overlay_group_s", header)
        self.assertIn("p2_overlay_install_groups", header)
        self.assertIn("p2_overlay_get_group", header)
        self.assertIn("p2_overlay_relocate_groups", header)
        self.assertIn("p2_overlay_register_loader", header)
        self.assertIn("descriptor->source += tagged_base", runtime)
        self.assertIn("g_p2_overlay_relocated = true", runtime)
        self.assertIn("p2_overlay_validate_tables()", runtime)
        self.assertIn("descriptor->image_crc32", runtime)
        self.assertIn("p2_hub_crc32_update", runtime)
        self.assertIn("resident[index] = groups[index]", runtime)
        self.assertIn("resident[index].source += tagged_base", runtime)
        self.assertIn("*descriptor = __p2_overlay_groups_start[group]", runtime)
        self.assertIn("P2_OVERLAY_ENTRY_CACHE_LINES  16u", runtime)
        self.assertIn("__p2_xmem_load64", runtime)
        self.assertIn("g_p2_overlay_entries_valid = true", runtime)
        self.assertIn("p2_overlay_read_entry(index, &entry, true)", runtime)

        dispatch = runtime[runtime.index("uintptr_t p2_overlay_dispatch_enter") :]
        self.assertIn("struct p2_overlay_entry_s entry;", dispatch)
        self.assertIn("p2_overlay_read_entry(stub_index, &entry, false)", dispatch)
        self.assertNotIn("__p2_overlay_entries_start[stub_index]", dispatch)
        self.assertNotRegex(dispatch, r"\bentry->(group|offset)\b")

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

    def test_group_crc_uses_resident_hub_acceleration(self) -> None:
        runtime = RUNTIME.read_text()
        self.assertIn("#include <arch/hub_crc32.h>", runtime)
        self.assertIn(
            "p2_hub_crc32_update(UINT32_C(0xffffffff), data, size)", runtime
        )
        self.assertNotIn("P2_OVERLAY_CRC_POLYNOMIAL", runtime)

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
        arch_make = ARCH_MAKE.read_text()

        self.assertIn("__p2_overlay_config_slot_size", board_defs)
        self.assertIn(
            "__p2_overlay_group_workspace_count=$(CONFIG_P2_HUB_OVERLAY_GROUP_COUNT)",
            board_defs,
        )
        self.assertIn("__p2_kernel_heap_configured=1", board_defs)
        self.assertIn("__p2_kernel_heap_config_size=$(CONFIG_MM_KERNEL_HEAPSIZE)", board_defs)
        self.assertIn(".p2.overlay.stubs", linker)
        self.assertIn("KEEP(*(.p2.xdata.ro.overlay.entries))", linker)
        self.assertNotIn("\n  .p2.overlay.entries :", linker)
        self.assertIn(".p2.overlay.groups", linker)
        self.assertIn("__p2_overlay_slot_start = P2_HUB_RUNTIME_END", linker)
        self.assertIn(".p2.xdata P2_XMEM_ORIGIN", linker)
        self.assertIn(".p2.xbss (NOLOAD)", linker)
        self.assertIn("p2_overlay_account (rx)", linker)
        self.assertLess(
            linker.index("p2_overlay_account (rx)"),
            linker.index("hub (rwx)"),
        )
        xdata = linker[
            linker.index(".p2.xdata P2_XMEM_ORIGIN") : linker.index(".p2.xbss (NOLOAD)")
        ]
        self.assertIn("__p2_overlay_entries_start", xdata)
        self.assertIn("__p2_overlay_entries_end", xdata)
        self.assertIn("_eheap == __p2_overlay_slot_start", linker)
        self.assertIn(
            "(__p2_overlay_group_workspace_count + 1) * 16",
            linker,
        )
        self.assertIn(
            "P2 overlay slot cannot stage configured Python group workspace",
            linker,
        )
        self.assertIn(
            "__p2_kernel_heap_config_size + 15 +",
            linker,
        )
        self.assertIn("__p2_initial_user_heap_min_size", linker)
        self.assertIn("less than 1024 bytes for the initial Hub user heap", linker)
        self.assertRegex(
            linker,
            re.escape("__p2_overlay_entries_end - __p2_overlay_entries_start")
            + r"\) ==\s*\n?\s*\(\(__p2_overlay_stubs_end",
        )
        fragment_flag = "-T $(call CONVERT_PATH,$(P2_OVERLAY_SCRIPT))"
        base_flag = "-T $(call CONVERT_PATH,$(ARCHSCRIPT))"
        self.assertLess(arch_make.index(base_flag), arch_make.index(fragment_flag))
        self.assertIn("--defsym=__p2_overlay_group_count=$$p2_overlay_count", arch_make)

    @unittest.skipUnless(tool("clang"), "set P2LLVM_ROOT for P2 link checks")
    def test_generated_count_precedes_base_table_reservation(self) -> None:
        clang = tool("clang")
        lld = tool("ld.lld")
        generator = tool("p2-overlay-link.py")
        self.assertIsNotNone(clang)
        self.assertIsNotNone(lld)
        self.assertIsNotNone(generator)

        source = """
          .section .text,"ax",@progbits
          .globl _start
        _start:
          nop
          .section .p2.overlay.body.00000001,"ax",@progbits
          nop
        """
        base_script = """
        PROVIDE(__p2_overlay_group_count = 0);
        SECTIONS
        {
          .text 0x1000 : { *(.text) }
          .p2.overlay.groups :
          {
            __p2_overlay_groups_start = .;
            . += __p2_overlay_group_count * 16;
            __p2_overlay_groups_end = .;
          }
          __p2_overlay_slot_start = 0x50000;
          __p2_overlay_slot_end = 0x51000;
        }
        ASSERT((__p2_overlay_groups_end - __p2_overlay_groups_start) ==
               __p2_overlay_group_count * 16, "group table count mismatch")
        """
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            assembly = root / "groups.S"
            obj = root / "groups.o"
            base = root / "base.ld"
            fragment = root / "fragment.ld"
            output = root / "linked.elf"
            assembly.write_text(source, encoding="utf-8")
            base.write_text(base_script, encoding="utf-8")
            subprocess.run(
                [str(clang), "--target=p2", "-c", str(assembly), "-o", str(obj)],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    str(generator),
                    "--fragment-only",
                    "--slot-address",
                    "0x50000",
                    "--slot-size",
                    "0x1000",
                    "--lma-address",
                    "0x3000000",
                    "-o",
                    str(fragment),
                    str(obj),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            ordered = subprocess.run(
                [
                    str(lld),
                    "--defsym=__p2_overlay_group_count=2",
                    "-T",
                    str(base),
                    "-T",
                    str(fragment),
                    str(obj),
                    "-o",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(ordered.returncode, 0, ordered.stderr)
            reverse = subprocess.run(
                [
                    str(lld),
                    "-T",
                    str(base),
                    "-T",
                    str(fragment),
                    str(obj),
                    "-o",
                    str(root / "reverse.elf"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(reverse.returncode, 0, reverse.stderr)

            def symbols(path):
                with path.open("rb") as stream:
                    table = ELFFile(stream).get_section_by_name(".symtab")
                    self.assertIsNotNone(table)
                    return {
                        name: table.get_symbol_by_name(name)[0]["st_value"]
                        for name in (
                            "__p2_overlay_group_count",
                            "__p2_overlay_groups_start",
                            "__p2_overlay_groups_end",
                        )
                    }

            good = symbols(output)
            self.assertEqual(good["__p2_overlay_group_count"], 2)
            self.assertEqual(
                good["__p2_overlay_groups_end"] - good["__p2_overlay_groups_start"],
                32,
            )
            late = symbols(root / "reverse.elf")
            self.assertEqual(late["__p2_overlay_group_count"], 2)
            self.assertEqual(
                late["__p2_overlay_groups_end"] - late["__p2_overlay_groups_start"],
                0,
            )

    @unittest.skipUnless(tool("clang"), "set P2LLVM_ROOT for P2 link checks")
    def test_synthetic_region_prevents_cumulative_overlay_hub_charge(self) -> None:
        clang = tool("clang")
        lld = tool("ld.lld")
        generator = tool("p2-overlay-link.py")
        self.assertIsNotNone(clang)
        self.assertIsNotNone(lld)
        self.assertIsNotNone(generator)

        source = """
          .section .text,"ax",@progbits
          .globl _start
        _start:
          nop
          .section .p2.overlay.body.00000001,"ax",@progbits
          .space 0x9000
          .section .p2.overlay.body.00000002,"ax",@progbits
          .space 0x9000
        """
        base_sections = """
        SECTIONS
        {
          .text 0x1000 : { *(.text) } > hub
          __p2_overlay_slot_start = 0x60000;
          __p2_overlay_slot_end = 0x70000;
        }
        """
        synthetic_memory = """
        MEMORY
        {
          p2_overlay_account (rx) : ORIGIN = 0x60000, LENGTH = 0x1000000
          hub (rwx) : ORIGIN = 0, LENGTH = 0x70000
        }
        """
        physical_memory = """
        MEMORY
        {
          hub (rwx) : ORIGIN = 0, LENGTH = 0x70000
        }
        """
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            assembly = root / "groups.S"
            obj = root / "groups.o"
            fragment = root / "fragment.ld"
            synthetic = root / "synthetic.ld"
            physical = root / "physical.ld"
            assembly.write_text(source, encoding="utf-8")
            synthetic.write_text(synthetic_memory + base_sections, encoding="utf-8")
            physical.write_text(physical_memory + base_sections, encoding="utf-8")
            subprocess.run(
                [str(clang), "--target=p2", "-c", str(assembly), "-o", str(obj)],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    str(generator),
                    "--fragment-only",
                    "--slot-address",
                    "0x60000",
                    "--slot-size",
                    "0x10000",
                    "--lma-address",
                    "0x3000000",
                    "-o",
                    str(fragment),
                    str(obj),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            protected = subprocess.run(
                [
                    str(lld),
                    "-T",
                    str(synthetic),
                    "-T",
                    str(fragment),
                    str(obj),
                    "-o",
                    str(root / "protected.elf"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            cumulative = subprocess.run(
                [
                    str(lld),
                    "-T",
                    str(physical),
                    "-T",
                    str(fragment),
                    str(obj),
                    "-o",
                    str(root / "cumulative.elf"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(protected.returncode, 0, protected.stderr)
        self.assertNotEqual(cumulative.returncode, 0)
        self.assertIn("will not fit in region 'hub'", cumulative.stderr)

    @unittest.skipUnless(tool("clang"), "set P2LLVM_ROOT for P2 codegen checks")
    def test_probe_stubs_compile_to_exactly_one_calla_each(self) -> None:
        clang = tool("clang")
        readelf = tool("llvm-readelf")
        self.assertIsNotNone(clang)
        self.assertIsNotNone(readelf)

        with tempfile.TemporaryDirectory() as directory:
            obj = pathlib.Path(directory) / "overlay-probe.o"
            probe_compile = subprocess.run(
                [str(clang), "--target=p2", "-c", str(PROBE), "-o", str(obj)],
                check=False,
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(probe_compile.returncode, 0, probe_compile.stderr)
            result = subprocess.run(
                [str(readelf), "-S", "-s", "-r", str(obj)],
                check=True,
                capture_output=True,
                text=True,
            )

        output = result.stdout
        self.assertRegex(output, r"\.p2\.overlay\.stubs\s+PROGBITS.*000008")
        self.assertRegex(
            output,
            r"\.p2\.xdata\.ro\.overlay\.entries\s+PROGBITS.*000010",
        )
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

        generated_config = ROOT / "include" / "nuttx" / "config.h"
        if not generated_config.is_file():
            self.skipTest("configure NuttX before the P2 runtime compile check")
        config_text = generated_config.read_text()
        for name in (
            "CONFIG_P2_HUB_OVERLAYS",
            "CONFIG_P2_HUB_OVERLAY_SLOT_SIZE",
            "CONFIG_P2_HUB_OVERLAY_SHADOW_DEPTH",
        ):
            if name not in config_text:
                self.skipTest("configure the P2 overlay profile first")

        defines = ("-D__NuttX__",)
        includes = (
            f"-I{ROOT / 'include'}",
            f"-I{ROOT / 'arch/p2/src/common'}",
            f"-I{ROOT / 'sched'}",
        )

        with tempfile.TemporaryDirectory() as directory:
            temporary = pathlib.Path(directory)
            runtime_obj = temporary / "p2_overlay.o"
            veneer_obj = temporary / "p2_overlay_veneer.o"
            runtime_compile = subprocess.run(
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
                check=False,
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(runtime_compile.returncode, 0, runtime_compile.stderr)
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
            symbols = subprocess.run(
                [str(nm), "-S", "--size-sort", str(runtime_obj)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout

        self.assertEqual(undefined.count("__p2_xmem_load64"), 1)
        self.assertNotIn("__p2_xmem_memcpy", undefined)
        self.assertRegex(symbols, r"\b000000c0\s+[bB]\s+g_p2_overlay_entry_cache\b")
        self.assertRegex(symbols, r"\b00000100\s+[bB]\s+g_p2_overlay_hot\b")


if __name__ == "__main__":
    unittest.main()
