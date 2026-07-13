import pathlib
import unittest


ROOT = pathlib.Path(__file__).parents[3]
BOARD = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb"


class StorageBoardBindingTests(unittest.TestCase):
    def test_board_specific_kconfig_is_reachable(self):
        board_kconfig = (ROOT / "boards/Kconfig").read_text()
        self.assertIn(
            'source "boards/p2/p2x8c4m64p/p2-ec32mb/Kconfig"',
            board_kconfig,
        )

    def test_storage_profile_enables_partitioned_smart_and_block_bindings(self):
        profile = (BOARD / "configs/storage/defconfig").read_text()
        for setting in (
            "CONFIG_BOARD_LATE_INITIALIZE=y",
            "CONFIG_P2_STORAGE=y",
            "CONFIG_P2_EC32MB_STORAGE_BINDINGS=y",
            "CONFIG_MTD_PARTITION=y",
            "CONFIG_MTD_SMART=y",
            "CONFIG_MTD_W25=y",
            "CONFIG_MMCSD_SPI=y",
        ):
            self.assertIn(setting, profile)

    def test_late_init_binds_flash_before_sd_and_reports_exact_exposure(self):
        source = (BOARD / "src/p2_ec32mb_boot.c").read_text()
        flash = source.index("w25_ret = p2_w25_initialize();")
        sd = source.index("mmcsd_ret = p2_mmcsd_initialize();")
        self.assertLess(flash, sd)
        self.assertIn('P2STORAGE:W25=PRIVATE', source)
        self.assertIn('P2STORAGE:W25_GEOMETRY BLOCK=', source)
        self.assertIn('P2STORAGE:W25_LAYOUT BOOT=', source)
        self.assertIn('P2STORAGE:W25_BOOT_CRC32=', source)
        self.assertIn('P2STORAGE:SMARTFS=/dev/smart0 AUTOFORMAT=NO', source)
        self.assertIn('P2STORAGE:MMCSD=/dev/mmcsd0', source)

    def test_raw_w25_stays_private_while_only_data_partition_gets_smart(self):
        source = (BOARD / "src/p2_ec32mb_storage.c").read_text()
        self.assertIn("static FAR struct mtd_dev_s *g_p2_w25;", source)
        self.assertIn("static FAR struct mtd_dev_s *g_p2_w25_data;", source)
        self.assertIn("g_p2_w25 = w25_initialize(spi);", source)
        self.assertIn(
            "g_p2_w25_data = mtd_partition(g_p2_w25, 2048, 63488);",
            source,
        )
        self.assertIn("smart_initialize(0, g_p2_w25_data, NULL)", source)
        self.assertIn("p2_w25_boot_crc32(g_p2_w25", source)
        self.assertNotIn("register_mtddriver", source)
        self.assertIn("mmcsd_spislotinitialize(0, 0, spi)", source)

    def test_board_never_autoformats_or_mounts_the_flash_partition(self):
        storage = (BOARD / "src/p2_ec32mb_storage.c").read_text()
        boot = (BOARD / "src/p2_ec32mb_boot.c").read_text()
        self.assertNotIn("mksmartfs", storage + boot)
        self.assertNotIn('nx_mount("/dev/smart0"', storage + boot)


if __name__ == "__main__":
    unittest.main()
