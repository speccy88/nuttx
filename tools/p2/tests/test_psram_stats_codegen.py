#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import os
import pathlib
import re
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
BOARD_SOURCE = (
    ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_psram.c"
)
TOOLCHAIN = pathlib.Path(
    os.environ.get("P2LLVM_ROOT", "/missing-p2llvm-toolchain")
)
CLANG = TOOLCHAIN / "bin/clang"
OBJDUMP = TOOLCHAIN / "bin/llvm-objdump"
CONFIG = ROOT / "include/nuttx/config.h"

FORBIDDEN = {
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
    "memcpy",
    "memmove",
    "memset",
}


def function_bodies(disassembly: str) -> dict[str, str]:
    matches = list(
        re.finditer(r"(?m)^0+[0-9a-f]* <(?P<name>[^>]+)>:\n", disassembly)
    )
    bodies = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else None
        bodies[match.group("name")] = disassembly[match.end() : end]
    return bodies


def call_targets(body: str) -> set[str]:
    targets = set()
    for target in re.findall(r"R_P2_20\s+(\S+)", body):
        target = target.split("+", 1)[0]
        if target.startswith(".text."):
            target = target[len(".text.") :]
        targets.add(target)
    return targets


@unittest.skipUnless(
    CLANG.is_file() and OBJDUMP.is_file() and CONFIG.is_file(),
    "set P2LLVM_ROOT and configure NuttX for P2 object-code checks",
)
class PsramStatsCodegenTests(unittest.TestCase):
    def test_public_stats_path_has_no_canonical_xmem_or_libc_copy(self):
        config = CONFIG.read_text()
        if "#define CONFIG_P2_EC32MB_PSRAM_UNIFIED 1" not in config:
            self.skipTest("NuttX is not configured for unified P2 PSRAM")

        with tempfile.TemporaryDirectory() as directory:
            obj = pathlib.Path(directory) / "p2_ec32mb_psram.o"
            command = [
                str(CLANG),
                "-c",
                "--target=p2",
                "-fno-jump-tables",
                "-fno-builtin",
                "-fno-common",
                "-ffunction-sections",
                "-fdata-sections",
                "-Wall",
                "-Wextra",
                "-Wshadow",
                "-Wundef",
                "-Wstrict-prototypes",
                "-Os",
                "-isystem",
                str(ROOT / "include"),
                "-isystem",
                str(ROOT / "include/newlib"),
                "-I",
                str(ROOT / "include"),
                "-D__NuttX__",
                "-DNDEBUG",
                "-mllvm",
                "-p2-unified-memory",
                "-D__KERNEL__",
                "-I",
                str(ROOT / "sched"),
                "-I",
                str(ROOT / "arch/p2/src/chip"),
                "-I",
                str(ROOT / "arch/p2/src/common"),
                str(BOARD_SOURCE),
                "-o",
                str(obj),
            ]
            subprocess.run(command, check=True, capture_output=True, text=True)
            disassembly = subprocess.run(
                [str(OBJDUMP), "-dr", str(obj)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout

        bodies = function_bodies(disassembly)
        self.assertIn("p2_psram_get_cache_stats", bodies)
        self.assertIn("__p2_xmem_psram_cache_snapshot", bodies)

        public_calls = call_targets(bodies["p2_psram_get_cache_stats"])
        self.assertIn("__p2_xmem_psram_cache_snapshot", public_calls)
        self.assertIn("p2_psram_unified_transfer", public_calls)

        pending = ["p2_psram_get_cache_stats"]
        audited = set()
        while pending:
            function = pending.pop()
            if function in audited:
                continue
            audited.add(function)
            calls = call_targets(bodies[function])
            self.assertFalse(
                calls & FORBIDDEN,
                f"{function} has forbidden calls: {sorted(calls & FORBIDDEN)}",
            )
            for called in calls:
                if (
                    called in bodies
                    and (
                        called.startswith("p2_psram_")
                        or called.startswith("__p2_xmem_psram_")
                    )
                ):
                    pending.append(called)


if __name__ == "__main__":
    unittest.main()
