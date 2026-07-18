#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

import pathlib
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).parents[1] / "report-memory.sh"
HUB_LIMIT = 0x7C000


def map_text(
    data_end=0x66000,
    bss_start=0x67000,
    image_end=0x68000,
    stack_start=0x69000,
    stack_end=0x6A000,
    heap_start=0x6A000,
    heap_end=HUB_LIMIT,
    overlay_start=None,
    overlay_end=HUB_LIMIT,
    symbolic_overlay_end=False,
):
    if overlay_start is None:
        overlay_start = heap_end

    symbols = (
        (data_end, "_edata"),
        (bss_start, "_sbss"),
        (image_end, "_ebss"),
        (stack_start, "_sinitialstack"),
        (stack_end, "_einitialstack"),
        (heap_start, "_sheap"),
        (heap_end, "_eheap"),
        (overlay_start, "__p2_overlay_slot_start"),
    )
    lines = ["     VMA      LMA     Size Align Out     In      Symbol"]
    for value, symbol in symbols:
        lines.append(
            f"{value:8x} {value:8x}        0     1 "
            f"        {symbol} = ABSOLUTE ( . )"
        )

    if symbolic_overlay_end:
        lines.append(
            f"{overlay_start:8x} {overlay_start:8x}        0     1 "
            "__p2_overlay_slot_end = P2_HUB_END"
        )
    else:
        lines.append(
            f"{overlay_end:8x} {overlay_end:8x}        0     1 "
            "        __p2_overlay_slot_end = ABSOLUTE ( . )"
        )

    return "\n".join(lines) + "\n"


def system_map_text(
    data_end=0x66000,
    bss_start=0x67000,
    image_end=0x68000,
    stack_start=0x69000,
    stack_end=0x6A000,
    heap_start=0x6A000,
    heap_end=HUB_LIMIT,
    overlay_start=None,
    overlay_end=HUB_LIMIT,
):
    if overlay_start is None:
        overlay_start = heap_end

    symbols = (
        (data_end, "A", "_edata"),
        (bss_start, "A", "_sbss"),
        (image_end, "A", "_ebss"),
        (stack_start, "A", "_sinitialstack"),
        (stack_end, "A", "_einitialstack"),
        (heap_start, "A", "_sheap"),
        (heap_end, "A", "_eheap"),
        (overlay_start, "A", "__p2_overlay_slot_start"),
        (overlay_end, "A", "__p2_overlay_slot_end"),
    )
    return "".join(
        f"{value:08x} {kind} {symbol}\n" for value, kind, symbol in symbols
    )


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
            self.assertIn(
                "P2MEM:HUB_OVERLAY_SLOT=0x0007c000-0x0007c000:BYTES=0",
                result.stdout,
            )
            self.assertIn("P2MEM:RAW_IMAGE=", result.stdout)
            self.assertIn("STAGING_CAPACITY=421888", result.stdout)
            self.assertTrue(
                result.stdout.rstrip().endswith("P2MEM:PASS:STATICALLY-VERIFIED")
            )

    def test_rejects_missing_or_malformed_map(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            missing = self.run_report(root / "missing.map")
            self.assertEqual(missing.returncode, 2)
            self.assertIn("P2MEM:BLOCKED:MAP=MISSING_OR_EMPTY", missing.stderr)

            malformed = root / "bad.map"
            malformed.write_text(
                map_text().replace("_eheap", "_wrong"), encoding="utf-8"
            )
            result = self.run_report(malformed)
            self.assertEqual(result.returncode, 2)
            self.assertIn("P2MEM:BLOCKED:MAP_SYMBOL=_eheap", result.stderr)

    def test_rejects_layout_and_image_overflow(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            invalid = root / "invalid.map"
            invalid.write_text(
                map_text(stack_start=0x6A000, stack_end=0x69000), encoding="utf-8"
            )
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

    def test_rejects_raw_image_that_reaches_bss_or_later_runtime_regions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            memory_map = root / "nuttx.map"
            raw = root / "overlaps-bss.bin"
            memory_map.write_text(map_text(), encoding="utf-8")
            raw.write_bytes(b"x" * (0x67000 + 1))

            result = self.run_report(memory_map, raw)
            self.assertEqual(result.returncode, 1)
            self.assertIn("P2MEM:FAIL:RAW_IMAGE_OVERLAPS_RUNTIME", result.stderr)
            self.assertIn("BSS_START=421888", result.stderr)
            self.assertIn("STACK_START=430080", result.stderr)
            self.assertIn("OVERLAY_START=507904", result.stderr)

    def test_symbolic_lld_assignment_requires_exact_system_map_symbol(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            memory_map = root / "nuttx.map"
            symbols = root / "System.map"
            memory_map.write_text(
                map_text(
                    data_end=0x48000,
                    bss_start=0x48800,
                    image_end=0x49000,
                    stack_start=0x4A000,
                    stack_end=0x4B000,
                    heap_start=0x4B000,
                    heap_end=0x5C000,
                    overlay_start=0x5C000,
                    symbolic_overlay_end=True,
                ),
                encoding="utf-8",
            )

            blocked = self.run_report(memory_map)
            self.assertEqual(blocked.returncode, 2)
            self.assertIn(
                "MAP_SYMBOL=__p2_overlay_slot_end:MISSING_OR_INVALID",
                blocked.stderr,
            )

            symbols.write_text(
                system_map_text(
                    data_end=0x48000,
                    bss_start=0x48800,
                    image_end=0x49000,
                    stack_start=0x4A000,
                    stack_end=0x4B000,
                    heap_start=0x4B000,
                    heap_end=0x5C000,
                    overlay_start=0x5C000,
                ),
                encoding="utf-8",
            )
            result = self.run_report(memory_map, "", symbols)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"P2MEM:SYSTEM_MAP={symbols}", result.stdout)
            self.assertIn(
                "P2MEM:HUB_OVERLAY_SLOT=0x0005c000-0x0007c000:BYTES=131072",
                result.stdout,
            )

    def test_reports_reserved_overlay_slot(self):
        with tempfile.TemporaryDirectory() as temporary:
            memory_map = pathlib.Path(temporary) / "overlay.map"
            memory_map.write_text(
                map_text(
                    data_end=0x46000,
                    bss_start=0x47000,
                    image_end=0x48000,
                    stack_start=0x49000,
                    stack_end=0x4A000,
                    heap_start=0x4A000,
                    heap_end=0x5C000,
                    overlay_start=0x5C000,
                ),
                encoding="utf-8",
            )

            result = self.run_report(memory_map)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "P2MEM:HUB_OVERLAY_SLOT=0x0005c000-0x0007c000:BYTES=131072",
                result.stdout,
            )


if __name__ == "__main__":
    unittest.main()
