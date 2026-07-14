#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

import os
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
BOARD_ROOT = ROOT / "boards" / "p2" / "p2x8c4m64p"
APPS = pathlib.Path(
    os.environ.get("NUTTX_APPS_DIR", str(ROOT.parent / "apps"))
).resolve()


class BaseProfileTests(unittest.TestCase):
    def read(self, path):
        return path.read_text(encoding="utf-8")

    def config(self, board):
        return self.read(BOARD_ROOT / board / "configs" / "base" / "defconfig")

    def test_common_flat_base_contract(self):
        required = (
            "CONFIG_ARCH_SETJMP_H=y",
            "CONFIG_BUILD_FLAT=y",
            "CONFIG_BUILTIN_COMPILER_RT=y",
            "CONFIG_DEBUG_CUSTOMOPT=y",
            'CONFIG_DEBUG_OPTLEVEL="-Oz"',
            "CONFIG_DEFAULT_SMALL=y",
            "CONFIG_LIBM=y",
            "CONFIG_INTERPRETERS_BERRY=y",
            "CONFIG_INTERPRETERS_BERRY_STACKSIZE=16384",
            "CONFIG_NSH_CLE=y",
            "CONFIG_SYSTEM_CLE=y",
            "CONFIG_SYSTEM_CLE_CMD_HISTORY=y",
            "CONFIG_SYSTEM_CLE_CMD_HISTORY_LEN=8",
            "CONFIG_SYSTEM_CLE_CMD_HISTORY_LINELEN=80",
            "CONFIG_SYSTEM_TERMCURSES_ESCDELAY_MS=30",
            "CONFIG_SYSTEM_VI=y",
            "CONFIG_SYSTEM_VI_COLS=80",
            "CONFIG_SYSTEM_VI_ROWS=24",
            "CONFIG_P2_EDGE_BASE_IMAGE=y",
            "CONFIG_P2_EC32MB_FLASHBOOT=y",
            "CONFIG_P2_EC32MB_SDCARD_AUTOMOUNT=y",
            "CONFIG_MTD_SMART=y",
            "CONFIG_MMCSD_SPI=y",
            "CONFIG_FS_SMARTFS=y",
            "CONFIG_FS_FAT=y",
            "CONFIG_TESTING_P2STORAGE=y",
            "# CONFIG_TESTING_P2STORAGE_DESTRUCTIVE is not set",
        )
        forbidden = (
            "CONFIG_ELF=y",
            "CONFIG_MODULES=y",
            "CONFIG_NSH_FILE_APPS=y",
            "CONFIG_NSH_READLINE=y",
            "CONFIG_GRAPHICS_LVGL=y",
            "CONFIG_LCD=y",
            "CONFIG_VIDEO=y",
            "CONFIG_INPUT=y",
            "CONFIG_INTERPRETERS_BERRY_LVGL=y",
            "CONFIG_SYSTEM_P2BANK=y",
            "CONFIG_SYSTEM_P2BERRYBANK=y",
            "CONFIG_TESTING_P2PSRAM=y",
        )

        for board in ("p2-ec32mb", "p2-ec"):
            config = self.config(board)
            for setting in required:
                self.assertIn(setting, config, f"{board}: missing {setting}")
            for setting in forbidden:
                self.assertNotIn(setting, config, f"{board}: contains {setting}")

    def test_psram_driver_is_ec32mb_only_and_not_a_berry_heap(self):
        ec32 = self.config("p2-ec32mb")
        rev_d = self.config("p2-ec")
        self.assertIn("CONFIG_P2_EC32MB_PSRAM=y", ec32)
        self.assertNotIn("CONFIG_P2_EC32MB_PSRAM=y", rev_d)

        berry = self.read(APPS / "interpreters" / "berry" / "include" / "berry_conf.h")
        self.assertIn("external PSRAM remains an", berry)
        self.assertIn("deliberately not used by malloc()", berry)

    def test_compact_p2_berry_keeps_console_and_core_language(self):
        berry_dir = APPS / "interpreters" / "berry"
        config = self.read(berry_dir / "include" / "berry_conf.h")
        coc = self.read(berry_dir / "include" / "berry_coc_p2.h")
        makefile = self.read(berry_dir / "Makefile")

        for setting in (
            "#  define BE_INTGER_TYPE                1",
            "#  define BE_USE_SINGLE_FLOAT           1",
            "#  define BE_USE_FILE_SYSTEM            1",
            "#  define BE_USE_SCRIPT_COMPILER        1",
            "#  define BE_USE_BYTECODE_SAVER         1",
        ):
            self.assertIn(setting, config)
        self.assertIn("#define BE_USE_STRING_MODULE 0", coc)
        self.assertIn("0002-Use-NuttX-console-reader.patch", makefile)
        self.assertIn("-DUSE_NUTTX_CONSOLE", makefile)
        self.assertNotIn("be_lvgl.c", makefile)

    def test_boot_scripts_mount_without_formatting_or_provisioning(self):
        for board in ("p2-ec32mb", "p2-ec"):
            rc_s = self.read(BOARD_ROOT / board / "src" / "etc" / "init.d" / "rcS")
            self.assertIn("mount -t smartfs /dev/smart0 /mnt/flash", rc_s)
            self.assertIn("mount -t vfat /dev/mmcsd0 /mnt/sd", rc_s)
            self.assertIn("AUTOFORMAT=NO", rc_s)
            self.assertIn(f"P2BASE:READY:BOARD={board}:APPS=berry,vi", rc_s)
            self.assertNotIn("mkfatfs", rc_s)
            self.assertNotIn("mksmartfs", rc_s)
            self.assertNotIn("cp /etc/berry-p2", rc_s)

            smoke = BOARD_ROOT / board / "src" / "etc" / "berry-p2" / "core_smoke.be"
            self.assertIn("P2BERRY:CORE=PASS", self.read(smoke))

    def test_p2_setjmp_and_compiler_regression_guards_are_present(self):
        arch_kconfig = self.read(ROOT / "arch" / "Kconfig")
        self.assertIn("select ARCH_HAVE_SETJMP", arch_kconfig)
        self.assertTrue((ROOT / "arch" / "p2" / "include" / "setjmp.h").is_file())
        self.assertTrue(
            (ROOT / "arch" / "p2" / "src" / "common" / "p2_setjmp.S").is_file()
        )

        patch = self.read(ROOT / "tools" / "p2" / "patches" / "p2llvm-preempt-safe-integer.patch")
        self.assertIn(
            'DecoderMethod = "DecodeTJInstruction", isBranch = 1, isTerminator = 1',
            patch,
        )
        bootstrap = self.read(ROOT / "tools" / "p2" / "bootstrap-local.sh")
        self.assertIn("p2llvm_conditional_branch_valid", bootstrap)

    def test_explicit_apps_worktree_overrides_persistent_default(self):
        build = self.read(ROOT / "tools" / "p2" / "build.sh")
        saved = build.index("caller_apps=${NUTTX_APPS_DIR:-}")
        restored = build.index("NUTTX_APPS_DIR=$caller_apps")
        selected = build.index("apps=${NUTTX_APPS_DIR:-$ROOT/../apps}")
        self.assertLess(saved, restored)
        self.assertLess(restored, selected)


if __name__ == "__main__":
    unittest.main()
