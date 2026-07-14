#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

import pathlib
import subprocess
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).parents[1] / "report-memory.sh"
HUB_LIMIT = 0x7C000


def map_text(
    image_end=0x68000,
    stack_start=0x69000,
    stack_end=0x6A000,
    heap_start=0x6A000,
    heap_end=HUB_LIMIT,
):
    symbols = (
        (image_end, "_ebss"),
        (stack_start, "_sinitialstack"),
        (stack_end, "_einitialstack"),
        (heap_start, "_sheap"),
        (heap_end, "_eheap"),
    )
    lines = ["     VMA      LMA     Size Align Out     In      Symbol"]
    for value, symbol in symbols:
        lines.append(
            f"{value:8x} {value:8x}        0     1 "
            f"        {symbol} = ABSOLUTE ( . )"
        )
    return "\n".join(lines) + "\n"


class ReportMemoryTests(unittest.TestCase):
    def run_report(self, *arguments):
        return subprocess.run(
            [str(SCRIPT), *(str(argument) for argument in arguments)],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_reports_map_and_optional_raw_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            memory_map = root / "nuttx.map"
            raw_image = root / "nuttx.bin"
            memory_map.write_text(map_text(), encoding="utf-8")
            raw_image.write_bytes(b"x" * 0x5000)

            result = self.run_report(memory_map, raw_image)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("P2MEM:LINKED_IMAGE_END=0x00068000", result.stdout)
            self.assertIn("P2MEM:HEAP=0x0006a000-0x0007c000:BYTES=73728", result.stdout)
            self.assertIn("P2MEM:RAW_IMAGE=", result.stdout)
            self.assertTrue(result.stdout.rstrip().endswith("P2MEM:PASS:STATICALLY-VERIFIED"))

    def test_rejects_missing_or_malformed_map(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            missing = self.run_report(root / "missing.map")
            self.assertEqual(missing.returncode, 2)
            self.assertIn("P2MEM:BLOCKED:MAP=MISSING_OR_EMPTY", missing.stderr)

            malformed = root / "bad.map"
            malformed.write_text(map_text().replace("_eheap", "_wrong"), encoding="utf-8")
            result = self.run_report(malformed)
            self.assertEqual(result.returncode, 2)
            self.assertIn("P2MEM:BLOCKED:MAP_SYMBOL=_eheap", result.stderr)

    def test_rejects_layout_and_image_overflow(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            invalid = root / "invalid.map"
            invalid.write_text(map_text(stack_start=0x6A000, stack_end=0x69000), encoding="utf-8")
            result = self.run_report(invalid)
            self.assertEqual(result.returncode, 2)
            self.assertIn("P2MEM:BLOCKED:MAP_LAYOUT=INVALID", result.stderr)

            valid = root / "valid.map"
            raw = root / "oversize.bin"
            valid.write_text(map_text(), encoding="utf-8")
            raw.write_bytes(b"x" * (HUB_LIMIT + 1))
            result = self.run_report(valid, raw)
            self.assertEqual(result.returncode, 1)
            self.assertIn("P2MEM:FAIL:RAW_IMAGE_OVERFLOW", result.stderr)


if __name__ == "__main__":
    unittest.main()
