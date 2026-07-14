#!/usr/bin/env python3
#
# SPDX-License-Identifier: Apache-2.0
#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.  The
# ASF licenses this file to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance with the
# License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the
# License for the specific language governing permissions and limitations
# under the License.

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
BOARD_SOURCE = (
    ROOT
    / "boards"
    / "p2"
    / "p2x8c4m64p"
    / "p2-ec32mb"
    / "src"
    / "p2_ec32mb_spi.c"
)
GENERIC_SOURCE = ROOT / "drivers" / "spi" / "spi_bitbang.c"
APP = ROOT.parent / "apps" / "testing" / "p2smartpins"


def function_body(source, name):
    start = source.rindex(name)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening : index + 1]
    raise AssertionError("unterminated function {}".format(name))


class SmartpinsSpiSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = BOARD_SOURCE.read_text(encoding="utf-8")
        cls.generic_source = GENERIC_SOURCE.read_text(encoding="utf-8")
        cls.app_source = (APP / "p2smartpins_main.c").read_text(encoding="utf-8")
        cls.app_kconfig = (APP / "Kconfig").read_text(encoding="utf-8")

    def test_uses_standard_generic_bitbang_character_driver(self):
        self.assertIn("<nuttx/spi/spi_bitbang.h>", self.source)
        self.assertIn("<nuttx/spi/spi_transfer.h>", self.source)
        self.assertIn(
            "spi_create_bitbang(&g_p2_spi_ops, &g_p2_spi_lower)", self.source
        )
        self.assertIn("spi_register(g_p2_spi, 0)", self.source)
        self.assertIn("devid == SPIDEV_USER(0)", self.source)
        self.assertIn("SPIDEV_DISPLAY(0)", self.source)
        self.assertIn("SPIDEV_TOUCHSCREEN(0)", self.source)
        self.assertNotIn("P2_STORAGE", self.source)

    def test_receiver_is_claimed_and_configured_before_source_is_enabled(self):
        activate = function_body(self.source, "static int p2_spi_activate")
        claim_miso = activate.index("CONFIG_P2_EC32MB_SPI_MISO_PIN")
        claim_mosi = activate.index("CONFIG_P2_EC32MB_SPI_MOSI_PIN")
        configure_miso = activate.index(
            "p2_pin_configure(CONFIG_P2_EC32MB_SPI_MISO_PIN"
        )
        enable_mosi = activate.index(
            "p2_sp_dir_high(CONFIG_P2_EC32MB_SPI_MOSI_PIN)"
        )
        self.assertLess(claim_miso, claim_mosi)
        self.assertLess(configure_miso, enable_mosi)

    def test_unconnected_clock_and_select_are_outputs_with_safe_rollback(self):
        activate = function_body(self.source, "static int p2_spi_activate")
        release = function_body(self.source, "static void p2_spi_release")
        self.assertIn("CONFIG_P2_EC32MB_SPI_SCK_PIN", activate)
        self.assertIn("CONFIG_P2_EC32MB_SPI_CS_PIN", activate)
        self.assertLess(
            release.index("CONFIG_P2_EC32MB_SPI_MOSI_PIN"),
            release.rindex("CONFIG_P2_EC32MB_SPI_MISO_PIN"),
        )
        self.assertIn("p2_spi_release(priv);", activate)
        self.assertIn("priv->claims = 0;", release)
        self.assertIn("priv->selected = false;", release)

    def test_select_and_deselect_are_idempotent_and_fail_closed(self):
        select = function_body(self.source, "static void p2_spi_select")
        exchange = function_body(self.source, "static uint16_t p2_spi_exchange")
        self.assertIn("if (!selected)", select)
        self.assertIn("if (priv->selected || priv->faulted)", select)
        self.assertIn("priv->faulted = true;", select)
        self.assertIn("return UINT16_MAX;", exchange)

    def test_generic_variable_width_exchange_stores_eight_bit_receive_data(self):
        exchange = function_body(
            self.generic_source, "static void spi_exchange"
        )
        self.assertIn("if (priv->nbits > 8)", exchange)
        self.assertIn(
            "else\n#endif\n            {\n              *dest++ = (uint8_t)datain;",
            exchange,
        )

    def test_application_uses_exact_standard_transfer_and_evidence(self):
        self.assertIn("SPIIOC_TRANSFER", self.app_source)
        self.assertIn("sequence.dev = SPIDEV_USER(0);", self.app_source)
        self.assertIn("sequence.mode = SPIDEV_MODE0;", self.app_source)
        self.assertIn("sequence.nbits = 8;", self.app_source)
        self.assertIn("transaction.deselect = true;", self.app_source)
        self.assertIn("MODE=0:REQUEST_HZ=%u", self.app_source)
        self.assertIn("SAFE=MOSI%d,MISO%d,SCK%d,CS%d=FLOAT", self.app_source)

    def test_application_kconfig_requires_board_lower_half(self):
        spi = self.app_kconfig.index("config TESTING_P2SMARTPINS_SPI")
        devpath = self.app_kconfig.index(
            "config TESTING_P2SMARTPINS_SPI_DEVPATH", spi
        )
        section = self.app_kconfig[spi:devpath]
        self.assertIn(
            "depends on P2_EC32MB_SPI && SPI_DRIVER && SPI_EXCHANGE", section
        )
        self.assertIn("P8 clock and P9 chip select", section)


if __name__ == "__main__":
    unittest.main()
