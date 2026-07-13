# SPDX-License-Identifier: Apache-2.0

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

import clock_protocol as clock


READY = "P2CLOCK:READY:SYSCLK=180000000:XTAL=20000000:COUNTER_BITS=32"


def exact_samples(duration=12, hz=clock.EXPECTED_SYSCLK_HZ, latency=0.001):
    samples = []
    for sequence in range(duration + 1):
        send = float(sequence)
        capture = send + latency / 2.0
        receive = send + latency
        samples.append(
            clock.ClockSample(
                sequence,
                int(capture * hz) % clock.COUNTER_MODULUS,
                send,
                receive,
            )
        )
    return samples


class ClockMarkerParserTests(unittest.TestCase):
    def test_exact_ready_sample_done_stream(self):
        parser = clock.ClockMarkerParser()
        self.assertIsNone(parser.feed_line("loadp2 loader chatter"))
        ready = parser.feed_line(READY)
        first = parser.feed_line(
            "P2CLOCK:SAMPLE:SEQ=00000000:COUNTER=FFFFFF00"
        )
        second = parser.feed_line(
            "P2CLOCK:SAMPLE:SEQ=00000001:COUNTER=00000080"
        )
        done = parser.feed_line("P2CLOCK:DONE:SAMPLES=00000002")

        self.assertEqual(ready.kind, "ready")
        self.assertEqual(first.sequence, 0)
        self.assertEqual(first.counter, 0xFFFFFF00)
        self.assertEqual(second.sequence, 1)
        self.assertEqual(done.sample_count, 2)

    def test_markers_are_exact_uppercase_and_fixed_width(self):
        invalid = (
            READY.replace("180000000", "179999999"),
            READY.replace("20000000", "25000000"),
            READY.replace("32", "64"),
            "P2CLOCK:SAMPLE:SEQ=0:COUNTER=00000000",
            "P2CLOCK:SAMPLE:SEQ=00000000:COUNTER=abcdef01",
            "P2CLOCK:DONE:SAMPLES=1",
            "P2CLOCK:READY:SYSCLK=180000000:XTAL=20000000:COUNTER_BITS=32:EXTRA=1",
            "P2CLOCK:UNKNOWN",
            "prefix P2CLOCK:DONE:SAMPLES=00000000",
            "p2clock:done:samples=00000000",
        )
        for line in invalid:
            with self.subTest(line=line):
                parser = clock.ClockMarkerParser()
                if not line.startswith("P2CLOCK:READY"):
                    parser.feed_line(READY)
                with self.assertRaises(clock.ClockProtocolError):
                    parser.feed_line(line)

    def test_order_duplicates_sequence_and_done_count_are_enforced(self):
        with self.assertRaisesRegex(clock.ClockProtocolError, "preceded READY"):
            clock.ClockMarkerParser().feed_line(
                "P2CLOCK:SAMPLE:SEQ=00000000:COUNTER=00000000"
            )

        parser = clock.ClockMarkerParser()
        parser.feed_line(READY)
        with self.assertRaisesRegex(clock.ClockProtocolError, "duplicate"):
            parser.feed_line(READY)

        parser = clock.ClockMarkerParser()
        parser.feed_line(READY)
        with self.assertRaisesRegex(clock.ClockProtocolError, "expected 00000000"):
            parser.feed_line(
                "P2CLOCK:SAMPLE:SEQ=00000001:COUNTER=00000000"
            )

        parser = clock.ClockMarkerParser()
        parser.feed_line(READY)
        parser.feed_line("P2CLOCK:SAMPLE:SEQ=00000000:COUNTER=00000000")
        with self.assertRaisesRegex(clock.ClockProtocolError, "DONE sample count"):
            parser.feed_line("P2CLOCK:DONE:SAMPLES=00000000")

    def test_target_failure_cannot_be_hidden(self):
        parser = clock.ClockMarkerParser()
        parser.feed_line(READY)
        with self.assertRaisesRegex(clock.ClockProtocolError, "target failure"):
            parser.feed_line("P2CLOCK:FAIL:READ:ERRNO=5")


class ClockCalibrationTests(unittest.TestCase):
    def test_wrap_safe_delta_and_multiwrap_calibration(self):
        self.assertEqual(clock.counter_delta(0xFFFFFF00, 0x00000080), 0x180)
        samples = exact_samples(duration=600)
        result = clock.calibration_result(samples, 599.0)
        self.assertGreater(result["counter_ticks"], 25 * clock.COUNTER_MODULUS)
        self.assertAlmostEqual(
            result["frequency_estimate_hz"],
            clock.EXPECTED_SYSCLK_HZ,
            delta=0.01,
        )
        self.assertAlmostEqual(result["ppm_estimate"], 0.0, delta=0.001)
        self.assertTrue(result["qualified"])
        self.assertTrue(result["structural_sanity"])
        self.assertEqual(result["status"], "PASS")

    def test_conservative_elapsed_and_frequency_bounds(self):
        samples = exact_samples(duration=12, latency=0.010)
        result = clock.calibration_result(samples, 11.98)
        self.assertEqual(result["elapsed_lower_bound_seconds"], 11.99)
        self.assertAlmostEqual(result["elapsed_midpoint_seconds"], 12.0)
        self.assertEqual(result["elapsed_upper_bound_seconds"], 12.01)
        self.assertLess(
            result["frequency_lower_bound_hz"],
            result["frequency_estimate_hz"],
        )
        self.assertLess(
            result["frequency_estimate_hz"],
            result["frequency_upper_bound_hz"],
        )

    def test_qualification_uses_last_send_minus_first_receive(self):
        samples = exact_samples(duration=10, latency=0.010)
        result = clock.calibration_result(samples, 10.0)
        self.assertEqual(result["elapsed_lower_bound_seconds"], 9.99)
        self.assertFalse(result["qualified"])
        self.assertEqual(result["status"], "FAIL")
        self.assertIsNone(clock.first_qualified_prefix(samples, 10.0))

        samples.extend(exact_samples(duration=11, latency=0.010)[-1:])
        prefix = clock.first_qualified_prefix(samples, 10.0)
        self.assertEqual(len(prefix), 12)

    def test_gap_and_overlapping_outstanding_requests_are_rejected(self):
        samples = exact_samples(duration=2)
        samples[1] = clock.ClockSample(1, samples[1].counter, 0.0005, 1.001)
        with self.assertRaisesRegex(clock.ClockProtocolError, "more than one S"):
            clock.validate_samples(samples)

        samples = exact_samples(duration=2)
        samples[1] = clock.ClockSample(1, samples[1].counter, 1.0, 5.001)
        with self.assertRaisesRegex(clock.ClockProtocolError, "exceeds"):
            clock.validate_samples(samples)

    def test_broad_one_percent_sanity_checks_the_conservative_interval(self):
        good = clock.calibration_result(exact_samples(12, hz=181_000_000), 10)
        self.assertTrue(good["structural_sanity"])

        high = clock.calibration_result(exact_samples(12, hz=182_000_000), 10)
        self.assertFalse(high["structural_sanity"])
        self.assertEqual(high["status"], "FAIL")

    def test_jsonl_record_contains_brackets_delta_and_upper_gap(self):
        samples = exact_samples(duration=1)
        record = clock.sample_record(samples[1], samples[0])
        self.assertEqual(record["format"], "p2-clock-sample-v1")
        self.assertEqual(record["sequence_hex"], "00000001")
        self.assertEqual(record["counter_hex"], "0ABBF490")
        self.assertEqual(record["counter_delta_ticks"], 180_000_000)
        self.assertAlmostEqual(record["conservative_gap_seconds"], 1.001)


if __name__ == "__main__":
    unittest.main()
