#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/p2"))

from schedstress_protocol import (
    BOOT_MARKER,
    HEAP_CONCURRENCY_PASS_MARKER,
    HEAP_CONCURRENCY_START_MARKER,
    HEAP_START_MARKER,
    PASS_MARKER,
    PROFILE_MARKER,
    STACK_START_MARKER,
    STAGES,
    TOTAL_EVENTS,
    TOTAL_MARKER,
    marker_patterns,
    parse_schedstress,
)


def complete_log():
    lines = [BOOT_MARKER, PROFILE_MARKER]
    for stage, count in STAGES:
        lines.extend(
            (
                "P2SCHED:{}:START:TARGET={}".format(stage, count),
                "P2SCHED:{}:PASS:COUNT={}".format(stage, count),
            )
        )
    lines.extend(
        (
            STACK_START_MARKER,
            "P2SCHED:STACK:PASS:CHECKS=3:SIZE=6144:USED=1872",
            HEAP_START_MARKER,
            "P2SCHED:HEAP:PASS:CHECKS=5:BEFORE=320:"
            "DURING=4448:AFTER=320",
            HEAP_CONCURRENCY_START_MARKER,
            HEAP_CONCURRENCY_PASS_MARKER,
            TOTAL_MARKER,
            PASS_MARKER,
        )
    )
    return "\r\n".join(lines) + "\r\n"


class SchedulerStressProtocolTests(unittest.TestCase):
    def test_complete_exact_protocol_passes(self):
        result = parse_schedstress(complete_log())

        self.assertTrue(result["complete"], result)
        self.assertEqual(result["reset_count"], 1)
        self.assertEqual(result["values"]["total_events"], 1_004_078)
        self.assertEqual(
            sum(result["values"]["stage_counts"].values()), TOTAL_EVENTS
        )
        self.assertEqual(result["values"]["stack_checks"], 3)
        self.assertEqual(result["values"]["heap_checks"], 5)
        self.assertEqual(result["values"]["heap_concurrency_threads"], 2)
        self.assertEqual(result["values"]["heap_concurrency_rounds"], 256)
        self.assertEqual(result["values"]["heap_concurrency_count"], 512)
        self.assertFalse(
            result["values"]["heap_concurrency_counted_in_total"]
        )

    def test_every_stage_start_pass_and_total_count_are_exact(self):
        replacements = []
        for stage, count in STAGES:
            replacements.extend(
                (
                    (
                        "P2SCHED:{}:START:TARGET={}".format(stage, count),
                        "P2SCHED:{}:START:TARGET={}".format(stage, count + 1),
                    ),
                    (
                        "P2SCHED:{}:PASS:COUNT={}".format(stage, count),
                        "P2SCHED:{}:PASS:COUNT={}".format(stage, count - 1),
                    ),
                )
            )
        replacements.extend(
            (
                (TOTAL_MARKER, "P2SCHED:TOTAL:PASS:COUNT=1004077"),
                (PASS_MARKER, "P2SCHED:PASS:COUNT=1004079"),
            )
        )

        for original, replacement in replacements:
            with self.subTest(original=original):
                result = parse_schedstress(
                    complete_log().replace(original, replacement)
                )
                self.assertFalse(result["complete"])
                self.assertTrue(result["errors"])

    def test_concurrent_allocation_proof_is_mandatory_and_not_in_total(self):
        for marker in (
            HEAP_CONCURRENCY_START_MARKER,
            HEAP_CONCURRENCY_PASS_MARKER,
        ):
            with self.subTest(marker=marker):
                result = parse_schedstress(
                    complete_log().replace(marker + "\r\n", "")
                )
                self.assertFalse(result["complete"])
                self.assertTrue(any(marker in error for error in result["errors"]))

        changed = complete_log().replace("TARGET=512", "TARGET=1004590")
        result = parse_schedstress(changed)
        self.assertFalse(result["complete"])
        self.assertEqual(result["values"]["total_events"], TOTAL_EVENTS)

    def test_stack_and_heap_pass_records_have_semantic_checks(self):
        replacements = (
            (
                "P2SCHED:STACK:PASS:CHECKS=3:SIZE=6144:USED=1872",
                "P2SCHED:STACK:PASS:CHECKS=3:SIZE=6144:USED=6145",
            ),
            (
                "P2SCHED:STACK:PASS:CHECKS=3:SIZE=6144:USED=1872",
                "P2SCHED:STACK:PASS:CHECKS=2:SIZE=6144:USED=1872",
            ),
            (
                "BEFORE=320:DURING=4448:AFTER=320",
                "BEFORE=320:DURING=4415:AFTER=320",
            ),
            (
                "BEFORE=320:DURING=4448:AFTER=320",
                "BEFORE=320:DURING=4448:AFTER=4449",
            ),
            (
                "P2SCHED:HEAP:PASS:CHECKS=5",
                "P2SCHED:HEAP:PASS:CHECKS=4",
            ),
        )
        for original, replacement in replacements:
            with self.subTest(replacement=replacement):
                result = parse_schedstress(
                    complete_log().replace(original, replacement)
                )
                self.assertFalse(result["complete"])

    def test_failure_cannot_be_hidden_by_terminal_pass(self):
        result = parse_schedstress(
            complete_log().replace(
                TOTAL_MARKER,
                "P2SCHED:FAIL:HEAP_CONCURRENCY:CODE=-5\r\n" + TOTAL_MARKER,
            )
        )

        self.assertFalse(result["complete"])
        self.assertEqual(
            result["failures"][0]["kind"], "P2 scheduler stress failure"
        )

    def test_profile_duplicate_and_order_are_strict(self):
        wrong_profile = parse_schedstress(
            complete_log().replace("RAM=524288", "RAM=1048576")
        )
        self.assertFalse(wrong_profile["complete"])

        duplicate = parse_schedstress(complete_log() + BOOT_MARKER + "\r\n")
        self.assertFalse(duplicate["complete"])
        self.assertEqual(duplicate["reset_count"], 2)
        self.assertIn(BOOT_MARKER, duplicate["duplicates"])

        lines = complete_log().splitlines()
        lines[-2], lines[-1] = lines[-1], lines[-2]
        out_of_order = parse_schedstress("\r\n".join(lines) + "\r\n")
        self.assertFalse(out_of_order["complete"])
        self.assertIn("protocol markers are out of order", out_of_order["errors"])

    def test_streaming_markers_require_all_exact_ordered_lines(self):
        text = complete_log()
        labels = [label for label, _pattern in marker_patterns()]

        self.assertEqual(labels[0:2], [BOOT_MARKER, PROFILE_MARKER])
        self.assertEqual(labels[-4:], [
            HEAP_CONCURRENCY_START_MARKER,
            HEAP_CONCURRENCY_PASS_MARKER,
            TOTAL_MARKER,
            PASS_MARKER,
        ])
        for label, pattern in marker_patterns():
            with self.subTest(label=label):
                self.assertIsNotNone(pattern.search(text))


if __name__ == "__main__":
    unittest.main()
