import pathlib
import unittest


ROOT = pathlib.Path(__file__).parents[3]
BOARD = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb"
P2STORAGE = ROOT.parent / "apps/testing/p2storage/p2storage_main.c"


class StorageBoardBindingTests(unittest.TestCase):
    def test_sd_test_paths_work_without_fat_long_filename_support(self):
        source = P2STORAGE.read_text()
        for path in (
            "p2record.bin",
            "p2dir/source.tmp",
            "p2dir/renamed.bin",
            "p2scrtch.bin",
        ):
            for component in path.split("/"):
                stem, separator, suffix = component.partition(".")
                self.assertLessEqual(len(stem), 8, component)
                if separator:
                    self.assertLessEqual(len(suffix), 3, component)
            self.assertIn('"{}"'.format(path), source)

    def test_board_specific_kconfig_is_reachable(self):
        board_kconfig = (ROOT / "boards/Kconfig").read_text()
        self.assertIn(
            'source "boards/p2/p2x8c4m64p/p2-ec32mb/Kconfig"',
            board_kconfig,
        )

    def test_storage_profile_enables_partitioned_smart_and_block_bindings(self):
        profile = (BOARD / "configs/storage/defconfig").read_text()
        for setting in (
            "CONFIG_BCH=y",
            "CONFIG_BOARD_LATE_INITIALIZE=y",
            "CONFIG_P2_STORAGE=y",
            "CONFIG_P2_EC32MB_STORAGE_BINDINGS=y",
            "CONFIG_P2_EC32MB_W25_PROBE_FREQUENCY=400000",
            "CONFIG_P2_STORAGE_MAX_FREQUENCY=2000000",
            "CONFIG_MTD_PARTITION=y",
            "CONFIG_MTD_SMART=y",
            "CONFIG_MTD_W25=y",
            "CONFIG_W25_SPIFREQUENCY=2000000",
            "CONFIG_MMCSD_SPI=y",
            "CONFIG_MMCSD_IDMODE_CLOCK=400000",
            "CONFIG_MMCSD_SPICLOCK=2000000",
        ):
            self.assertIn(setting, profile)

        kconfig = (ROOT.parent / "apps/testing/p2storage/Kconfig").read_text()
        self.assertIn("depends on BCH && !DISABLE_PSEUDOFS_OPERATIONS", kconfig)

    def test_sd_format_creates_the_partition_layout_required_by_p2_rom(self):
        source = P2STORAGE.read_text()
        for requirement in (
            '#  define P2STORAGE_SD_PARTITION_DEVPATH "/dev/p2sd1"',
            "#  define P2STORAGE_SD_PARTITION_START   UINT32_C(2048)",
            "#  define P2STORAGE_FAT32_PARTITION_TYPE 0x0c",
            "entry[0] = 0x80;",
            "g_io_buffer[P2STORAGE_MBR_SIGNATURE_OFFSET] = 0x55;",
            "g_io_buffer[P2STORAGE_MBR_SIGNATURE_OFFSET + 1] = 0xaa;",
            "register_blockpartition(P2STORAGE_SD_PARTITION_DEVPATH, 0660,",
            "format.ff_hidsec = P2STORAGE_SD_PARTITION_START;",
            "mkfatfs(P2STORAGE_SD_PARTITION_DEVPATH, &format)",
            'P2STORAGE:SD:ROM-MBR:TYPE=0C:START=%',
        ):
            self.assertIn(requirement, source)

        self.assertNotIn("mkfatfs(medium->devpath, &format)", source)

    def test_sd_rom_verifier_is_raw_read_only_and_checks_the_rom_contract(self):
        source = P2STORAGE.read_text()
        begin = source.index("static int p2storage_sd_rom_verify(void)")
        end = source.index("#ifdef CONFIG_TESTING_P2STORAGE_DESTRUCTIVE", begin)
        verifier = source[begin:end]

        for requirement in (
            "fd = open(g_sd.devpath, O_RDONLY);",
            'memcmp(&g_io_buffer[0x17c], "Prop", 4)',
            "root_cluster != 2",
            "UINT32_C(0x41615252)",
            "UINT32_C(0x61417272)",
            "P2STORAGE_P2_ROM_MAX_IMAGE_SIZE",
            'NAME=_BOOT_P2.BIX:CLUSTER=%',
            'P2STORAGE:SD:ROM-CHAIN:FIRST=%',
            'P2STORAGE:SD:ROM-IMAGE:LBA=%',
            'terminal = "SD-ROM-VERIFY";',
        ):
            self.assertIn(requirement, source)

        self.assertNotIn("O_RDWR", verifier)
        self.assertNotIn("p2storage_mount", verifier)
        self.assertNotIn("p2storage_write", verifier)
        self.assertNotIn("mkfatfs", verifier)

    def test_late_init_binds_flash_before_sd_and_reports_exact_exposure(self):
        source = (BOARD / "src/p2_ec32mb_boot.c").read_text()
        flash = source.index("w25_ret = p2_w25_initialize();")
        sd = source.index("mmcsd_ret = p2_mmcsd_initialize();")
        self.assertLess(flash, sd)
        self.assertIn('P2STORAGE:W25=PRIVATE', source)
        self.assertIn('P2STORAGE:W25_FREQUENCY PROBE=', source)
        self.assertIn('P2STORAGE:W25_GEOMETRY BLOCK=', source)
        self.assertIn('P2STORAGE:W25_LAYOUT BOOT=', source)
        self.assertIn('P2STORAGE:W25_BOOT_CRC32=', source)
        self.assertIn(
            'P2STORAGE:W25=UNAVAILABLE:CHECK_FLASH_SWITCH', source
        )
        self.assertIn('P2STORAGE:SMARTFS=/dev/smart0 AUTOFORMAT=NO', source)
        self.assertIn('P2STORAGE:MMCSD=/dev/mmcsd0', source)
        self.assertIn('P2STORAGE:MMCSD_FREQUENCY ID=', source)

    def test_probe_and_transfer_frequencies_have_compile_time_fences(self):
        source = (BOARD / "src/p2_ec32mb_storage.c").read_text()
        self.assertIn(
            "CONFIG_P2_EC32MB_W25_PROBE_FREQUENCY > 400000", source
        )
        self.assertIn(
            "CONFIG_P2_EC32MB_W25_PROBE_FREQUENCY >= "
            "CONFIG_W25_SPIFREQUENCY",
            source,
        )
        self.assertIn(
            "CONFIG_MMCSD_IDMODE_CLOCK >= CONFIG_MMCSD_SPICLOCK", source
        )

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

    def test_absent_w25_is_rejected_before_generic_unbounded_busy_wait(self):
        source = (BOARD / "src/p2_ec32mb_storage.c").read_text()
        probe = source.index("ret = p2_w25_read_jedec(")
        guard = source.index("if (!p2_w25_jedec_valid(info.jedec))", probe)
        generic = source.index("g_p2_w25 = w25_initialize(spi);", guard)

        self.assertLess(probe, guard)
        self.assertLess(guard, generic)
        self.assertIn("jedec[0] == P2_W25_JEDEC_WINBOND", source)
        self.assertIn("jedec[2] == P2_W25_JEDEC_CAPACITY", source)
        self.assertIn("return -ENODEV;", source[guard:generic])

    def test_board_never_autoformats_or_mounts_the_flash_partition(self):
        storage = (BOARD / "src/p2_ec32mb_storage.c").read_text()
        boot = (BOARD / "src/p2_ec32mb_boot.c").read_text()
        self.assertNotIn("mksmartfs", storage + boot)
        self.assertNotIn('nx_mount("/dev/smart0"', storage + boot)


if __name__ == "__main__":
    unittest.main()
