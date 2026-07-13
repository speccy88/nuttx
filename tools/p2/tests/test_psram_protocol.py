#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/p2"))

from psram_protocol import (
    command_bytes,
    expected_fnv1a,
    normalize_sequence,
    parse_psram,
    pattern_byte,
)


SEQUENCE = "A55A0713"
CHECKSUM = "634C9DC5"


def progress_line(direction, value, sequence=SEQUENCE):
    return "P2PSRAM:PROGRESS:SEQUENCE={}:{}={}".format(
        sequence, direction, value
    )


def complete_log():
    lines = [
        f"P2PSRAM:BEGIN:SEQUENCE={SEQUENCE}",
        "P2PSRAM:GEOMETRY:SIZE=33554432:CHIPS=4:CHIP_SIZE=8388608:"
        "WORD=4:MAX_REQUEST=65536:COG=2",
        "P2PSRAM:PROFILE:MAX_REQUEST=65536:QPI_HZ=5000000:"
        "TICK_USEC=10000:TIMEOUT_TICKS=500:CANCEL_GRACE_TICKS=100",
        "P2PSRAM:WALKING:PASS:BITS=32",
        "P2PSRAM:ADDRESS:PASS:LINES=23",
        "P2PSRAM:BOUNDARY:PASS:COUNT=5",
        "P2PSRAM:RANDOM:PASS:COUNT=1024",
    ]
    for value in range(4 * 1024 * 1024, 32 * 1024 * 1024 + 1, 4 * 1024 * 1024):
        lines.append(progress_line("WRITE", value))
    for value in range(4 * 1024 * 1024, 32 * 1024 * 1024 + 1, 4 * 1024 * 1024):
        lines.append(progress_line("READ", value))
    lines.extend(
        (
            "P2PSRAM:FULL:PASS:BYTES=33554432:FNV1A={}".format(CHECKSUM),
            "P2PSRAM:THROUGHPUT:WRITE_BPS=900000:READ_BPS=1100000",
            "P2PSRAM:CONCURRENT:PASS:WORK=32768:ELAPSED_TICKS=4:"
            "CPU_AVAILABLE_PERMILLE=930:CPU_OCCUPANCY_PERMILLE=70",
            "P2PSRAM:TIMEOUT:PASS:RESULT=110:BYTES=32768:"
            "DEADLINE_TICKS=1:MIN_WIRE_USEC=26214:TICK_USEC=10000",
            "P2PSRAM:RECOVERY:PASS",
            "P2PSRAM:CE_TIMING:PASS:MAX_CYCLES=711:LIMIT_CYCLES=1440",
            f"P2PSRAM:PASS:SEQUENCE={SEQUENCE}",
        )
    )
    return "\r\n".join(lines) + "\r\n"


class PsramProtocolTests(unittest.TestCase):
    def test_complete_transcript(self):
        result = parse_psram(complete_log(), SEQUENCE)
        self.assertTrue(result["complete"], result["errors"])
        self.assertEqual(result["values"]["full_bytes"], 32 * 1024 * 1024)
        self.assertEqual(result["values"]["fnv1a"], int(CHECKSUM, 16))
        self.assertEqual(result["values"]["max_ce_cycles"], 711)

    def test_missing_progress_is_rejected(self):
        text = complete_log().replace(
            progress_line("READ", 32 * 1024 * 1024) + "\r\n", ""
        )
        result = parse_psram(text, SEQUENCE)
        self.assertFalse(result["complete"])
        self.assertTrue(any("read progress" in error for error in result["errors"]))

    def test_progress_before_begin_is_rejected(self):
        lines = complete_log().splitlines()
        progress = [line for line in lines if line.startswith("P2PSRAM:PROGRESS:")]
        current = [line for line in lines if not line.startswith("P2PSRAM:PROGRESS:")]
        result = parse_psram("\r\n".join(progress + current) + "\r\n", SEQUENCE)
        self.assertFalse(result["complete"])
        self.assertTrue(any("progress" in error for error in result["errors"]))

    def test_progress_after_final_pass_is_rejected(self):
        lines = complete_log().splitlines()
        progress = [line for line in lines if line.startswith("P2PSRAM:PROGRESS:")]
        current = [line for line in lines if not line.startswith("P2PSRAM:PROGRESS:")]
        result = parse_psram("\r\n".join(current + progress) + "\r\n", SEQUENCE)
        self.assertFalse(result["complete"])
        self.assertTrue(any("progress" in error for error in result["errors"]))

    def test_interleaved_progress_is_rejected(self):
        last_write = progress_line("WRITE", 32 * 1024 * 1024)
        first_read = progress_line("READ", 4 * 1024 * 1024)
        text = complete_log().replace(
            last_write + "\r\n" + first_read,
            first_read + "\r\n" + last_write,
        )
        result = parse_psram(text, SEQUENCE)
        self.assertFalse(result["complete"])
        self.assertTrue(any("out of order" in error for error in result["errors"]))

    def test_embedded_or_wrong_nonce_progress_is_rejected(self):
        expected = progress_line("WRITE", 4 * 1024 * 1024)
        replacements = (
            "prefix" + expected,
            expected.replace(SEQUENCE, "FFFFFFFF"),
        )
        for replacement in replacements:
            with self.subTest(replacement=replacement):
                text = complete_log().replace(expected, replacement)
                result = parse_psram(text, SEQUENCE)
                self.assertFalse(result["complete"])
                self.assertTrue(any("progress" in error for error in result["errors"]))

    def test_profile_checksum_and_concurrency_drift_are_rejected(self):
        replacements = (
            ("MAX_REQUEST=65536:COG=2", "MAX_REQUEST=32768:COG=2"),
            ("QPI_HZ=5000000", "QPI_HZ=4000000"),
            ("FNV1A=" + CHECKSUM, "FNV1A=00000000"),
            (
                "CPU_AVAILABLE_PERMILLE=930:CPU_OCCUPANCY_PERMILLE=70",
                "CPU_AVAILABLE_PERMILLE=0:CPU_OCCUPANCY_PERMILLE=1000",
            ),
            ("MIN_WIRE_USEC=26214", "MIN_WIRE_USEC=9999"),
        )
        for original, replacement in replacements:
            with self.subTest(replacement=replacement):
                result = parse_psram(
                    complete_log().replace(original, replacement), SEQUENCE
                )
                self.assertFalse(result["complete"])

    def test_nonce_specific_pattern_and_full_fnv_vector(self):
        vectors = {
            0x00000000: 0x13,
            0x00000001: 0x2C,
            0x00000002: 0xA4,
            0x00000003: 0x14,
            0x000000FF: 0x80,
            0x00000100: 0x24,
            0x0000FFFF: 0x6F,
            0x00010000: 0x14,
            0x00FFFFFF: 0x6E,
            0x01000000: 0x6E,
            0x01FFFFFF: 0xC9,
        }
        for address, expected in vectors.items():
            with self.subTest(address=address):
                self.assertEqual(pattern_byte(SEQUENCE, address), expected)
        self.assertEqual(expected_fnv1a(SEQUENCE), int(CHECKSUM, 16))

    def test_duplicate_final_pass_is_rejected(self):
        text = complete_log() + f"P2PSRAM:PASS:SEQUENCE={SEQUENCE}\r\n"
        result = parse_psram(text, SEQUENCE)
        self.assertFalse(result["complete"])
        self.assertTrue(any("exactly one" in error for error in result["errors"]))

    def test_truncated_final_line_is_rejected(self):
        result = parse_psram(complete_log().rstrip("\r\n"), SEQUENCE)
        self.assertFalse(result["complete"])
        self.assertTrue(any("final pass" in error for error in result["errors"]))

    def test_failure_marker_is_rejected(self):
        text = complete_log().replace(
            "P2PSRAM:RECOVERY:PASS",
            "P2PSRAM:FAIL:TIMEOUT:110\r\nP2PSRAM:RECOVERY:PASS",
        )
        result = parse_psram(text, SEQUENCE)
        self.assertFalse(result["complete"])
        self.assertTrue(any("failure" in error for error in result["errors"]))

    def test_nonce_is_exact_and_command_is_single_line(self):
        self.assertEqual(normalize_sequence(SEQUENCE), SEQUENCE)
        self.assertEqual(command_bytes(SEQUENCE), b"p2psram A55A0713\r")
        for invalid in ("a55a0713", "A55A713", "GGGGGGGG", ""):
            with self.assertRaises(ValueError):
                normalize_sequence(invalid)


if __name__ == "__main__":
    unittest.main()
