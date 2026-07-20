#!/usr/bin/env python3
#
# SPDX-License-Identifier: Apache-2.0

"""Focused host and source checks for bounded P2 overlay hot profiling."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
HEADER = ROOT / "arch/p2/include/overlay.h"
RUNTIME = ROOT / "arch/p2/src/common/p2_overlay.c"
LOGIC = ROOT / "arch/p2/src/common/p2_overlay_hot_logic.h"
PROBE = ROOT / "tools/p2/tests/p2_overlay_hot_logic_test.c"
RESIDENCY = ROOT / "tools/p2/verify-python-residency.py"


class OverlayHotProfileTest(unittest.TestCase):
    def test_space_saving_logic_has_exact_host_coverage(self) -> None:
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("host C compiler is unavailable")

        with tempfile.TemporaryDirectory() as directory:
            temp = pathlib.Path(directory)
            include = temp / "include"
            (include / "nuttx").mkdir(parents=True)
            (include / "arch").mkdir()
            (include / "nuttx/config.h").write_text("", encoding="utf-8")
            (include / "nuttx/compiler.h").write_text(
                "#define FAR\n", encoding="utf-8"
            )
            (include / "arch/overlay.h").symlink_to(HEADER)
            executable = temp / "p2-overlay-hot-test"
            subprocess.run(
                [
                    compiler,
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    f"-I{include}",
                    f"-I{LOGIC.parent}",
                    str(PROBE),
                    "-o",
                    str(executable),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run([str(executable)], check=True)

    def test_resident_table_is_bounded_and_snapshotted_coherently(self) -> None:
        header = HEADER.read_text(encoding="utf-8")
        runtime = RUNTIME.read_text(encoding="utf-8")

        self.assertIn("#define P2_OVERLAY_HOT_CAPACITY                8u", header)
        self.assertIn("struct p2_overlay_hot_entry_s", header)
        self.assertIn("struct p2_overlay_hot_snapshot_s", header)
        self.assertIn("int p2_overlay_get_hot_snapshot", header)
        self.assertIn('section(".bss.p2_overlay_hot")', runtime)
        self.assertIn("sizeof(g_p2_overlay_hot) == 256", runtime)
        self.assertIn("sizeof(g_p2_overlay_hot) <= 512", runtime)

        residency = RESIDENCY.read_text(encoding="utf-8")
        self.assertIn('"python_overlay_report_hot",', residency)
        self.assertIn('"p2_overlay_get_hot_snapshot",', residency)

        snapshot = runtime[
            runtime.index("int p2_overlay_get_hot_snapshot") :
            runtime.index("uintptr_t p2_overlay_dispatch_enter")
        ]
        self.assertIn("irqstate = enter_critical_section();", snapshot)
        self.assertIn("snapshot->total_count = g_p2_overlay_hot_total_count", snapshot)
        self.assertIn("snapshot->entries[index] = g_p2_overlay_hot[index]", snapshot)
        self.assertIn("leave_critical_section(irqstate);", snapshot)
        self.assertNotIn("__p2_xmem", snapshot)

    def test_each_valid_non_direct_entry_updates_once(self) -> None:
        runtime = RUNTIME.read_text(encoding="utf-8")
        enter = runtime[
            runtime.index("uintptr_t p2_overlay_dispatch_enter") :
            runtime.index("uint32_t p2_overlay_dispatch_exit")
        ]

        direct_return = enter.index("P2_OVERLAY_DIRECT_TARGET_FLAG")
        overflow = enter.index(
            "g_p2_overlay_depth >= CONFIG_P2_HUB_OVERLAY_SHADOW_DEPTH",
            direct_return,
        )
        update = enter.index("p2_overlay_hot_update", overflow)
        push = enter.index("g_p2_overlay_shadow[g_p2_overlay_depth++]", update)
        self.assertLess(direct_return, overflow)
        self.assertLess(overflow, update)
        self.assertLess(update, push)
        self.assertEqual(enter.count("p2_overlay_hot_update"), 1)
        self.assertIn("hot_key.caller_group = hot_caller_group", enter)
        self.assertIn("hot_key.caller_offset = hot_caller_offset", enter)
        self.assertIn("hot_key.target_group = group", enter)
        self.assertIn("hot_key.target_stub = (uint32_t)stub_index", enter)

        registration = runtime[
            runtime.index("int p2_overlay_register_loader") :
            runtime.index("int p2_overlay_last_error")
        ]
        self.assertEqual(registration.count("p2_overlay_hot_reset"), 1)

    def test_nested_resident_direct_call_bypasses_profile_work(self) -> None:
        runtime = RUNTIME.read_text(encoding="utf-8")
        enter = runtime[
            runtime.index("uintptr_t p2_overlay_dispatch_enter") :
            runtime.index("uint32_t p2_overlay_dispatch_exit")
        ]

        direct = enter.index(
            "direct = g_p2_overlay_depth != 0 &&\n"
            "           g_p2_overlay_loaded_group == group"
        )
        direct_return = enter.index("P2_OVERLAY_DIRECT_TARGET_FLAG", direct)
        callsite = enter.index("p2_overlay_hot_callsite", direct_return)
        update = enter.index("p2_overlay_hot_update", callsite)
        self.assertLess(direct, direct_return)
        self.assertLess(direct_return, callsite)
        self.assertLess(callsite, update)

    def test_nested_resident_transition_separates_hot_and_reload_groups(
        self,
    ) -> None:
        runtime = RUNTIME.read_text(encoding="utf-8")
        logic = LOGIC.read_text(encoding="utf-8")
        helper = runtime[
            runtime.index("static int p2_overlay_hot_callsite") :
            runtime.index(
                "/****", runtime.index("static int p2_overlay_hot_callsite")
            )
        ]

        self.assertIn("p2_overlay_validate_resume(caller_resume) < 0", helper)
        self.assertIn("if (pc > slot_start)", helper)
        self.assertIn("reload_group >= p2_overlay_group_count()", helper)
        self.assertIn("p2_overlay_validate_group(reload_group, true) < 0", helper)
        self.assertIn("p2_overlay_hot_decode_callsite(", helper)
        self.assertIn("if (pc <= slot_start)", logic)
        self.assertIn(
            "*hot_caller_group = P2_OVERLAY_RESIDENT_GROUP", logic
        )
        self.assertIn("*hot_caller_group = reload_group", logic)

        enter = runtime[
            runtime.index("uintptr_t p2_overlay_dispatch_enter") :
            runtime.index("uint32_t p2_overlay_dispatch_exit")
        ]
        reload_group = enter.index("reload_group = g_p2_overlay_loaded_group")
        validation = enter.index("p2_overlay_hot_callsite", reload_group)
        failure = enter.index("p2_overlay_fail(ret);", validation)
        update = enter.index("p2_overlay_hot_update", failure)
        shadow = enter.index("shadow->caller_group = reload_group", update)
        self.assertIn(
            "p2_overlay_hot_callsite(reload_group, caller_resume,\n"
            "                                &hot_caller_group, "
            "&hot_caller_offset)",
            enter,
        )
        self.assertLess(reload_group, validation)
        self.assertLess(validation, failure)
        self.assertLess(failure, update)
        self.assertLess(update, shadow)


if __name__ == "__main__":
    unittest.main()
