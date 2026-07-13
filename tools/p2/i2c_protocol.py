#!/usr/bin/env python3
"""Strict console protocol for the P2 BMP180 I2C HIL application."""

import re
from typing import Dict, List, Sequence, Tuple


START_MARKER = (
    "P2I2C:START:BUS=/dev/i2c0:SDA=24:SCL=25:"
    "ADDR=0x77:FREQ=100000"
)
BUS_MARKER = (
    "P2I2C:BUS=PASS:DEV=/dev/i2c0:SDA=24:SCL=25:OPEN_DRAIN=YES"
)
BMP180_MARKER = (
    "P2I2C:BMP180=PASS:DEV=/dev/press0:ADDR=0x77:ID=0x55"
)
ID_MARKER = (
    "P2I2C:ID=0x55:REGISTER=0xD0:"
    "TRANSFER=WRITE_RESTART_READ"
)
PASS_MARKER = "P2I2C:PASS"
READING_COUNT = 32
PRESSURE_MIN_PA = 30000
PRESSURE_MAX_PA = 120000

READINGS_PATTERN = re.compile(
    r"^P2I2C:READINGS=(\d+):MIN=(\d+):MAX=(\d+):"
    r"FNV1A=([0-9A-F]{8})$"
)
RECOVERY_PATTERN = re.compile(
    r"^P2I2C:BUS_RECOVERY=PASS:SDA=24:SCL=25:PULSES=([0-9])$"
)

FAILURE_PATTERNS = (
    ("P2I2C failure", re.compile(r"^P2I2C:FAIL:[a-z0-9-]+:[1-9]\d*$")),
    (
        "P2 I2C board initialization failure",
        re.compile(r"ERROR: Failed to initialize P2 I2C: -?\d+"),
    ),
    ("PANIC", re.compile(r"\bPANIC\b", re.IGNORECASE)),
    ("assertion", re.compile(r"\bASSERT(?:ION)?\b", re.IGNORECASE)),
    ("stack overflow", re.compile(r"STACK\s+OVERFLOW", re.IGNORECASE)),
    ("unexpected IRQ", re.compile(r"UNEXPECTED\s+IRQ", re.IGNORECASE)),
    ("register dump", re.compile(r"REGISTER\s+DUMP", re.IGNORECASE)),
)


def _line_positions(lines: Sequence[str], marker: str) -> List[int]:
    return [index for index, line in enumerate(lines) if line == marker]


def marker_patterns() -> Tuple[Tuple[str, re.Pattern], ...]:
    """Return ordered streaming markers for one complete application run."""

    return (
        (
            "P2I2C:BUS_RECOVERY=PASS",
            re.compile(
                r"(?:^|[\r\n])P2I2C:BUS_RECOVERY=PASS:"
                r"SDA=24:SCL=25:PULSES=[0-9]\r?\n"
            ),
        ),
        (
            BUS_MARKER,
            re.compile(r"(?:^|[\r\n])" + re.escape(BUS_MARKER) + r"\r?\n"),
        ),
        (
            BMP180_MARKER,
            re.compile(
                r"(?:^|[\r\n])" + re.escape(BMP180_MARKER) + r"\r?\n"
            ),
        ),
        (
            START_MARKER,
            re.compile(r"(?:^|[\r\n])" + re.escape(START_MARKER) + r"\r?\n"),
        ),
        (
            ID_MARKER,
            re.compile(r"(?:^|[\r\n])" + re.escape(ID_MARKER) + r"\r?\n"),
        ),
        (
            "P2I2C:READINGS=32",
            re.compile(
                r"(?:^|[\r\n])P2I2C:READINGS=32:MIN=\d+:MAX=\d+:"
                r"FNV1A=[0-9A-F]{8}\r?\n"
            ),
        ),
        (
            PASS_MARKER,
            re.compile(r"(?:^|[\r\n])P2I2C:PASS\r?\n"),
        ),
    )


def parse_i2c(text: str) -> Dict[str, object]:
    """Validate one reset of the fixed P2 BMP180 marker protocol."""

    lines = [line.strip() for line in text.replace("\r", "\n").split("\n")]
    errors: List[str] = []
    duplicates: List[str] = []
    failures: List[Dict[str, str]] = []
    marker_positions: Dict[str, int] = {}
    values: Dict[str, object] = {}

    for line in lines:
        for label, pattern in FAILURE_PATTERNS:
            if pattern.search(line):
                failures.append({"kind": label, "line": line})
                break

    recoveries = [
        (index, match)
        for index, line in enumerate(lines)
        for match in [RECOVERY_PATTERN.fullmatch(line)]
        if match is not None
    ]
    if not recoveries:
        errors.append("missing P2I2C:BUS_RECOVERY record")
    elif len(recoveries) != 1:
        duplicates.append("P2I2C:BUS_RECOVERY")
    else:
        position, match = recoveries[0]
        marker_positions["P2I2C:BUS_RECOVERY"] = position
        values["recovery_pulses"] = int(match.group(1))

    for marker in (BUS_MARKER, BMP180_MARKER, START_MARKER, ID_MARKER, PASS_MARKER):
        found = _line_positions(lines, marker)
        if not found:
            errors.append("missing {}".format(marker))
        elif len(found) != 1:
            duplicates.append(marker)
        else:
            marker_positions[marker] = found[0]

    readings = [
        (index, match)
        for index, line in enumerate(lines)
        for match in [READINGS_PATTERN.fullmatch(line)]
        if match is not None
    ]
    if not readings:
        errors.append("missing P2I2C:READINGS record")
    elif len(readings) != 1:
        duplicates.append("P2I2C:READINGS")
    else:
        position, match = readings[0]
        count, minimum, maximum = (int(value) for value in match.groups()[:3])
        hash_value = match.group(4)
        marker_positions["P2I2C:READINGS"] = position
        values.update(
            {
                "reading_count": count,
                "minimum_pa": minimum,
                "maximum_pa": maximum,
                "fnv1a": hash_value,
            }
        )
        if count != READING_COUNT:
            errors.append(
                "pressure reading count is {}, expected {}".format(
                    count, READING_COUNT
                )
            )
        if not PRESSURE_MIN_PA <= minimum <= PRESSURE_MAX_PA:
            errors.append(
                "minimum pressure {} is outside {}..{} Pa".format(
                    minimum, PRESSURE_MIN_PA, PRESSURE_MAX_PA
                )
            )
        if not PRESSURE_MIN_PA <= maximum <= PRESSURE_MAX_PA:
            errors.append(
                "maximum pressure {} is outside {}..{} Pa".format(
                    maximum, PRESSURE_MIN_PA, PRESSURE_MAX_PA
                )
            )
        if minimum > maximum:
            errors.append("minimum pressure exceeds maximum pressure")

    positions = [
        marker_positions[marker]
        for marker in (
            "P2I2C:BUS_RECOVERY",
            BUS_MARKER,
            BMP180_MARKER,
            START_MARKER,
            ID_MARKER,
            "P2I2C:READINGS",
            PASS_MARKER,
        )
        if marker in marker_positions
    ]
    order_valid = positions == sorted(positions)
    if not order_valid:
        errors.append("protocol markers are out of order")

    complete = not errors and not duplicates and not failures and order_valid
    return {
        "complete": complete,
        "errors": errors,
        "duplicates": duplicates,
        "failures": failures,
        "order_valid": order_valid,
        "reset_count": len(recoveries),
        "values": values,
    }
