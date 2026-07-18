# SPDX-License-Identifier: Apache-2.0

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

import sd_benchmark_protocol as benchmark

SEQUENCE = "1234ABCD"


def complete_log(
    sequence=SEQUENCE,
    byte_count=benchmark.DEFAULT_BYTES,
    passes=benchmark.DEFAULT_PASSES,
    usecs=None,
):
    sysclk_hz = 360_000_000
    if usecs is None:
        usecs = [6_400_000 - index * 200_000 for index in range(passes)]
    cycles = [usec * (sysclk_hz // 1_000_000) for usec in usecs]
    rates = [byte_count * sysclk_hz // count for count in cycles]
    lines = [
        "NuttShell (NSH) NuttX-12.0",
        (
            "P2SDBENCH:BEGIN:VERSION=2:MODE=RAW:OP=READ:SEQ={}:"
            "DEV=/dev/mmcsd0:BYTES={}:PASSES={}:THRESHOLD_BPS=41000000"
        ).format(sequence, byte_count, passes),
        (
            "P2SDBENCH:CONFIG:SYSCLK_HZ=360000000:BUS=SDIO4:"
            "BUS_WIDTH_BITS=4:REQUESTED_BUS_CLOCK_HZ=120000000:"
            "BUS_CLOCK_HZ=120000000:ACTIVE_DIVISOR=3:"
            "RAW_CEILING_BPS=60000000:HIGH_SPEED=1:OVERCLOCKED=1:"
            "PHASE_CALIBRATED=1:RX_MODE=ASYNC:RX_LAG=0:"
            "PAYLOAD_CRC16=UNCHECKED:CMD_CRC7=CHECKED:HIL_REQUIRED=1:"
            "BUFFER_BYTES=65536:DRIVER=P2-SDIO-STREAMER:BUILD=1111111111"
        ),
        (
            "P2SDBENCH:GEOMETRY:PHASE=BEFORE:SECTORS=62333952:"
            "SECTOR_SIZE=512:MEDIA_CHANGED=0"
        ),
        (
            "P2SDBENCH:BASELINE:MODE=RAW:OP=READ:VERIFY=CRC16:"
            "BYTES={}:FNV1A=89ABCDEF:SEQ={}"
        ).format(byte_count, sequence),
        (
            "P2SDBENCH:TIMER:SOURCE=P2_GETCT:FREQUENCY_HZ=360000000:"
            "RESOLUTION_CYCLES=1:"
            "SCOPE=READ_CALLS:VERIFY=HASH_TIMED_BYTES"
        ),
    ]
    for index, (usec, count, bps) in enumerate(zip(usecs, cycles, rates), 1):
        lines.append(
            (
                "P2SDBENCH:RESULT:MODE=RAW:OP=READ:PASS={}:BYTES={}:"
                "CYCLES={}:USEC={}:BPS={}:FNV1A=89ABCDEF:SEQ={}"
            ).format(index, byte_count, count, usec, bps, sequence)
        )
    lines.extend(
        (
            (
                "P2SDBENCH:GEOMETRY:PHASE=AFTER:SECTORS=62333952:"
                "SECTOR_SIZE=512:MEDIA_CHANGED=0"
            ),
            (
                "P2SDBENCH:PASS:MODE=RAW:OP=READ:SEQ={}:PASSES={}:"
                "BYTES={}:MIN_BPS={}:MEDIAN_BPS={}:MAX_BPS={}:"
                "THRESHOLD_BPS=41000000"
            ).format(
                sequence,
                passes,
                byte_count,
                min(rates),
                sorted(rates)[len(rates) // 2],
                max(rates),
            ),
            "P2SDBENCH:DONE:SEQ={}".format(sequence),
            "nsh>",
        )
    )
    return "\r\n".join(lines) + "\r\n"


class SdBenchmarkProtocolTests(unittest.TestCase):
    def test_command_is_exact_non_destructive_read_only_protocol(self):
        self.assertEqual(
            benchmark.command_line(SEQUENCE, 256 * 1024 * 1024, 7),
            "p2storage sd-benchmark-read 1234ABCD 268435456 7",
        )
        self.assertEqual(
            benchmark.command_bytes(SEQUENCE, 256 * 1024 * 1024, 7),
            b"p2storage sd-benchmark-read 1234ABCD 268435456 7\r",
        )
        self.assertNotIn(
            "ACCEPT-DATA-LOSS",
            benchmark.command_line(SEQUENCE, 256 * 1024 * 1024, 7),
        )

    def test_parameters_reject_short_unaligned_even_or_stale_values(self):
        for byte_count, passes in (
            (benchmark.MIN_BYTES - 512, 7),
            (benchmark.MAX_BYTES + 512, 7),
            (benchmark.MIN_BYTES + 1, 7),
            (benchmark.MIN_BYTES, 2),
            (benchmark.MIN_BYTES, 4),
            (benchmark.MIN_BYTES, 33),
        ):
            with self.subTest(byte_count=byte_count, passes=passes):
                with self.assertRaises(ValueError):
                    benchmark.validate_parameters(byte_count, passes)
        for sequence in ("1234abcd", "1234ABC", "0x1234ABCD", -1):
            with self.subTest(sequence=sequence):
                with self.assertRaises(ValueError):
                    benchmark.normalize_sequence(sequence)

    def test_complete_log_proves_every_pass_and_reports_decimal_and_binary_rates(self):
        result = benchmark.parse_benchmark(complete_log(), SEQUENCE)

        self.assertTrue(result["complete"], result)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(result["measurements"]), 7)
        self.assertGreater(result["aggregates"]["min_bps"], 41_000_000)
        self.assertEqual(result["threshold_mb_per_s"], 41.0)
        self.assertAlmostEqual(result["threshold_mib_per_s"], 39.10064697265625)
        self.assertEqual(result["data_fnv1a"], "89ABCDEF")
        self.assertEqual(result["baseline"]["fnv1a"], "89ABCDEF")
        self.assertEqual(result["baseline"]["verification"], "CRC16")
        self.assertEqual(result["config"]["bus"], "SDIO4")
        self.assertTrue(result["geometry"]["stable"])
        self.assertEqual(result["timer"]["source"], "P2_GETCT")
        self.assertEqual(result["timer"]["frequency_hz"], 360_000_000)
        self.assertEqual(result["timer"]["scope"], "READ_CALLS")
        self.assertEqual(result["timer"]["verification"], "HASH_TIMED_BYTES")

    def test_exactly_forty_one_decimal_megabytes_per_second_is_not_a_pass(self):
        byte_count = 41 * 512 * 4000
        exact_usec = byte_count // 41
        text = complete_log(
            byte_count=byte_count,
            usecs=[exact_usec] + [1_500_000] * 6,
        )
        result = benchmark.parse_benchmark(text, SEQUENCE, byte_count, 7)

        self.assertFalse(result["complete"])
        self.assertEqual(result["measurements"][0]["bps"], 41_000_000)
        self.assertIn("did not strictly exceed", " ".join(result["errors"]))

    def test_record_requires_native_hil_and_requested_clock_consistency(self):
        cases = (
            (
                "bus",
                complete_log().replace("BUS=SDIO4", "BUS=SPI1", 1),
                "record bus is not native SDIO4",
            ),
            (
                "hil",
                complete_log().replace("HIL_REQUIRED=1", "HIL_REQUIRED=0", 1),
                "native HIL_REQUIRED telemetry is inconsistent",
            ),
            (
                "requested-clock",
                complete_log().replace(
                    "REQUESTED_BUS_CLOCK_HZ=120000000",
                    "REQUESTED_BUS_CLOCK_HZ=119999999",
                    1,
                ),
                "active bus clock exceeds requested bus clock",
            ),
        )
        for name, text, expected in cases:
            with self.subTest(name=name):
                result = benchmark.parse_benchmark(text, SEQUENCE)
                self.assertFalse(result["complete"], result)
                self.assertIn(expected, result["errors"])

        hil_errors = [
            error
            for error in benchmark.parse_benchmark(
                complete_log().replace("HIL_REQUIRED=1", "HIL_REQUIRED=0", 1),
                SEQUENCE,
            )["errors"]
            if "HIL_REQUIRED" in error
        ]
        self.assertEqual(
            hil_errors,
            ["native HIL_REQUIRED telemetry is inconsistent"],
        )

    def test_target_cannot_lie_about_rate_or_aggregates(self):
        text = complete_log()
        first = benchmark.DEFAULT_BYTES * 360_000_000 // (6_400_000 * 360)
        lied_rate = text.replace(
            "USEC=6400000:BPS={}".format(first),
            "USEC=6400000:BPS={}".format(first + 1),
        )
        result = benchmark.parse_benchmark(lied_rate, SEQUENCE)
        self.assertFalse(result["complete"])
        self.assertIn("recomputed", " ".join(result["errors"]))

        minimum = min(
            benchmark.DEFAULT_BYTES * 360_000_000 // (usec * 360)
            for usec in [6_400_000 - index * 200_000 for index in range(7)]
        )
        lied_aggregate = text.replace(
            "MIN_BPS={}".format(minimum), "MIN_BPS={}".format(minimum + 1)
        )
        result = benchmark.parse_benchmark(lied_aggregate, SEQUENCE)
        self.assertFalse(result["complete"])
        self.assertIn("PASS min_bps", " ".join(result["errors"]))

    def test_integrity_geometry_and_sequence_drift_are_rejected(self):
        cases = (
            (
                "hash",
                complete_log()
                .replace(
                    "FNV1A=89ABCDEF:SEQ=1234ABCD",
                    "FNV1A=89ABCDEF:SEQ=1234ABCD",
                    1,
                )
                .replace(
                    "FNV1A=89ABCDEF:SEQ=1234ABCD",
                    "FNV1A=01234567:SEQ=1234ABCD",
                    1,
                ),
                "CRC16 baseline",
            ),
            (
                "geometry",
                complete_log().replace(
                    "PHASE=AFTER:SECTORS=62333952",
                    "PHASE=AFTER:SECTORS=62333951",
                ),
                "geometry changed",
            ),
            (
                "sequence",
                complete_log().replace(
                    "FNV1A=89ABCDEF:SEQ=1234ABCD",
                    "FNV1A=89ABCDEF:SEQ=1234ABCE",
                    1,
                ),
                "stale sequence",
            ),
        )
        for name, text, expected in cases:
            with self.subTest(name=name):
                result = benchmark.parse_benchmark(text, SEQUENCE)
                self.assertFalse(result["complete"])
                self.assertIn(expected, " ".join(result["errors"]))

    def test_missing_duplicate_out_of_order_failure_and_protocol_drift_fail(self):
        good = complete_log()
        first_result = next(line for line in good.splitlines() if ":RESULT:" in line)
        config_line = next(line for line in good.splitlines() if ":CONFIG:" in line)
        timer_line = next(line for line in good.splitlines() if ":TIMER:" in line)
        cases = (
            ("missing", good.replace(first_result + "\r\n", "")),
            (
                "duplicate",
                good.replace(first_result, first_result + "\r\n" + first_result),
            ),
            (
                "out-of-order",
                good.replace(config_line + "\r\n", "").replace(
                    timer_line, config_line + "\r\n" + timer_line
                ),
            ),
            (
                "failure",
                good.replace(
                    "P2SDBENCH:DONE:SEQ=1234ABCD",
                    "P2SDBENCH:FAIL:STAGE=READ:CODE=-5\r\nP2SDBENCH:DONE:SEQ=1234ABCD",
                ),
            ),
            (
                "unexpected",
                good.replace(
                    "P2SDBENCH:DONE:SEQ=1234ABCD",
                    "P2SDBENCH:NEW:FIELD=1\r\nP2SDBENCH:DONE:SEQ=1234ABCD",
                ),
            ),
        )
        for name, text in cases:
            with self.subTest(name=name):
                result = benchmark.parse_benchmark(text, SEQUENCE)
                self.assertFalse(result["complete"], result)

    def test_offline_parameter_inference_requires_one_begin(self):
        self.assertEqual(
            benchmark.transcript_parameters(complete_log()),
            (SEQUENCE, benchmark.DEFAULT_BYTES, benchmark.DEFAULT_PASSES),
        )
        with self.assertRaises(ValueError):
            benchmark.transcript_parameters("no benchmark here")
        with self.assertRaises(ValueError):
            benchmark.transcript_parameters(complete_log() + complete_log())


if __name__ == "__main__":
    unittest.main()
