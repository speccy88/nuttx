#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

import pathlib
import subprocess
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).parents[1] / "report-memory.sh"
HUB_LIMIT = 0x7C000


def map_text(
    image_end: int = 0x68000,
    stack_start: int = 0x69000,
    stack_end: int = 0x6A000,
    heap_start: int = 0x6A000,
    heap_end: int = HUB_LIMIT,
) -> str:
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
    def run_report(self, *arguments: pathlib.Path) -> subprocess.CompletedProcess[str]:
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

            without_raw = self.run_report(memory_map)
            self.assertEqual(without_raw.returncode, 0, without_raw.stderr)
            self.assertIn(
                "P2MEM:LINKED_IMAGE_END=0x00068000:BYTES=425984",
                without_raw.stdout,
            )
            self.assertIn(
                "P2MEM:INITIAL_STACK=0x00069000-0x0006a000:BYTES=4096",
                without_raw.stdout,
            )
            self.assertIn(
                "P2MEM:HEAP=0x0006a000-0x0007c000:BYTES=73728:"
                "HEADROOM_TO_0X0007C000=73728",
                without_raw.stdout,
            )
            self.assertNotIn("P2MEM:RAW_IMAGE=", without_raw.stdout)
            self.assertTrue(
                without_raw.stdout.rstrip().endswith(
                    "P2MEM:PASS:STATICALLY-VERIFIED"
                )
            )

            with_raw = self.run_report(memory_map, raw_image)
            self.assertEqual(with_raw.returncode, 0, with_raw.stderr)
            self.assertIn(
                f"P2MEM:RAW_IMAGE={raw_image}:BYTES=20480:"
                "STAGING_CAPACITY=507904:STAGING_REMAINING=487424",
                with_raw.stdout,
            )

    def test_rejects_missing_or_malformed_map(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            missing = self.run_report(root / "missing.map")
            self.assertEqual(missing.returncode, 2)
            self.assertIn("P2MEM:BLOCKED:MAP=MISSING_OR_EMPTY", missing.stderr)

            malformed = root / "malformed.map"
            malformed.write_text(
                map_text().replace("_einitialstack", "_wrong_symbol"),
                encoding="utf-8",
            )
            result = self.run_report(malformed)
            self.assertEqual(result.returncode, 2)
            self.assertIn(
                "P2MEM:BLOCKED:MAP_SYMBOL=_einitialstack:MISSING_OR_INVALID",
                result.stderr,
            )

    def test_rejects_map_and_raw_image_overflow(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            memory_map = root / "overflow.map"
            memory_map.write_text(
                map_text(
                    image_end=HUB_LIMIT + 1,
                    stack_start=HUB_LIMIT + 2,
                    stack_end=HUB_LIMIT + 3,
                    heap_start=HUB_LIMIT + 3,
                    heap_end=HUB_LIMIT + 4,
                ),
                encoding="utf-8",
            )
            result = self.run_report(memory_map)
            self.assertEqual(result.returncode, 1)
            self.assertIn("P2MEM:FAIL:LINKED_IMAGE_END_OVERFLOW", result.stderr)

            valid_map = root / "nuttx.map"
            raw_image = root / "oversize.bin"
            valid_map.write_text(map_text(), encoding="utf-8")
            raw_image.write_bytes(b"x" * (HUB_LIMIT + 1))
            result = self.run_report(valid_map, raw_image)
            self.assertEqual(result.returncode, 1)
            self.assertIn("P2MEM:FAIL:RAW_IMAGE_OVERFLOW", result.stderr)

    def test_rejects_invalid_ranges_and_missing_arguments(self):
        no_arguments = self.run_report()
        self.assertEqual(no_arguments.returncode, 2)
        self.assertIn("usage:", no_arguments.stderr)

        with tempfile.TemporaryDirectory() as temporary:
            memory_map = pathlib.Path(temporary) / "invalid.map"
            memory_map.write_text(
                map_text(stack_start=0x6A000, stack_end=0x69000),
                encoding="utf-8",
            )
            result = self.run_report(memory_map)
            self.assertEqual(result.returncode, 2)
            self.assertIn("P2MEM:BLOCKED:MAP_LAYOUT=INVALID", result.stderr)


if __name__ == "__main__":
    unittest.main()
