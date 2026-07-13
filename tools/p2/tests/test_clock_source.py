# SPDX-License-Identifier: Apache-2.0

import pathlib
import unittest


APPS_ROOT = pathlib.Path(__file__).resolve().parents[4] / "apps"
SOURCE = APPS_ROOT / "testing" / "p2clock" / "p2clock_main.c"


class ClockSourceTests(unittest.TestCase):
    def test_command_frame_is_consumed_before_sample_response(self):
        text = SOURCE.read_text(encoding="utf-8")
        terminator_read = text.index("p2clock_read_byte(&terminator)")
        sample_dispatch = text.index("if (command == 'S')")

        self.assertLess(terminator_read, sample_dispatch)
        self.assertIn("terminator != '\\r' && terminator != '\\n'", text)
        self.assertIn('p2clock_fail("FRAME", EPROTO)', text)

    def test_protocol_uses_raw_getct_and_exact_markers(self):
        text = SOURCE.read_text(encoding="utf-8")

        self.assertIn('__asm__ __volatile__("getct %0"', text)
        self.assertIn("P2CLOCK:READY:SYSCLK=%u:XTAL=%u:COUNTER_BITS=32", text)
        self.assertIn("P2CLOCK:SAMPLE:SEQ=%08", text)
        self.assertIn("P2CLOCK:DONE:SAMPLES=%08", text)


if __name__ == "__main__":
    unittest.main()
