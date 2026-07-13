#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Strict marker parsing and conservative P2 raw-clock calibration math."""

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence


EXPECTED_SYSCLK_HZ = 180_000_000
EXPECTED_XTAL_HZ = 20_000_000
COUNTER_BITS = 32
COUNTER_MODULUS = 1 << COUNTER_BITS
MAX_GAP_SECONDS = 5.0
STRUCTURAL_SANITY_FRACTION = 0.01
STRUCTURAL_SANITY_PPM = int(STRUCTURAL_SANITY_FRACTION * 1_000_000)

READY_PATTERN = re.compile(
    r"^P2CLOCK:READY:SYSCLK=(\d+):XTAL=(\d+):COUNTER_BITS=(\d+)$"
)
SAMPLE_PATTERN = re.compile(
    r"^P2CLOCK:SAMPLE:SEQ=([0-9A-F]{8}):COUNTER=([0-9A-F]{8})$"
)
DONE_PATTERN = re.compile(r"^P2CLOCK:DONE:SAMPLES=([0-9A-F]{8})$")
FAILURE_PATTERN = re.compile(r"^P2CLOCK:FAIL:(.+)$")


class ClockProtocolError(ValueError):
    """The target marker stream or calibration sample set is invalid."""


@dataclass(frozen=True)
class Marker:
    """One strictly parsed target marker."""

    kind: str
    line: str
    sequence: Optional[int] = None
    counter: Optional[int] = None
    sysclk_hz: Optional[int] = None
    xtal_hz: Optional[int] = None
    counter_bits: Optional[int] = None
    sample_count: Optional[int] = None


@dataclass(frozen=True)
class ClockSample:
    """One counter value bracketed by host monotonic timestamps."""

    sequence: int
    counter: int
    send_monotonic: float
    receive_monotonic: float

    def validate(self) -> None:
        if not 0 <= self.sequence < COUNTER_MODULUS:
            raise ClockProtocolError("sample sequence is outside 32-bit range")
        if not 0 <= self.counter < COUNTER_MODULUS:
            raise ClockProtocolError("sample counter is outside 32-bit range")
        if not math.isfinite(self.send_monotonic):
            raise ClockProtocolError("sample send timestamp is not finite")
        if not math.isfinite(self.receive_monotonic):
            raise ClockProtocolError("sample receive timestamp is not finite")
        if self.receive_monotonic < self.send_monotonic:
            raise ClockProtocolError("sample receive timestamp precedes send")


class ClockMarkerParser:
    """Enforce the READY, sequential SAMPLE, DONE state machine."""

    def __init__(self) -> None:
        self.ready: Optional[Marker] = None
        self.sample_count = 0
        self.done: Optional[Marker] = None

    def feed_line(self, line: str) -> Optional[Marker]:
        """Parse one normalized line, ignoring non-P2 loader chatter."""

        if not line.startswith("P2CLOCK:"):
            if re.search(r"P2CLOCK:", line, re.IGNORECASE):
                raise ClockProtocolError(
                    "malformed or prefixed P2CLOCK marker: {}".format(line)
                )
            return None

        failure = FAILURE_PATTERN.fullmatch(line)
        if failure is not None:
            raise ClockProtocolError("target failure marker: {}".format(line))

        ready = READY_PATTERN.fullmatch(line)
        if ready is not None:
            if self.ready is not None:
                raise ClockProtocolError("duplicate P2CLOCK:READY marker")
            if self.sample_count or self.done is not None:
                raise ClockProtocolError("P2CLOCK:READY marker is out of order")
            sysclk_hz, xtal_hz, counter_bits = (
                int(value) for value in ready.groups()
            )
            if sysclk_hz != EXPECTED_SYSCLK_HZ:
                raise ClockProtocolError(
                    "target SYSCLK is {}, expected {}".format(
                        sysclk_hz, EXPECTED_SYSCLK_HZ
                    )
                )
            if xtal_hz != EXPECTED_XTAL_HZ:
                raise ClockProtocolError(
                    "target XTAL is {}, expected {}".format(
                        xtal_hz, EXPECTED_XTAL_HZ
                    )
                )
            if counter_bits != COUNTER_BITS:
                raise ClockProtocolError(
                    "target counter width is {}, expected {}".format(
                        counter_bits, COUNTER_BITS
                    )
                )
            marker = Marker(
                "ready",
                line,
                sysclk_hz=sysclk_hz,
                xtal_hz=xtal_hz,
                counter_bits=counter_bits,
            )
            self.ready = marker
            return marker

        sample = SAMPLE_PATTERN.fullmatch(line)
        if sample is not None:
            if self.ready is None:
                raise ClockProtocolError("P2CLOCK:SAMPLE preceded READY")
            if self.done is not None:
                raise ClockProtocolError("P2CLOCK:SAMPLE followed DONE")
            sequence = int(sample.group(1), 16)
            counter = int(sample.group(2), 16)
            if sequence != self.sample_count:
                raise ClockProtocolError(
                    "sample sequence is {:08X}, expected {:08X}".format(
                        sequence, self.sample_count
                    )
                )
            marker = Marker(
                "sample", line, sequence=sequence, counter=counter
            )
            self.sample_count += 1
            return marker

        done = DONE_PATTERN.fullmatch(line)
        if done is not None:
            if self.ready is None:
                raise ClockProtocolError("P2CLOCK:DONE preceded READY")
            if self.done is not None:
                raise ClockProtocolError("duplicate P2CLOCK:DONE marker")
            sample_count = int(done.group(1), 16)
            if sample_count != self.sample_count:
                raise ClockProtocolError(
                    "DONE sample count is {:08X}, expected {:08X}".format(
                        sample_count, self.sample_count
                    )
                )
            marker = Marker("done", line, sample_count=sample_count)
            self.done = marker
            return marker

        raise ClockProtocolError("malformed or unknown P2CLOCK marker: {}".format(line))


def counter_delta(previous: int, current: int) -> int:
    """Return one wrap-safe unsigned 32-bit GETCT delta."""

    if not 0 <= previous < COUNTER_MODULUS:
        raise ClockProtocolError("previous counter is outside 32-bit range")
    if not 0 <= current < COUNTER_MODULUS:
        raise ClockProtocolError("current counter is outside 32-bit range")
    return (current - previous) % COUNTER_MODULUS


def validate_samples(
    samples: Sequence[ClockSample], max_gap_seconds: float = MAX_GAP_SECONDS
) -> None:
    """Validate ordering and keep every possible target gap below the bound."""

    if not math.isfinite(max_gap_seconds) or max_gap_seconds <= 0:
        raise ClockProtocolError("maximum sample gap must be finite and positive")
    for index, sample in enumerate(samples):
        sample.validate()
        round_trip = sample.receive_monotonic - sample.send_monotonic
        if round_trip > max_gap_seconds:
            raise ClockProtocolError(
                "sample round trip {:.9f}s exceeds {:.9f}s".format(
                    round_trip, max_gap_seconds
                )
            )
        if sample.sequence != index:
            raise ClockProtocolError(
                "sample sequence is {}, expected {}".format(
                    sample.sequence, index
                )
            )
        if index == 0:
            continue
        previous = samples[index - 1]
        if sample.send_monotonic < previous.receive_monotonic:
            raise ClockProtocolError(
                "sample requests overlap; more than one S was outstanding"
            )
        conservative_gap = sample.receive_monotonic - previous.send_monotonic
        if conservative_gap > max_gap_seconds:
            raise ClockProtocolError(
                "sample gap {:.9f}s exceeds {:.9f}s".format(
                    conservative_gap, max_gap_seconds
                )
            )


def sample_record(
    sample: ClockSample, previous: Optional[ClockSample] = None
) -> Dict[str, object]:
    """Return the append-only JSONL representation of one sample."""

    sample.validate()
    record: Dict[str, object] = {
        "format": "p2-clock-sample-v1",
        "sequence": sample.sequence,
        "sequence_hex": "{:08X}".format(sample.sequence),
        "counter": sample.counter,
        "counter_hex": "{:08X}".format(sample.counter),
        "send_monotonic_seconds": sample.send_monotonic,
        "receive_monotonic_seconds": sample.receive_monotonic,
        "round_trip_seconds": sample.receive_monotonic - sample.send_monotonic,
        "counter_delta_ticks": None,
        "conservative_gap_seconds": None,
    }
    if previous is not None:
        previous.validate()
        record["counter_delta_ticks"] = counter_delta(
            previous.counter, sample.counter
        )
        record["conservative_gap_seconds"] = (
            sample.receive_monotonic - previous.send_monotonic
        )
    return record


def calibration_result(
    samples: Sequence[ClockSample],
    required_duration_seconds: float,
    nominal_hz: int = EXPECTED_SYSCLK_HZ,
    max_gap_seconds: float = MAX_GAP_SECONDS,
    sanity_fraction: float = STRUCTURAL_SANITY_FRACTION,
) -> Dict[str, object]:
    """Compute a qualified frequency interval from host timestamp brackets.

    A target capture can occur at any point from its command send timestamp to
    the completed response receive timestamp.  Consequently, the elapsed time
    between the first and last captures lies within::

      [last.send - first.receive, last.receive - first.send]

    Frequency bounds invert those elapsed bounds.  Per-interval 32-bit deltas
    are summed so a ten-minute run may cross the GETCT wrap many times.
    """

    if len(samples) < 2:
        raise ClockProtocolError("at least two clock samples are required")
    if not math.isfinite(required_duration_seconds) or required_duration_seconds <= 0:
        raise ClockProtocolError("required duration must be finite and positive")
    if nominal_hz <= 0:
        raise ClockProtocolError("nominal frequency must be positive")
    if not math.isfinite(sanity_fraction) or not 0 < sanity_fraction < 1:
        raise ClockProtocolError("sanity fraction must be between zero and one")

    validate_samples(samples, max_gap_seconds)
    first = samples[0]
    last = samples[-1]
    ticks = sum(
        counter_delta(previous.counter, current.counter)
        for previous, current in zip(samples, samples[1:])
    )
    lower_elapsed = last.send_monotonic - first.receive_monotonic
    upper_elapsed = last.receive_monotonic - first.send_monotonic
    midpoint_elapsed = (
        (last.send_monotonic + last.receive_monotonic)
        - (first.send_monotonic + first.receive_monotonic)
    ) / 2.0
    if lower_elapsed <= 0 or midpoint_elapsed <= 0 or upper_elapsed <= 0:
        raise ClockProtocolError("host timestamp brackets do not span positive time")
    if ticks <= 0:
        raise ClockProtocolError("counter did not advance")

    frequency_lower = ticks / upper_elapsed
    frequency_estimate = ticks / midpoint_elapsed
    frequency_upper = ticks / lower_elapsed
    ppm_lower = (frequency_lower / nominal_hz - 1.0) * 1_000_000.0
    ppm_estimate = (frequency_estimate / nominal_hz - 1.0) * 1_000_000.0
    ppm_upper = (frequency_upper / nominal_hz - 1.0) * 1_000_000.0
    qualified = lower_elapsed >= required_duration_seconds
    sanity_lower = nominal_hz * (1.0 - sanity_fraction)
    sanity_upper = nominal_hz * (1.0 + sanity_fraction)
    structural_sanity = (
        frequency_lower >= sanity_lower and frequency_upper <= sanity_upper
    )

    return {
        "format": "p2-clock-calibration-v1",
        "status": "PASS" if qualified and structural_sanity else "FAIL",
        "qualified": qualified,
        "structural_sanity": structural_sanity,
        "structural_sanity_fraction": sanity_fraction,
        "structural_sanity_ppm": sanity_fraction * 1_000_000.0,
        "required_duration_seconds": required_duration_seconds,
        "sample_count": len(samples),
        "first_sequence": first.sequence,
        "last_sequence": last.sequence,
        "first_counter_hex": "{:08X}".format(first.counter),
        "last_counter_hex": "{:08X}".format(last.counter),
        "counter_ticks": ticks,
        "counter_bits": COUNTER_BITS,
        "counter_wrap_seconds_at_nominal": COUNTER_MODULUS / nominal_hz,
        "maximum_gap_seconds": max_gap_seconds,
        "elapsed_lower_bound_seconds": lower_elapsed,
        "elapsed_midpoint_seconds": midpoint_elapsed,
        "elapsed_upper_bound_seconds": upper_elapsed,
        "nominal_frequency_hz": nominal_hz,
        "frequency_lower_bound_hz": frequency_lower,
        "frequency_estimate_hz": frequency_estimate,
        "frequency_upper_bound_hz": frequency_upper,
        "ppm_lower_bound": ppm_lower,
        "ppm_estimate": ppm_estimate,
        "ppm_upper_bound": ppm_upper,
        "qualification_rule": "last.send-first.receive >= duration",
        "tolerance_policy": "broad structural sanity only; no accuracy tolerance",
    }


def first_qualified_prefix(
    samples: Sequence[ClockSample], required_duration_seconds: float
) -> Optional[List[ClockSample]]:
    """Return the earliest prefix satisfying the conservative duration rule."""

    if not samples:
        return None
    first_receive = samples[0].receive_monotonic
    for end in range(1, len(samples)):
        if samples[end].send_monotonic - first_receive >= required_duration_seconds:
            return list(samples[: end + 1])
    return None
