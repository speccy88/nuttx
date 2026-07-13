import pathlib
import unittest


ROOT = pathlib.Path(__file__).parents[3]
BOARD = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb"


class StorageBoardBindingTests(unittest.TestCase):
    def test_storage_profile_enables_late_private_and_block_bindings(self):
        profile = (BOARD / "configs/storage/defconfig").read_text()
        for setting in (
            "CONFIG_BOARD_LATE_INITIALIZE=y",
            "CONFIG_P2_STORAGE=y",
            "CONFIG_P2_EC32MB_STORAGE_BINDINGS=y",
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
        self.assertIn('P2STORAGE:MMCSD=/dev/mmcsd0', source)

    def test_w25_stays_private_while_mmcsd_uses_generic_block_binding(self):
        source = (BOARD / "src/p2_ec32mb_storage.c").read_text()
        self.assertIn("static FAR struct mtd_dev_s *g_p2_w25;", source)
        self.assertIn("g_p2_w25 = w25_initialize(spi);", source)
        self.assertNotIn("register_mtddriver", source)
        self.assertNotIn("smart_initialize", source)
        self.assertIn("mmcsd_spislotinitialize(0, 0, spi)", source)


if __name__ == "__main__":
    unittest.main()
