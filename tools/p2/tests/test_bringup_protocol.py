import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

from bringup_protocol import BRINGUP_MARKERS, parse_bringup


class BringupProtocolTests(unittest.TestCase):
    def good_output(self):
        return "loader output\r\n" + "\r\n".join(BRINGUP_MARKERS) + "\r\n"

    def test_exact_ordered_marker_lines_pass(self):
        result = parse_bringup(self.good_output())

        self.assertTrue(result["complete"])
        self.assertEqual(result["found"], list(BRINGUP_MARKERS))
        self.assertEqual(result["reset_count"], 1)

    def test_missing_marker_fails(self):
        result = parse_bringup(
            self.good_output().replace("P2NUTTX:TICK=OK\r\n", "")
        )

        self.assertFalse(result["complete"])
        self.assertEqual(result["missing"], ["P2NUTTX:TICK=OK"])

    def test_out_of_order_markers_fail(self):
        lines = list(BRINGUP_MARKERS)
        lines[4], lines[5] = lines[5], lines[4]

        result = parse_bringup("\n".join(lines))

        self.assertFalse(result["complete"])
        self.assertFalse(result["order_valid"])

    def test_duplicate_boot_marker_is_an_unexpected_reset(self):
        result = parse_bringup(self.good_output() + "P2NUTTX:BOOT\n")

        self.assertFalse(result["complete"])
        self.assertEqual(result["duplicates"], ["P2NUTTX:BOOT"])
        self.assertEqual(result["reset_count"], 2)

    def test_failure_output_cannot_be_hidden_by_pass_markers(self):
        result = parse_bringup(
            self.good_output() + "P2NUTTX:FAIL:STACKS\nAssertion failed\n"
        )

        self.assertFalse(result["complete"])
        self.assertEqual(
            [failure["kind"] for failure in result["failures"]],
            ["P2NUTTX failure", "assertion"],
        )

    def test_marker_substrings_are_not_accepted(self):
        result = parse_bringup(
            self.good_output().replace("P2NUTTX:PASS", "echo P2NUTTX:PASS")
        )

        self.assertFalse(result["complete"])
        self.assertEqual(result["missing"], ["P2NUTTX:PASS"])


if __name__ == "__main__":
    unittest.main()
