#!/usr/bin/env python3
#
# SPDX-License-Identifier: Apache-2.0

"""Focused host/source and P2 checks for overlay shadow-stack bypass."""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
KCONFIG = ROOT / "arch/p2/Kconfig"
RUNTIME = ROOT / "arch/p2/src/common/p2_overlay.c"
VENEER = ROOT / "arch/p2/src/common/p2_overlay_veneer.S"
INTERNAL = ROOT / "arch/p2/src/common/p2_overlay_internal.h"


def tool(name: str) -> pathlib.Path | None:
    root = os.environ.get("P2LLVM_ROOT")
    if not root:
        return None

    path = pathlib.Path(root) / "bin" / name
    return path if path.is_file() else None


class OverlayShadowFastPathTest(unittest.TestCase):
    def test_same_group_path_precedes_shadow_overflow_and_push(self) -> None:
        runtime = RUNTIME.read_text(encoding="utf-8")
        enter_start = runtime.index("uintptr_t p2_overlay_dispatch_enter")
        exit_start = runtime.index("uint32_t p2_overlay_dispatch_exit", enter_start)
        enter = runtime[enter_start:exit_start]

        direct = enter.index("direct = g_p2_overlay_depth != 0")
        top_check = enter.index(
            "g_p2_overlay_shadow[g_p2_overlay_depth - 1].callee_group != group",
            direct,
        )
        marked_return = enter.index("P2_OVERLAY_DIRECT_TARGET_FLAG", top_check)
        overflow = enter.index(
            "g_p2_overlay_depth >= CONFIG_P2_HUB_OVERLAY_SHADOW_DEPTH",
            marked_return,
        )
        push = enter.index("g_p2_overlay_shadow[g_p2_overlay_depth++]", overflow)

        self.assertLess(direct, top_check)
        self.assertLess(top_check, marked_return)
        self.assertLess(marked_return, overflow)
        self.assertLess(overflow, push)
        self.assertIn("g_p2_overlay_owner != task", enter[:direct])
        self.assertIn("g_p2_overlay_transition", enter[:direct])
        self.assertIn("g_p2_overlay_loaded_group == group", enter[direct:top_check])

    def test_veneer_direct_path_keeps_original_resume(self) -> None:
        veneer = VENEER.read_text(encoding="utf-8")
        internal = INTERNAL.read_text(encoding="utf-8")

        self.assertIn("P2_OVERLAY_DIRECT_TARGET_BIT   31", internal)
        self.assertIn("P2_OVERLAY_DIRECT_TARGET_FLAG  0x80000000", internal)
        self.assertIn("testb   pb, #P2_OVERLAY_DIRECT_TARGET_BIT wc", veneer)
        self.assertIn(
            "if_c  andn    pb, ##P2_OVERLAY_DIRECT_TARGET_FLAG", veneer
        )
        self.assertRegex(veneer, r"sub\s+ptra, #4\n\s+if_c\s+jmp\s+pb")

        kconfig = KCONFIG.read_text(encoding="utf-8")
        self.assertIn("Each record holds the exact return resume", kconfig)
        self.assertIn("the default depth therefore costs 768 bytes", kconfig)
        self.assertIn("the maximum costs\n\t\t12288 bytes", kconfig)

    def test_resident_progress_snapshot_covers_every_dispatch_path(self) -> None:
        runtime = RUNTIME.read_text(encoding="utf-8")
        header = (ROOT / "arch/p2/include/overlay.h").read_text(
            encoding="utf-8"
        )

        self.assertIn("struct p2_overlay_stats_s", header)
        self.assertIn("int p2_overlay_get_stats", header)
        self.assertIn("stats->entry_count = g_p2_overlay_entry_count", runtime)
        self.assertIn(
            "stats->load_attempt_count = g_p2_overlay_load_attempt_count",
            runtime,
        )
        self.assertIn("stats->load_bytes = g_p2_overlay_load_bytes", runtime)
        self.assertIn("stats->loading_group = g_p2_overlay_loading_group", runtime)
        self.assertIn("stats->current_depth = g_p2_overlay_depth", runtime)
        self.assertIn("stats->last_error = g_p2_overlay_error", runtime)
        self.assertIn("g_p2_overlay_last_stub_index = UINT32_MAX", runtime)
        self.assertIn("g_p2_overlay_direct_count++", runtime)
        self.assertEqual(runtime.count("g_p2_overlay_load_count++;"), 2)
        self.assertEqual(
            runtime.count("g_p2_overlay_load_attempt_count++;"), 2
        )
        self.assertIn("g_p2_overlay_exit_count++;", runtime)

    @unittest.skipUnless(
        tool("clang") and tool("llvm-objdump"),
        "set P2LLVM_ROOT for P2 veneer checks",
    )
    def test_p2_veneer_encodes_conditional_direct_jump(self) -> None:
        clang = tool("clang")
        objdump = tool("llvm-objdump")
        assert clang is not None
        assert objdump is not None

        with tempfile.TemporaryDirectory() as directory:
            veneer_obj = pathlib.Path(directory) / "p2_overlay_veneer.o"
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
            disassembly = subprocess.run(
                [str(objdump), "-d", str(veneer_obj)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout

        self.assertRegex(disassembly, r"testb\s+pb, #31")
        self.assertRegex(disassembly, r"if_c\s+augs\s+#4194304")
        self.assertRegex(disassembly, r"if_c\s+andn\s+pb, #0")
        self.assertRegex(disassembly, r"if_c\s+jmp\s+pb")


if __name__ == "__main__":
    unittest.main()
