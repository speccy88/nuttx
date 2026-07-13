#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

import reset


class FakeConnection:
    def __init__(self):
        self.is_open = True
        self._dtr = False
        self.transitions = []
        self.input_flushes = 0

    @property
    def dtr(self):
        return self._dtr

    @dtr.setter
    def dtr(self, value):
        self._dtr = value
        self.transitions.append(value)

    def reset_input_buffer(self):
        self.input_flushes += 1


class ResetTests(unittest.TestCase):
    def test_reset_matches_pinned_dtr_pulse_without_transmitting(self):
        connection = FakeConnection()
        sleeps = []

        reset.dtr_reset(connection, sleep=sleeps.append)

        self.assertEqual(connection.transitions, [True, False, True])
        self.assertEqual(sleeps, [reset.DTR_DWELL_SECONDS] * 3)
        self.assertEqual(connection.input_flushes, 1)
        self.assertFalse(hasattr(connection, "writes"))

    def test_closed_or_incomplete_connection_is_rejected(self):
        connection = FakeConnection()
        connection.is_open = False
        with self.assertRaisesRegex(reset.ResetError, "not open"):
            reset.dtr_reset(connection, sleep=lambda duration: None)

        with self.assertRaisesRegex(reset.ResetError, "DTR"):
            reset.dtr_reset(object(), sleep=lambda duration: None)


if __name__ == "__main__":
    unittest.main()
