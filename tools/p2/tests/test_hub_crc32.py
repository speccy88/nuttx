#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

"""Differential and object-code tests for the resident P2 Hub CRC-32 path."""

from __future__ import annotations

import os
import pathlib
import random
import re
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
ASSEMBLY = ROOT / "arch/p2/src/common/p2_hub_crc32.S"
HEADER = ROOT / "arch/p2/include/hub_crc32.h"
COMMON_DEFS = ROOT / "arch/p2/src/common/Make.defs"
BOARD = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_python.c"
CONTAINER = ROOT / "arch/p2/src/common/p2_python_container.c"
OVERLAY = ROOT / "arch/p2/src/common/p2_overlay.c"
RESIDENCY = ROOT / "tools/p2/verify-python-residency.py"
POLYNOMIAL = 0xEDB88320
MASK = 0xFFFFFFFF


def bitwise_update(crc: int, data: bytes) -> int:
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (POLYNOMIAL if crc & 1 else 0)
    return crc & MASK


def crcbit(value: int, input_bit: int) -> int:
    value = (value >> 1) ^ (
        POLYNOMIAL if (value ^ input_bit) & 1 else 0
    )
    return value & MASK


def crcnib(value: int, nibble: int) -> int:
    # CRCNIB consumes Q[31] first and shifts Q left after each CRCBIT.

    for bit in (3, 2, 1, 0):
        value = crcbit(value, (nibble >> bit) & 1)
    return value


def hardware_update(crc: int, data: bytes) -> int:
    """Model XOR byte plus two zero-Q CRCNIB instructions."""

    for byte in data:
        crc ^= byte
        crc = crcnib(crc, 0)
        crc = crcnib(crc, 0)
    return crc & MASK


def final_crc(update, data: bytes) -> int:
    return update(MASK, data) ^ MASK


class HubCrc32Tests(unittest.TestCase):
    def test_known_vectors_and_all_byte_values(self) -> None:
        vectors = (
            b"",
            b"123456789",
            bytes(range(256)),
            bytes(range(255, -1, -1)),
            bytes([0x00]) * 1024,
            bytes([0xFF]) * 1024,
        )
        for vector in vectors:
            with self.subTest(size=len(vector), prefix=vector[:9]):
                self.assertEqual(
                    hardware_update(MASK, vector), bitwise_update(MASK, vector)
                )

        self.assertEqual(final_crc(hardware_update, b""), 0)
        self.assertEqual(final_crc(hardware_update, b"123456789"), 0xCBF43926)

    def test_differential_unaligned_and_incremental_splits(self) -> None:
        random_source = random.Random(0x50324352)
        data = bytes(random_source.randrange(256) for _ in range(512))
        unaligned = bytes([0xA5]) + data + bytes([0x5A])
        view = unaligned[1:-1]
        expected = hardware_update(0x13579BDF, view)
        self.assertEqual(expected, bitwise_update(0x13579BDF, view))

        for split in range(len(view) + 1):
            crc = hardware_update(0x13579BDF, view[:split])
            crc = hardware_update(crc, view[split:])
            self.assertEqual(crc, expected, f"split={split}")

        for chunk_size in (1, 2, 3, 7, 16, 63, 128, 511, 1000):
            crc = 0x13579BDF
            for offset in range(0, len(view), chunk_size):
                crc = hardware_update(crc, view[offset : offset + chunk_size])
            self.assertEqual(crc, expected, f"chunk={chunk_size}")

    def test_single_bit_corruption_fails_closed(self) -> None:
        payload = b"P2 Python container and overlay integrity" + bytes(range(256))
        expected = final_crc(hardware_update, payload)
        for byte_index in range(len(payload)):
            for bit in range(8):
                corrupt = bytearray(payload)
                corrupt[byte_index] ^= 1 << bit
                self.assertNotEqual(
                    final_crc(hardware_update, bytes(corrupt)), expected
                )

    def test_leaf_assembly_is_resident_reentrant_and_direct_hub_io(self) -> None:
        source = ASSEMBLY.read_text()
        header = HEADER.read_text()
        defs = COMMON_DEFS.read_text()
        residency = RESIDENCY.read_text()

        self.assertIn("CMN_ASRCS += p2_context.S p2_hub_crc32.S", defs)
        self.assertIn("uint32_t p2_hub_crc32_update", header)
        self.assertIn(".text", source)
        self.assertNotRegex(source, r"\.(?:data|bss)\b")
        self.assertNotRegex(source, r"\bcall[ab]?\b")
        self.assertNotIn("__p2_xmem", source)
        self.assertIn("wrlong  r3, ptra++", source)
        self.assertIn("rdlong  r3, --ptra", source)
        self.assertIn("rdbyte  r3, pa", source)
        self.assertEqual(len(re.findall(r"(?m)^\s*setq\s+#0\s*$", source)), 2)
        self.assertEqual(len(re.findall(r"(?m)^\s*crcnib\s+", source)), 2)
        self.assertIn("0xedb88320", source.lower())
        self.assertIn("CRCNIB consumes Q[31:28]", source)
        self.assertIn("shields its companion instruction", source)
        self.assertIn(
            'Requirement("overlay", "p2_hub_crc32_update", '
            '("p2_hub_crc32_update",))',
            residency,
        )

    def test_all_target_crc_paths_keep_their_integrity_comparisons(self) -> None:
        board = BOARD.read_text()
        container = CONTAINER.read_text()
        overlay = OVERLAY.read_text()

        self.assertIn("return p2_hub_crc32_update(crc, data, size);", board)
        self.assertGreaterEqual(board.count("p2_python_crc32_update("), 3)
        self.assertIn("calculated_crc != frame_crc", board)
        self.assertIn(
            "return (crc ^ UINT32_C(0xffffffff)) == expected_crc ? 0 : -EBADMSG;",
            board,
        )

        self.assertIn("#ifdef CONFIG_ARCH_P2", container)
        self.assertIn("return p2_hub_crc32_update(crc, data, size);", container)
        self.assertIn("crc = p2_container_crc32_update(crc, buffer, chunk);", container)
        self.assertIn("P2_CONTAINER_CRC_POLYNOMIAL", container)
        self.assertRegex(
            container,
            r"p2_container_crc32\([\s\S]*?checksum\s*!=\s*section\.crc32",
        )

        self.assertIn("p2_hub_crc32_update(UINT32_C(0xffffffff)", overlay)
        self.assertRegex(
            overlay,
            r"p2_overlay_crc32\([\s\S]*?!=\s*descriptor->image_crc32"
            r"\)[\s\S]*?return -EILSEQ;",
        )

    @unittest.skipUnless(
        os.environ.get("P2LLVM_ROOT"), "set P2LLVM_ROOT for P2 object checks"
    )
    def test_p2_object_contains_direct_rdbyte_and_two_crcnib_ops(self) -> None:
        toolchain = pathlib.Path(os.environ["P2LLVM_ROOT"])
        clang = toolchain / "bin/clang"
        objdump = toolchain / "bin/llvm-objdump"
        if not clang.is_file() or not objdump.is_file():
            self.skipTest("P2 clang and llvm-objdump are required")

        with tempfile.TemporaryDirectory() as directory:
            obj = pathlib.Path(directory) / "p2_hub_crc32.o"
            subprocess.run(
                [str(clang), "--target=p2", "-c", str(ASSEMBLY), "-o", str(obj)],
                check=True,
                capture_output=True,
                text=True,
            )
            disassembly = subprocess.run(
                [str(objdump), "-dr", str(obj)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout

        body = disassembly.split("<p2_hub_crc32_update>:", 1)[1]
        self.assertIn("rdbyte r3, pa", body)
        self.assertEqual(body.count("crcnib r31, r30"), 2)
        self.assertNotIn("R_P2_20", body)
        self.assertNotIn("__p2_xmem", body)


if __name__ == "__main__":
    unittest.main()
