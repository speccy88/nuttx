#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Minimal, testable Propeller 2 reset control for an open serial port.

The pulse sequence and two-millisecond dwell times match the DTR reset used
by the pinned P2 loader.  This module deliberately does not open, close, or
write to the serial connection; callers can therefore retain one connection
across a bounded reset campaign.
"""

import time
from typing import Callable


DTR_DWELL_SECONDS = 0.002


class ResetError(RuntimeError):
    """The supplied serial connection cannot perform a safe DTR reset."""


def dtr_reset(
    connection: object,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Pulse DTR on an already-open connection without transmitting bytes."""

    if hasattr(connection, "is_open") and not connection.is_open:
        raise ResetError("serial connection is not open")
    if not hasattr(connection, "dtr"):
        raise ResetError("serial connection does not expose DTR control")
    if not hasattr(connection, "reset_input_buffer"):
        raise ResetError("serial connection cannot discard pre-reset input")

    try:
        connection.dtr = True
        sleep(DTR_DWELL_SECONDS)
        connection.dtr = False
        sleep(DTR_DWELL_SECONDS)
        connection.dtr = True
        sleep(DTR_DWELL_SECONDS)
        connection.reset_input_buffer()
    except (AttributeError, OSError, RuntimeError, ValueError) as exc:
        raise ResetError("DTR reset failed: {}".format(exc)) from exc
