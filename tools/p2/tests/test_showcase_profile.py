#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
BOARD_ROOT = ROOT / "boards" / "p2" / "p2x8c4m64p"


class ShowcaseProfileTests(unittest.TestCase):
    def read(self, path):
        return path.read_text(encoding="utf-8")

    def config(self, board):
        return self.read(BOARD_ROOT / board / "configs" / "showcase" / "defconfig")

    def test_common_showcase_contract(self):
        required = (
            "CONFIG_BOARD_LATE_INITIALIZE=y",
            "CONFIG_ETC_ROMFS=y",
            "CONFIG_NSH_READLINE=y",
            "CONFIG_READLINE_TABCOMPLETION=y",
            "CONFIG_READLINE_CMD_HISTORY=y",
            "CONFIG_TTY_SIGINT=y",
            "CONFIG_SIG_DEFAULT=y",
            "CONFIG_USERLED_LOWER=y",
            "CONFIG_P2_EC32MB_GPIO=y",
            "CONFIG_P2_EC32MB_UART1=y",
            "CONFIG_P2_EC32MB_PWM=y",
            "CONFIG_P2_EC32MB_CAPTURE=y",
            "CONFIG_P2_EC32MB_ADC=y",
            "CONFIG_P2_EC32MB_DAC=y",
            "CONFIG_P2_EC32MB_SPI=y",
            "CONFIG_P2_EC32MB_I2C=y",
            "CONFIG_P2_EC32MB_BMP180=y",
            "CONFIG_P2_EC32MB_FLASHBOOT=y",
            "CONFIG_MTD_SMART=y",
            "CONFIG_MMCSD_SPI=y",
            "CONFIG_FS_SMARTFS=y",
            "CONFIG_FS_FAT=y",
            "CONFIG_SYSTEM_P2HELP=y",
            "CONFIG_TESTING_P2SMARTPINS=y",
            "CONFIG_TESTING_P2I2C=y",
            "CONFIG_TESTING_P2STORAGE=y",
        )

        for board in ("p2-ec32mb", "p2-ec"):
            config = self.config(board)
            for symbol in required:
                self.assertIn(symbol, config, f"{board}: missing {symbol}")
            self.assertIn("# CONFIG_ARCH_LEDS is not set", config)
            self.assertIn("# CONFIG_FSUTILS_MKFATFS is not set", config)
            self.assertIn("# CONFIG_FSUTILS_MKSMARTFS is not set", config)
            self.assertIn("# CONFIG_SYSTEM_DD is not set", config)
            self.assertIn("# CONFIG_TESTING_P2STORAGE_DESTRUCTIVE is not set", config)

    def test_board_specific_psram_and_led_contract(self):
        ec32_config = self.config("p2-ec32mb")
        ec_config = self.config("p2-ec")
        self.assertIn("CONFIG_P2_EC32MB_PSRAM=y", ec32_config)
        self.assertIn("CONFIG_TESTING_P2PSRAM=y", ec32_config)
        self.assertNotIn("CONFIG_P2_EC32MB_PSRAM=y", ec_config)
        self.assertNotIn("CONFIG_TESTING_P2PSRAM=y", ec_config)

        ec32_header = self.read(BOARD_ROOT / "p2-ec32mb" / "include" / "board.h")
        ec_header = self.read(BOARD_ROOT / "p2-ec" / "include" / "board.h")
        self.assertIn("#define BOARD_LED0_PIN 38", ec32_header)
        self.assertIn("#define BOARD_LED1_PIN 39", ec32_header)
        self.assertIn("#define BOARD_HAVE_PSRAM 1", ec32_header)
        self.assertIn("#define BOARD_LED0_PIN              56", ec_header)
        self.assertIn("#define BOARD_LED1_PIN              57", ec_header)
        self.assertNotIn("BOARD_HAVE_PSRAM", ec_header)

    def test_markers_and_sd_writer_size_gate(self):
        for board in ("p2-ec32mb", "p2-ec"):
            rc_s = self.read(BOARD_ROOT / board / "src" / "etc" / "init.d" / "rcS")
            self.assertIn(f"P2SHOWCASE:READY:BOARD={board}:RUN=p2help", rc_s)

        build = self.read(ROOT / "tools" / "p2" / "build.sh")
        self.assertIn("p2-ec32mb|p2-ec", build)
        self.assertIn("sd_boot_max=491516", build)
        self.assertIn("P2SHOWCASE:READY:BOARD=$board:RUN=p2help", build)

    def test_ctrl_c_service_cannot_be_starved(self):
        timer = self.read(ROOT / "arch" / "p2" / "src" / "common" / "p2_timer.c")
        timer_isr = timer[timer.index("static int p2_timer_isr") :]
        self.assertIn("p2_serialinterrupt(irq, context, arg);", timer_isr)
        self.assertLess(
            timer_isr.index("p2_serialinterrupt(irq, context, arg);"),
            timer_isr.index("nxsched_process_timer();"),
        )

        serial = self.read(ROOT / "arch" / "p2" / "src" / "common" / "p2_serial.c")
        service = serial[serial.index("static void p2_uart_service(void)\n{") :]
        first_enter = service.index("flags = enter_critical_section();")
        guard = service.index("if (g_p2_uart_priv.servicing)")
        claim = service.index("g_p2_uart_priv.servicing = true;")
        first_leave = service.index("leave_critical_section(flags);", claim)
        self.assertLess(first_enter, guard)
        self.assertLess(guard, claim)
        self.assertLess(claim, first_leave)
        self.assertIn("g_p2_uart_priv.servicing = false;", service)

        nsh = self.read(ROOT.parent / "apps" / "nshlib" / "nsh_proccmds.c")
        sleep_commands = nsh[
            nsh.index("struct nsh_sleep_signal_s") : nsh.index("int cmd_uptime")
        ]
        self.assertIn("TIOCSCTTY", sleep_commands)
        self.assertIn("TIOCNOTTY", sleep_commands)
        self.assertIn("sigaction(SIGINT, &act, &state->oldact)", sleep_commands)
        self.assertIn("sigaction(SIGINT, &state->oldact, NULL)", sleep_commands)
        self.assertEqual(sleep_commands.count("nsh_sleep_prepare(vtbl"), 2)
        self.assertEqual(sleep_commands.count("nsh_sleep_cleanup(vtbl"), 2)


if __name__ == "__main__":
    unittest.main()
