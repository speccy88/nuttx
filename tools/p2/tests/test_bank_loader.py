#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import os
import pathlib
import re
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
BOARD = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb"
APP = ROOT.parent / "apps/system/p2bank"


class P2BankLogicTests(unittest.TestCase):
    def test_handoff_crc_and_path_rules(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = pathlib.Path(temporary) / "p2-bank-logic-test"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-D_POSIX_C_SOURCE=200809L",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(ROOT / "include"),
                    "-I",
                    str(APP),
                    str(ROOT / "tools/p2/tests/p2_bank_logic_test.c"),
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run([str(executable)], cwd=ROOT, check=True)

    def test_destructive_loader_is_bounded_and_hub_independent(self):
        assembly = (BOARD / "src/p2_ec32mb_psram_service.S").read_text()
        source = (BOARD / "src/p2_ec32mb_psram.c").read_text()
        loader = assembly[
            assembly.index("p2_psram_bank_loader_image:") :
            assembly.index("p2_psram_bank_loader_image_end:")
        ]

        self.assertIn("P2_BANK_HUB_IMAGE_LIMIT", source)
        self.assertIn("p2_psram_range_valid(external_address, transfer_size)", source)
        self.assertIn("g_p2_psram_bank_boot.external_address", source)
        self.assertIn("g_p2_psram_bank_boot.image_size", source)
        self.assertIn("cogstop P2_BANK_TARGET", loader)
        self.assertIn("wrlong  P2_BANK_DATA, P2_BANK_HUB", loader)
        self.assertIn("coginit #0, #0", loader)
        self.assertLess(loader.index("cogstop P2_BANK_TARGET"),
                        loader.index("wrlong  P2_BANK_DATA, P2_BANK_HUB"))
        self.assertNotIn("calla", loader)
        self.assertNotIn("g_p2_", loader)
        self.assertIn(
            ".error \"P2 PSRAM bank loader overlaps cog data registers\"",
            assembly,
        )

    def test_manager_requires_regular_flash_file_and_crc_readback(self):
        source = (APP / "p2bank.c").read_text()
        makefile = (APP / "Makefile").read_text()
        profile = (BOARD / "configs/berrymgr/defconfig").read_text()

        self.assertIn("PROGNAME  = p2bank berry", makefile)
        self.assertIn("CONFIG_SYSTEM_P2BANK=y", profile)
        self.assertIn(
            'CONFIG_SYSTEM_P2BANK_DEFAULT_BANK="/mnt/flash/banks/berry.bin"',
            profile,
        )
        self.assertIn("S_ISREG(status.st_mode)", source)
        self.assertIn('p2bank_path_safe(bank_path, "/mnt/flash/"', source)
        self.assertIn('p2bank_path_safe(script_path, "/mnt/sd/"', source)
        self.assertIn("p2bank_verify_psram(image_size, image_crc, buffer)", source)
        self.assertIn("p2bank_write_handoff(image_size, image_crc, script_path)", source)
        self.assertIn("P2BANK:VERIFIED:", source)
        self.assertIn("P2BANK:SWITCHING:", source)


class P2BankTargetBuildTests(unittest.TestCase):
    def setUp(self):
        default = ROOT.parent / ".p2-nuttx-cache/p2llvm/install"
        self.toolchain = pathlib.Path(os.environ.get("P2LLVM_ROOT", str(default)))
        self.clang = self.toolchain / "bin/clang"
        self.nm = self.toolchain / "bin/llvm-nm"
        self.readelf = self.toolchain / "bin/llvm-readelf"
        missing = [path for path in (self.clang, self.nm, self.readelf)
                   if not path.is_file()]
        self.assertEqual(missing, [], "P2 toolchain is required: " +
                         ", ".join(str(path) for path in missing))

    def test_cogexec_image_compiles_aligned_small_and_relocation_free(self):
        with tempfile.TemporaryDirectory() as temporary:
            objfile = pathlib.Path(temporary) / "bank-service.o"
            subprocess.run(
                [
                    str(self.clang),
                    "--target=p2",
                    "-fno-jump-tables",
                    "-fno-builtin",
                    "-fno-common",
                    "-ffunction-sections",
                    "-fdata-sections",
                    "-D__ASSEMBLY__",
                    "-I",
                    str(ROOT / "include"),
                    "-I",
                    str(BOARD / "src"),
                    "-x",
                    "assembler-with-cpp",
                    "-c",
                    str(BOARD / "src/p2_ec32mb_psram_service.S"),
                    "-o",
                    str(objfile),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            symbols = subprocess.run(
                [str(self.nm), "--print-size", "--size-sort", str(objfile)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            match = re.search(
                r"^([0-9a-fA-F]{8}) ([0-9a-fA-F]{8}) T "
                r"p2_psram_bank_loader_image$",
                symbols,
                re.MULTILINE,
            )
            self.assertIsNotNone(match)
            self.assertEqual(int(match.group(1), 16) & 3, 0)
            self.assertLessEqual(int(match.group(2), 16), 384 * 4)

            elf = subprocess.run(
                [str(self.readelf), "-W", "-S", "-r", str(objfile)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertIn(".text.p2_psram_bank_loader_image", elf)
            self.assertNotIn(".rela.text.p2_psram_bank_loader_image", elf)

    def test_manager_and_board_api_compile_with_strict_p2_warnings(self):
        common = [
            str(self.clang),
            "--target=p2",
            "-fno-jump-tables",
            "-fno-builtin",
            "-fno-common",
            "-ffunction-sections",
            "-fdata-sections",
            "-Oz",
            "-Wall",
            "-Wextra",
            "-Wshadow",
            "-Wundef",
            "-Wstrict-prototypes",
            "-Werror",
            "-D__ELF__",
            "-I",
            str(ROOT / "include"),
            "-I",
            str(ROOT.parent / "apps/include"),
        ]

        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            sources = (
                APP / "p2bank.c",
                APP / "p2bank_main.c",
                APP / "berry_main.c",
                BOARD / "src/p2_ec32mb_psram.c",
            )
            for source in sources:
                command = list(common)
                if source.parent == APP:
                    command += [
                        "-DCONFIG_SYSTEM_P2BANK_BUFSIZE=4096",
                        '-DCONFIG_SYSTEM_P2BANK_DEFAULT_BANK="/mnt/flash/banks/berry.bin"',
                    ]
                command += ["-c", str(source), "-o", str(directory / (source.name + ".o"))]
                subprocess.run(
                    command,
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                )


if __name__ == "__main__":
    unittest.main()
