#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

"""Fail-closed source checks for the P2 architecture RNG path."""

from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
BOARD = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb"


class P2RngSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (ROOT / "arch/p2/src/common/p2_rng.c").read_text(
            encoding="utf-8"
        )
        cls.arch_kconfig = (ROOT / "arch/p2/Kconfig").read_text(
            encoding="utf-8"
        )
        cls.make_defs = (ROOT / "arch/p2/src/common/Make.defs").read_text(
            encoding="utf-8"
        )
        cls.arch_makefile = (ROOT / "arch/p2/src/Makefile").read_text(
            encoding="utf-8"
        )
        cls.profile = (BOARD / "configs/python/defconfig").read_text(
            encoding="utf-8"
        )
        cls.build = (ROOT / "tools/p2/build.sh").read_text(encoding="utf-8")
        cls.hil = (ROOT / "tools/p2/test-python.py").read_text(encoding="utf-8")

    def test_p2_chips_advertise_the_architecture_rng(self) -> None:
        for symbol in ("ARCH_CHIP_P2X8C4M64P", "ARCH_CHIP_P2_CUSTOM"):
            start = self.arch_kconfig.index("config " + symbol)
            end = self.arch_kconfig.find("\nconfig ", start + 1)
            if end < 0:
                end = len(self.arch_kconfig)
            self.assertIn("select ARCH_HAVE_RNG", self.arch_kconfig[start:end])

    def test_driver_is_linked_for_random_or_arch_urandom(self) -> None:
        self.assertIn(
            "ifneq ($(filter y,$(CONFIG_DEV_RANDOM) "
            "$(CONFIG_DEV_URANDOM_ARCH)),)",
            self.make_defs,
        )
        self.assertIn("CMN_CSRCS += p2_rng.c", self.make_defs)
        self.assertNotRegex(
            self.make_defs,
            r"p2_rng\$\(OBJEXT\):\s*P2_UNIFIED_MEMORY_FLAGS\s*=",
        )

    def test_conditioner_has_explicit_crypto_and_device_dependencies(self) -> None:
        start = self.arch_kconfig.index("config P2_RNG_BLAKE2S")
        end = self.arch_kconfig.index("\nconfig ", start + 1)
        block = self.arch_kconfig[start:end]
        self.assertIn("depends on CRYPTO", block)
        self.assertIn("depends on DEV_RANDOM || DEV_URANDOM_ARCH", block)
        self.assertNotIn("select CRYPTO", block)

    def test_rng_and_conditioner_remain_resident_boot_safe_code(self) -> None:
        self.assertNotIn("libcrypto", self.arch_makefile)
        self.assertNotRegex(
            self.make_defs,
            r"p2_rng\$\(OBJEXT\):.*(?:OVERLAY|EXTERNALIZE)",
        )

    def test_driver_has_only_the_hardware_getrnd_source(self) -> None:
        getrnd = re.search(
            r"static uint32_t p2_getrnd\(void\)\s*\{(?P<body>.*?)\n\}",
            self.source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(getrnd)
        body = getrnd.group("body")
        self.assertIn('__asm__ __volatile__("getrnd %0" : "=r" (value));', body)
        self.assertEqual(body.count("getrnd"), 1)

        # A fixed seed, software PRNG, timer, or libc rand fallback would make
        # successful reads look healthy while silently losing boot entropy.
        fallback_patterns = (
            r"\bxorshift\b",
            r"\bcongruential\b",
            r"\b(?:s?rand|nrand)\s*\(",
            r"\b(?:clock|gettimeofday|time)\s*\(",
            r"\brandom_state\b",
            r"\bfallback\b",
        )
        for pattern in fallback_patterns:
            with self.subTest(pattern=pattern):
                self.assertNotRegex(self.source, pattern)

    def test_read_handles_unaligned_and_tagged_destinations(self) -> None:
        read = re.search(
            r"static ssize_t p2_rng_read\(.*?\)\s*\{(?P<body>.*?)\n\}",
            self.source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(read)
        body = read.group("body")
        self.assertIn("while (remaining > 0)", body)
        self.assertIn("uint32_t value = p2_getrnd();", body)
        self.assertIn("remaining < sizeof(value) ? remaining : sizeof(value)", body)
        self.assertIn("memcpy(buffer, &value, chunk);", body)
        self.assertIn("buffer += chunk;", body)
        self.assertIn("remaining -= chunk;", body)
        self.assertIn("return buflen;", body)
        self.assertNotRegex(body, r"\*\s*\(\s*(?:u?int32_t|uint32_t)\s*\*\s*\)\s*buffer")

    def test_conditioner_hashes_sixteen_fresh_words_and_fails_closed(self) -> None:
        start = self.source.index("#ifdef CONFIG_P2_RNG_BLAKE2S")
        end = self.source.index("#else", start)
        conditioned = self.source[start:end]
        for token in (
            "uint32_t raw[16]",
            "uint8_t conditioned[BLAKE2S_OUTBYTES]",
            "index < nitems(raw)",
            "raw[index] = p2_getrnd()",
            "blake2s(conditioned, sizeof(conditioned), raw, sizeof(raw)",
            "memcpy(buffer, conditioned, chunk)",
            "return -EIO",
        ):
            self.assertIn(token, conditioned)
        self.assertLess(
            conditioned.index("raw[index] = p2_getrnd()"),
            conditioned.index("blake2s(conditioned"),
        )
        self.assertLess(
            conditioned.index("blake2s(conditioned"),
            conditioned.index("memcpy(buffer, conditioned, chunk)"),
        )

    def test_python_profile_cannot_select_a_software_urandom_fallback(self) -> None:
        for setting in (
            "CONFIG_CRYPTO=y",
            "CONFIG_DEV_RANDOM=y",
            "CONFIG_DEV_URANDOM=y",
            "CONFIG_DEV_URANDOM_ARCH=y",
            "CONFIG_P2_RNG_BLAKE2S=y",
        ):
            self.assertIn(setting, self.profile)

        for setting in (
            "CONFIG_DEV_URANDOM_XORSHIFT128=y",
            "CONFIG_DEV_URANDOM_CONGRUENTIAL=y",
            "CONFIG_DEV_URANDOM_RANDOM_POOL=y",
        ):
            self.assertNotIn(setting, self.profile)

    def test_python_build_proves_getrnd_survived_the_final_link(self) -> None:
        for setting in (
            "CONFIG_ARCH_HAVE_RNG=y",
            "CONFIG_CRYPTO=y",
            "CONFIG_DEV_RANDOM=y",
            "CONFIG_DEV_URANDOM_ARCH=y",
            "CONFIG_P2_RNG_BLAKE2S=y",
        ):
            self.assertIn(setting, self.build)

        disassembly = self.build.index(
            '"$P2LLVM_ROOT/bin/llvm-objdump" --disassemble-symbols=p2_rng_read'
        )
        failure = self.build.index(
            "ERROR: packaged P2 Python image lacks conditioned hardware GETRND",
            disassembly,
        )
        gate = self.build[disassembly:failure]
        self.assertIn("getrnd[[:space:]]", gate)
        self.assertIn("/blake2s/ { conditioner = 1 }", gate)
        self.assertIn("hardware && conditioner", gate)
        self.assertIn('"$ROOT/nuttx.full"', gate)
        self.assertNotIn("|| true", gate)

    def test_python_hil_exercises_both_os_and_secrets_entropy_apis(self) -> None:
        start = self.hil.index('"hardware_entropy"')
        end = self.hil.index("PythonTest(", start)
        command = self.hil[start:end]
        self.assertIn("os.urandom(256)", command)
        self.assertIn("secrets.token_bytes(256)", command)
        self.assertIn("a!=b", command)
        self.assertIn("any(a)", command)
        self.assertIn("any(b)", command)
        self.assertIn('FINGERPRINT:"+a[:16].hex()', command)
        self.assertIn("P2PYTEST:ENTROPY:PASS", command)


if __name__ == "__main__":
    unittest.main()
