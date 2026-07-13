import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

import hil


class I2cSourceTests(unittest.TestCase):
    def test_board_option_selects_the_promised_character_driver(self):
        kconfig = (
            self.root
            / "boards"
            / "p2"
            / "p2x8c4m64p"
            / "p2-ec32mb"
            / "Kconfig"
        ).read_text(encoding="utf-8")
        option = kconfig.split("config P2_EC32MB_I2C", 1)[1].split(
            "config P2_EC32MB_I2C_SDA_PIN", 1
        )[0]

        self.assertIn("select I2C\n", option)
        self.assertIn("select I2C_BITBANG\n", option)
        self.assertIn("select I2C_DRIVER\n", option)

    @classmethod
    def setUpClass(cls):
        cls.root = pathlib.Path(__file__).resolve().parents[3]
        cls.apps = cls.root.parent / "apps"
        cls.app_dir = cls.apps / "testing" / "p2i2c"
        cls.source = (cls.app_dir / "p2i2c_main.c").read_text(encoding="utf-8")
        cls.profile = (
            cls.root
            / "boards"
            / "p2"
            / "p2x8c4m64p"
            / "p2-ec32mb"
            / "configs"
            / "i2c"
            / "defconfig"
        )

    def test_application_build_metadata_is_complete(self):
        for name in (
            "CMakeLists.txt",
            "Kconfig",
            "Make.defs",
            "Makefile",
            "p2i2c_main.c",
        ):
            self.assertTrue((self.app_dir / name).is_file(), name)

    def test_raw_id_read_is_one_repeated_start_transfer(self):
        self.assertIn("messages[0].flags = I2C_M_NOSTOP;", self.source)
        self.assertIn("messages[1].flags = I2C_M_READ;", self.source)
        self.assertIn("transfer.msgc = 2;", self.source)
        self.assertIn("I2CIOC_TRANSFER", self.source)
        self.assertIn("P2I2C_BMP180_ID_REG     0xd0", self.source)
        self.assertIn("P2I2C_BMP180_ID         0x55", self.source)

    def test_pressure_loop_and_multiply_free_hash_are_fixed(self):
        self.assertIn("P2I2C_READING_COUNT     32", self.source)
        self.assertIn("index < P2I2C_READING_COUNT", self.source)
        self.assertIn("nread != sizeof(pressure)", self.source)
        self.assertIn("P2I2C_PRESSURE_MIN_PA   30000", self.source)
        self.assertIn("P2I2C_PRESSURE_MAX_PA   120000", self.source)
        self.assertIn("times403 = (times25 << 4) + times3;", self.source)
        self.assertNotIn("value * UINT32_C(0x01000193)", self.source)

    def test_defconfig_is_the_locked_direct_init_profile(self):
        values = hil.read_kconfig(self.profile)
        hil.validate_i2c_config(values)
        self.assertEqual(values["CONFIG_INIT_ENTRYPOINT"], '"p2i2c_main"')
        self.assertEqual(values["CONFIG_P2_EC32MB_I2C_SDA_PIN"], "24")
        self.assertEqual(values["CONFIG_P2_EC32MB_I2C_SCL_PIN"], "25")


if __name__ == "__main__":
    unittest.main()
