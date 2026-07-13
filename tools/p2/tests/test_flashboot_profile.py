#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
BOARD = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb"
APPS = ROOT.parent / "apps"


class FlashBootProfileTests(unittest.TestCase):
    def test_profile_has_reset_romfs_and_non_destructive_verifier(self):
        profile = (BOARD / "configs/flashboot/defconfig").read_text()
        for setting in (
            "CONFIG_BOARDCTL_RESET=y",
            "CONFIG_ETC_ROMFS=y",
            "CONFIG_FS_ROMFS=y",
            "CONFIG_P2_EC32MB_FLASHBOOT=y",
            "CONFIG_TESTING_P2STORAGE=y",
            "CONFIG_TESTING_P2STORAGE_FLASH_PREMOUNTED=y",
        ):
            self.assertIn(setting, profile)
        for disabled in (
            "# CONFIG_TESTING_P2STORAGE_DESTRUCTIVE is not set",
            "# CONFIG_FSUTILS_MKFATFS is not set",
            "# CONFIG_FSUTILS_MKSMARTFS is not set",
        ):
            self.assertIn(disabled, profile)

    def test_startup_mounts_exact_partition_and_never_formats(self):
        script = (BOARD / "src/etc/init.d/rcS").read_text()
        makefile = (BOARD / "src/Makefile").read_text()
        self.assertIn("RCSRCS = etc/init.d/rc.sysinit etc/init.d/rcS", makefile)
        self.assertIn("mount -t smartfs /dev/smart0 /mnt/flash", script)
        self.assertIn("P2FLASHBOOT:SMARTFS=/dev/smart0@/mnt/flash", script)
        self.assertIn("DESTRUCTIVE_HANDLERS=ABSENT", script)
        self.assertNotIn("mksmartfs", script)
        self.assertNotIn("mkfatfs", script)

    def test_board_compile_fences_reject_destructive_flashboot_images(self):
        source = (BOARD / "src/p2_ec32mb_boot.c").read_text()
        for symbol in (
            "CONFIG_TESTING_P2STORAGE_DESTRUCTIVE",
            "CONFIG_FSUTILS_MKSMARTFS",
            "CONFIG_FSUTILS_MKFATFS",
            "CONFIG_TESTING_P2STORAGE_FLASH_PREMOUNTED",
            "CONFIG_BOARDCTL_RESET",
        ):
            self.assertIn(symbol, source)
        arch_kconfig = (ROOT / "arch/p2/Kconfig").read_text()
        chip = arch_kconfig.index("config ARCH_CHIP_P2X8C4M64P")
        custom = arch_kconfig.index("config ARCH_CHIP_P2_CUSTOM")
        self.assertIn("select ARCH_HAVE_RESET", arch_kconfig[chip:custom])
        chip_source = (ROOT / "arch/p2/src/p2x8c4m64p/p2_chip.c").read_text()
        self.assertIn("int board_reset(int status)", chip_source)
        self.assertIn("#include <nuttx/board.h>", chip_source)

    def test_flash_verify_can_use_startup_mount_without_unmounting_it(self):
        source = (APPS / "testing/p2storage/p2storage_main.c").read_text()
        kconfig = (APPS / "testing/p2storage/Kconfig").read_text()
        self.assertIn("CONFIG_TESTING_P2STORAGE_FLASH_PREMOUNTED", source)
        self.assertIn("premounted = medium == &g_flash", source)
        self.assertIn("premounted ? 0 : p2storage_unmount(medium)", source)
        self.assertIn("config TESTING_P2STORAGE_FLASH_PREMOUNTED", kconfig)


if __name__ == "__main__":
    unittest.main()
