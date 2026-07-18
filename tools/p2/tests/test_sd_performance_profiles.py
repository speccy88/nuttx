#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
BOARD = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb"


class SdPerformanceProfileTests(unittest.TestCase):
    def test_onboard_socket_is_honestly_bounded_below_record_target(self):
        board = (BOARD / "include/board.h").read_text()
        self.assertIn("#define BOARD_SD_MISO_PIN 58", board)
        self.assertIn("#define BOARD_SD_MOSI_PIN 59", board)
        self.assertIn("#define BOARD_SD_CS_PIN 60", board)
        self.assertIn("#define BOARD_SD_CLK_PIN 61", board)

        sysclk_hz = 180_000_000
        fastest_configured_spi_hz = sysclk_hz // 4
        self.assertLess(fastest_configured_spi_hz // 8, 40_000_000)

    def test_onboard_performance_profile_is_read_only_and_mode3(self):
        profile = (BOARD / "configs/sdspi-perf/defconfig").read_text()
        for setting in (
            "CONFIG_P2_STORAGE_SMARTPIN_SPI=y",
            "CONFIG_P2_STORAGE_SD_MODE3=y",
            "CONFIG_P2_STORAGE_MAX_FREQUENCY=36000000",
            "CONFIG_MMCSD_SPICLOCK=36000000",
            "CONFIG_MMCSD_SPIMODE=3",
            "CONFIG_MMCSD_READONLY=y",
            "CONFIG_TESTING_P2STORAGE=y",
        ):
            self.assertIn(setting, profile)

        self.assertIn("# CONFIG_TESTING_P2STORAGE_DESTRUCTIVE is not set", profile)

    def test_smartpin_receive_has_bounded_failure_and_safe_idle(self):
        source = (BOARD / "src/p2_ec32mb_storage.c").read_text()
        begin = source.index("static int p2_storage_smartpin_recv(")
        end = source.index("\n#endif", begin)
        receive = source[begin:end]

        for requirement in (
            "P2_SP_PULSE",
            "P2_SP_SYNC_RX",
            "p2_storage_wait_smartpin",
            "p2_storage_smartpin_stop();",
            "p2_storage_fault(priv, -ETIMEDOUT);",
            "memset(buffer, 0xff, nbytes);",
            "nbytes != P2_STORAGE_SMARTPIN_BLOCK_BYTES",
            "enter_critical_section();",
            "leave_critical_section(flags);",
        ):
            self.assertIn(requirement, receive)

        self.assertIn("p2_storage_deadline_expired", source)
        self.assertIn("p2_sdspi_get_last_error", source)
        self.assertIn("SPI_STATUS_TRANSFER_ERROR", source)
        self.assertIn("p2_storage_arbiter_recover", source)

        mmcsd = (ROOT / "drivers/mmcsd/mmcsd_spi.c").read_text()
        self.assertIn("SPI_STATUS_TRANSFER_ERROR", mmcsd)

    def test_overclock_requires_explicit_opt_in(self):
        kconfig = (ROOT / "arch/p2/Kconfig").read_text()
        clock = (ROOT / "arch/p2/src/p2x8c4m64p/p2_chip.c").read_text()
        self.assertIn("config P2_EXPERIMENTAL_OVERCLOCK", kconfig)
        self.assertIn(
            "range 20000000 180000000 if !P2_EXPERIMENTAL_OVERCLOCK",
            kconfig,
        )
        self.assertIn("CONFIG_P2_SYSCLK_HZ > 180000000", clock)
        self.assertIn("CONFIG_P2_EXPERIMENTAL_OVERCLOCK", clock)

    def test_native_record_profile_has_bus_headroom_and_locked_integrity_run(self):
        profile = (BOARD / "configs/sdio-record/defconfig").read_text()
        for setting in (
            "CONFIG_P2_EXPERIMENTAL_OVERCLOCK=y",
            "CONFIG_P2_SYSCLK_HZ=360000000",
            "CONFIG_P2_EC32MB_SDIO_NATIVE=y",
            "CONFIG_P2_EC32MB_SDIO_COMMAND_HZ=5000000",
            "CONFIG_P2_EC32MB_SDIO_DIVISOR=3",
            "CONFIG_P2_EC32MB_SDIO_ALLOW_OVERCLOCK=y",
            "CONFIG_P2_EC32MB_SDIO_MAX_TRANSFER=262144",
            "CONFIG_MMCSD_MULTIBLOCK_LIMIT=512",
            "CONFIG_MMCSD_READONLY=y",
            "CONFIG_TESTING_P2STORAGE_SD_BENCHMARK=y",
            "CONFIG_TESTING_P2STORAGE_BENCHMARK_BUFFER_SIZE=65536",
        ):
            self.assertIn(setting, profile)

        self.assertIn(
            "# CONFIG_P2_EC32MB_SDIO_VERIFY_FAST_CRC16 is not set",
            profile,
        )
        self.assertIn("# CONFIG_MMCSD_SPI is not set", profile)
        self.assertIn("# CONFIG_TESTING_P2STORAGE_DESTRUCTIVE is not set", profile)

        raw_ceiling_bps = (360_000_000 // 3) * 4 // 8
        self.assertEqual(raw_ceiling_bps, 60_000_000)
        self.assertGreater(raw_ceiling_bps, 41_000_000)

    def test_native_streamer_retains_crc_baseline_and_fast_trailer_modes(self):
        source = (BOARD / "src/p2_ec32mb_sdio.c").read_text()
        service = (BOARD / "src/p2_ec32mb_sdio_service.S").read_text()
        wire = (BOARD / "src/p2_ec32mb_sdio_wire.h").read_text()

        for requirement in (
            "p2_sdio_crc7",
            "p2_sdio_validate_fast_crc",
            "p2_sdio_calibrate_phase",
            "p2_sdio_native_set_fast_crc16",
            "MMCSD_MULTIBLOCK",
            "p2_sdio_abort_data",
            "SDIO_STATUS_WRPROTECTED",
            "p2_sdio_start_service",
        ):
            self.assertIn(requirement, source)

        self.assertIn("priv->phase_calibrated && priv->wide", source)
        self.assertIn("g_p2_sdio_wire.verify_crc = verify_crc", source)
        self.assertIn("P2_SDIO_WIRE_VERIFY_CRC_OFFSET", wire)
        self.assertIn("add     value, #17", service)
        self.assertIn("if_z  jmp     #.Ltrailer_done", service)
        self.assertIn("P2_SDIO_WIRE_STATUS_ETIMEDOUT", service)
        self.assertIn("native SDIO service overlaps cog scratch registers", service)

    def test_native_cmd6_waits_before_exposing_high_speed(self):
        source = (BOARD / "src/p2_ec32mb_sdio.c").read_text()
        begin = source.index("static int p2_sdio_switch_hs(")
        end = source.index("\nstatic void p2_sdio_select_data_clock", begin)
        switch = source[begin:end]

        self.assertIn("#define P2_SDIO_SWITCH_CLOCKS        8u", source)
        self.assertIn("if (ret == OK && set)", switch)
        self.assertIn("p2_sdio_clock_idle(priv, P2_SDIO_SWITCH_CLOCKS);", switch)
        self.assertLess(
            switch.index("p2_sdio_read_manual"),
            switch.index("p2_sdio_clock_idle"),
        )
        self.assertLess(
            switch.index("p2_sdio_clock_idle"),
            switch.index("if (set)", switch.index("p2_sdio_clock_idle")),
        )

    def test_native_streamer_uses_exclusive_clock_handoff(self):
        source = (BOARD / "src/p2_ec32mb_sdio.c").read_text()
        service = (BOARD / "src/p2_ec32mb_sdio_service.S").read_text()
        begin = source.index("static int p2_sdio_read_service(")
        end = source.index("\nstatic int p2_sdio_phase_read", begin)
        request = source[begin:end]
        publish = service[
            service.index(".Lpublish:") : service.index(
                ".Lidle", service.index(".Lpublish:")
            )
        ]

        self.assertLess(
            request.index("p2_sdio_clock_release();"),
            request.index("g_p2_sdio_wire.request = sequence;"),
        )
        self.assertLess(
            request.index("g_p2_sdio_wire.complete != sequence"),
            request.index("p2_sdio_clock_low();"),
        )
        for requirement in ("dirl    #21", "wrpin   #0, #21", "outl    #21"):
            self.assertIn(requirement, publish)
        self.assertLess(
            publish.index("dirl    #21"),
            publish.index("P2_SDIO_WIRE_COMPLETE_OFFSET"),
        )

        xzero = service.index(".long   0xfcb5e200")
        self.assertLess(xzero, service.index("dirh    #21", xzero))

    def test_native_fixture_power_cycle_is_electrically_safe(self):
        source = (BOARD / "src/p2_ec32mb_sdio.c").read_text()
        kconfig = (BOARD / "Kconfig").read_text()
        document = (ROOT / "Documentation/platforms/p2/sd-performance.rst").read_text()

        for requirement in (
            "P2_SDIO_POWER_OFF_USEC       10000u",
            "P2_SDIO_POWER_STABLE_USEC    5000u",
            "p2_sdio_force_powerdown();",
            "up_udelay(P2_SDIO_POWER_OFF_USEC);",
            "up_udelay(P2_SDIO_POWER_STABLE_USEC);",
        ):
            self.assertIn(requirement, source)

        self.assertLess(
            source.index("p2_sdio_force_powerdown();"),
            source.index("p2_sdio_pin_low(P2_SDIO_POWER_PIN);"),
        )
        self.assertLess(
            source.index("p2_sdio_pin_low(P2_SDIO_POWER_PIN);"),
            source.index("up_udelay(P2_SDIO_POWER_OFF_USEC);"),
        )
        for requirement in (
            "actively discharge the card supply",
            "pull-ups must connect to that switched supply",
        ):
            self.assertIn(requirement, kconfig)
        for requirement in (
            "common P2/card ground",
            "switched card\nVDD",
            "hardware pull-down",
            "below 0.5 V for at least 1 ms",
            "disables the switch for 10 ms",
        ):
            self.assertIn(requirement, document)

    def test_native_fixture_rejects_configured_pin_overlap(self):
        source = (BOARD / "src/p2_ec32mb_sdio.c").read_text()

        self.assertIn("#define P2_SDIO_FIXTURE_PIN(pin)", source)
        for symbol in (
            "CONFIG_P2_EC32MB_GPIO_OUT_PIN",
            "CONFIG_P2_EC32MB_GPIO_IN_PIN",
            "CONFIG_P2_EC32MB_UART1_TX_PIN",
            "CONFIG_P2_EC32MB_UART1_RX_PIN",
            "CONFIG_P2_EC32MB_PWM_PIN",
            "CONFIG_P2_EC32MB_CAPTURE_PIN",
            "CONFIG_P2_EC32MB_ADC_PIN",
            "CONFIG_P2_EC32MB_DAC_PIN",
            "CONFIG_P2_EC32MB_SPI_MOSI_PIN",
            "CONFIG_P2_EC32MB_SPI_MISO_PIN",
            "CONFIG_P2_EC32MB_SPI_SCK_PIN",
            "CONFIG_P2_EC32MB_SPI_CS_PIN",
            "CONFIG_P2_EC32MB_I2C_SDA_PIN",
            "CONFIG_P2_EC32MB_I2C_SCL_PIN",
        ):
            self.assertIn("P2_SDIO_FIXTURE_PIN({})".format(symbol), source)

        self.assertIn("overlaps configured GPIO", source)
        self.assertIn("overlaps configured UART1", source)
        self.assertIn("overlaps configured SPI", source)
        self.assertIn("overlaps configured I2C", source)


if __name__ == "__main__":
    unittest.main()
