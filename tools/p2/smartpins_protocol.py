#!/usr/bin/env python3
"""Strict console protocol for the deterministic P2 Smart Pin HIL app."""

import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


STAGE_ORDER = ("GPIO", "EDGE", "UART", "PWM_CAPTURE", "DAC_ADC", "SPI")
DEFAULT_STAGES = ("GPIO",)
WIRING_MARKER = "P2SMART:WIRING=P0-P1,P2-P3,P4-P5,P6-P7"
GPIO_PATTERN = (0, 1, 1, 0, 1, 0, 0, 1)
DIGITAL_PAYLOAD_FNV1A = "504B8F7B"
SPI_BEGIN_MARKER = (
    "P2SMART:SPI:BEGIN=MOSI=6:MISO=7:SCK=8:CS=9:"
    "MODE=0:REQUEST_HZ=100000"
)
SPI_SAFE_MARKER = "P2SMART:SPI:SAFE=MOSI6,MISO7,SCK8,CS9=FLOAT"
CONFIG_STAGE_SYMBOLS = (
    ("GPIO", "CONFIG_TESTING_P2SMARTPINS"),
    ("EDGE", "CONFIG_TESTING_P2SMARTPINS_EDGE"),
    ("UART", "CONFIG_TESTING_P2SMARTPINS_UART"),
    ("PWM_CAPTURE", "CONFIG_TESTING_P2SMARTPINS_PWM_CAPTURE"),
    ("DAC_ADC", "CONFIG_TESTING_P2SMARTPINS_DAC_ADC"),
    ("SPI", "CONFIG_TESTING_P2SMARTPINS_SPI"),
)

FAILURE_PATTERNS = (
    ("P2SMART failure", re.compile(r"^P2SMART:FAIL(?:[:=]|$)")),
    ("PANIC", re.compile(r"\bPANIC\b", re.IGNORECASE)),
    ("assertion", re.compile(r"\bASSERT(?:ION)?\b", re.IGNORECASE)),
    ("error", re.compile(r"\bERROR\b", re.IGNORECASE)),
    ("stack overflow", re.compile(r"STACK\s+OVERFLOW", re.IGNORECASE)),
    ("unexpected IRQ", re.compile(r"UNEXPECTED\s+IRQ", re.IGNORECASE)),
    ("register dump", re.compile(r"REGISTER\s+DUMP", re.IGNORECASE)),
)

SAMPLE_PATTERNS = {
    "GPIO": re.compile(r"^P2SMART:GPIO:SAMPLE=(\d+):TX=([01]):RX=([01])$"),
    "PWM_CAPTURE": re.compile(
        r"^P2SMART:PWM_CAPTURE:SAMPLE=(\d+):FREQ=(\d+):"
        r"DUTY=(\d+):EDGES=(\d+)$"
    ),
    "DAC_ADC": re.compile(
        r"^P2SMART:DAC_ADC:SAMPLE=(\d+):DAC=(-?\d+):ADC=(-?\d+)$"
    ),
}

STAGE_FIXED_MARKERS = {
    "GPIO": (
        "P2SMART:GPIO:BEGIN=0-1",
        "P2SMART:GPIO:SAFE=FLOAT",
        "P2SMART:GPIO:PASS",
    ),
    "EDGE": (
        "P2SMART:EDGE:BEGIN=0-1",
        "P2SMART:EDGE:COUNT=6",
        "P2SMART:EDGE:SAFE=FLOAT",
        "P2SMART:EDGE:PASS",
    ),
    "UART": (
        "P2SMART:UART:BEGIN=2-3",
        "P2SMART:UART:COUNT=16:FNV1A={}".format(DIGITAL_PAYLOAD_FNV1A),
        "P2SMART:UART:SAFE=FLOAT",
        "P2SMART:UART:PASS",
    ),
    "PWM_CAPTURE": (
        "P2SMART:PWM_CAPTURE:BEGIN=4-5",
        "P2SMART:PWM_CAPTURE:SAFE=FLOAT",
        "P2SMART:PWM_CAPTURE:PASS",
    ),
    "DAC_ADC": (
        "P2SMART:DAC_ADC:BEGIN=4-5",
        "P2SMART:DAC_ADC:SAFE=FLOAT",
        "P2SMART:DAC_ADC:PASS",
    ),
    "SPI": (
        SPI_BEGIN_MARKER,
        "P2SMART:SPI:COUNT=16:TX={0}:RX={0}".format(
            DIGITAL_PAYLOAD_FNV1A
        ),
        SPI_SAFE_MARKER,
        "P2SMART:SPI:PASS",
    ),
}


def _line_positions(lines: Sequence[str], marker: str) -> List[int]:
    return [index for index, line in enumerate(lines) if line == marker]


def _validate_gpio(samples: Sequence[Tuple[int, re.Match]]) -> List[str]:
    errors: List[str] = []
    if len(samples) != len(GPIO_PATTERN):
        return ["GPIO sample count is {}, expected {}".format(
            len(samples), len(GPIO_PATTERN)
        )]

    for expected_index, (position, match) in enumerate(samples):
        del position
        index, transmitted, received = (int(value) for value in match.groups())
        if index != expected_index:
            errors.append("GPIO sample index {} is out of order".format(index))
        if transmitted != GPIO_PATTERN[expected_index]:
            errors.append("GPIO sample {} has wrong TX value".format(index))
        if received != transmitted:
            errors.append("GPIO sample {} data mismatch".format(index))
    return errors


def _validate_pwm_capture(samples: Sequence[Tuple[int, re.Match]]) -> List[str]:
    errors: List[str] = []
    targets = (25, 50, 75)
    if len(samples) != len(targets):
        return ["PWM_CAPTURE sample count is {}, expected {}".format(
            len(samples), len(targets)
        )]

    for expected_index, (position, match) in enumerate(samples):
        del position
        index, frequency, duty, edges = (int(value) for value in match.groups())
        if index != expected_index:
            errors.append(
                "PWM_CAPTURE sample index {} is out of order".format(index)
            )
        if not 950 <= frequency <= 1050:
            errors.append(
                "PWM_CAPTURE sample {} frequency {} is outside 950..1050".format(
                    index, frequency
                )
            )
        if abs(duty - targets[expected_index]) > 5:
            errors.append(
                "PWM_CAPTURE sample {} duty {} misses target {}".format(
                    index, duty, targets[expected_index]
                )
            )
        if edges <= 0:
            errors.append("PWM_CAPTURE sample {} has no edges".format(index))
    return errors


def _validate_dac_adc(samples: Sequence[Tuple[int, re.Match]]) -> List[str]:
    errors: List[str] = []
    if len(samples) != 3:
        return ["DAC_ADC sample count is {}, expected 3".format(len(samples))]

    previous_dac: Optional[int] = None
    previous_adc: Optional[int] = None
    for expected_index, (position, match) in enumerate(samples):
        del position
        index, dac, adc = (int(value) for value in match.groups())
        if index != expected_index:
            errors.append("DAC_ADC sample index {} is out of order".format(index))
        if previous_dac is not None and dac <= previous_dac:
            errors.append("DAC_ADC DAC codes are not strictly increasing")
        if previous_adc is not None and adc <= previous_adc:
            errors.append("DAC_ADC ADC samples are not strictly increasing")
        previous_dac = dac
        previous_adc = adc
    return errors


SAMPLE_VALIDATORS = {
    "GPIO": _validate_gpio,
    "PWM_CAPTURE": _validate_pwm_capture,
    "DAC_ADC": _validate_dac_adc,
}


def stages_from_kconfig(config: Dict[str, str]) -> Tuple[str, ...]:
    """Return the canonical protocol stages enabled in an exact .config."""

    return tuple(
        stage for stage, symbol in CONFIG_STAGE_SYMBOLS
        if config.get(symbol) == "y"
    )


def hil_marker_patterns(
    expected_stages: Iterable[str],
) -> Tuple[Tuple[str, re.Pattern], ...]:
    """Build streaming marker patterns for ``hil.MarkerParser``.

    The caller must still run :func:`parse_smartpins` over the complete cycle
    text before recording PASS.  These patterns ensure the streaming runner
    cannot terminate before every fixed marker and indexed data record has
    arrived.
    """

    expected = tuple(expected_stages)
    canonical = tuple(stage for stage in STAGE_ORDER if stage in expected)
    if expected != canonical or "GPIO" not in expected:
        raise ValueError(
            "Smart Pin stages must be unique, canonical, and include GPIO"
        )
    patterns: List[Tuple[str, re.Pattern]] = [
        ("P2SMART:BEGIN", re.compile(r"(?:^|[\r\n])P2SMART:BEGIN\r?\n")),
        (WIRING_MARKER, re.compile(re.escape(WIRING_MARKER))),
        (
            "P2SMART:CAPS",
            re.compile(
                re.escape("P2SMART:CAPS=" + ",".join(expected)) + r"\r?\n"
            ),
        ),
    ]

    for stage in expected:
        fixed = STAGE_FIXED_MARKERS[stage]
        begin, trailing = fixed[0], fixed[1:]
        patterns.append((begin, re.compile(re.escape(begin))))

        if stage == "GPIO":
            patterns.extend(
                (
                    "P2SMART:GPIO:SAMPLE={}".format(index),
                    re.compile(
                        re.escape(
                            "P2SMART:GPIO:SAMPLE={}:TX={}:RX={}".format(
                                index, value, value
                            )
                        )
                    ),
                )
                for index, value in enumerate(GPIO_PATTERN)
            )
        elif stage == "PWM_CAPTURE":
            patterns.extend(
                (
                    "P2SMART:PWM_CAPTURE:SAMPLE={}".format(index),
                    re.compile(
                        r"P2SMART:PWM_CAPTURE:SAMPLE={}:FREQ=\d+:"
                        r"DUTY=\d+:EDGES=\d+".format(index)
                    ),
                )
                for index in range(3)
            )
        elif stage == "DAC_ADC":
            patterns.extend(
                (
                    "P2SMART:DAC_ADC:SAMPLE={}".format(index),
                    re.compile(
                        r"P2SMART:DAC_ADC:SAMPLE={}:DAC=-?\d+:ADC=-?\d+".format(
                            index
                        )
                    ),
                )
                for index in range(3)
            )

        for marker in trailing:
            patterns.append((marker, re.compile(re.escape(marker))))

    patterns.append(("P2SMART:PASS", re.compile(r"P2SMART:PASS\r?\n")))
    return tuple(patterns)


def parse_smartpins(
    text: str, expected_stages: Optional[Iterable[str]] = DEFAULT_STAGES
) -> Dict[str, object]:
    """Validate one reset of the P2 Smart Pin marker/data protocol.

    ``expected_stages`` must come from the exact image configuration during
    HIL execution.  Passing ``None`` accepts the image's CAPS line while still
    requiring GPIO, which is useful when inspecting an already captured log.
    """

    lines = [line.strip() for line in text.replace("\r", "\n").split("\n")]
    failures: List[Dict[str, str]] = []
    errors: List[str] = []
    duplicates: List[str] = []
    positions: List[int] = []

    for line in lines:
        for label, pattern in FAILURE_PATTERNS:
            if pattern.search(line):
                failures.append({"kind": label, "line": line})
                break

    header_markers = ("P2SMART:BEGIN", WIRING_MARKER)
    for marker in header_markers:
        found = _line_positions(lines, marker)
        if len(found) != 1:
            if not found:
                errors.append("missing {}".format(marker))
            else:
                duplicates.append(marker)
        else:
            positions.append(found[0])

    cap_lines = [
        (index, line) for index, line in enumerate(lines)
        if line.startswith("P2SMART:CAPS=")
    ]
    capabilities: Tuple[str, ...] = ()
    if len(cap_lines) != 1:
        errors.append("expected exactly one P2SMART:CAPS line")
    else:
        cap_position, cap_line = cap_lines[0]
        positions.append(cap_position)
        capabilities = tuple(cap_line.split("=", 1)[1].split(","))
        if not capabilities or any(stage not in STAGE_ORDER for stage in capabilities):
            errors.append("CAPS contains an unknown or empty stage")
        if tuple(stage for stage in STAGE_ORDER if stage in capabilities) != capabilities:
            errors.append("CAPS stages are out of canonical order")
        if "GPIO" not in capabilities:
            errors.append("CAPS must include GPIO")

    if expected_stages is None:
        expected = capabilities
    else:
        expected = tuple(expected_stages)
        if any(stage not in STAGE_ORDER for stage in expected):
            errors.append("expected_stages contains an unknown stage")
        if expected != capabilities:
            errors.append(
                "CAPS {} does not match expected {}".format(
                    ",".join(capabilities), ",".join(expected)
                )
            )

    stage_results: Dict[str, Dict[str, object]] = {}
    for stage in STAGE_ORDER:
        fixed = STAGE_FIXED_MARKERS[stage]
        fixed_positions: Dict[str, int] = {}
        present = any(_line_positions(lines, marker) for marker in fixed)
        pattern = SAMPLE_PATTERNS.get(stage)
        samples = [] if pattern is None else [
            (index, match)
            for index, line in enumerate(lines)
            for match in [pattern.fullmatch(line)]
            if match is not None
        ]
        present = present or bool(samples)

        if stage not in capabilities:
            if present:
                errors.append("{} markers present but stage is absent from CAPS".format(stage))
            continue

        for marker in fixed:
            found = _line_positions(lines, marker)
            if not found:
                errors.append("missing {}".format(marker))
            elif len(found) != 1:
                duplicates.append(marker)
            else:
                fixed_positions[marker] = found[0]

        sample_errors: List[str] = []
        if stage in SAMPLE_VALIDATORS:
            sample_errors = SAMPLE_VALIDATORS[stage](samples)
            errors.extend(sample_errors)

        sample_positions = [position for position, match in samples]
        if all(marker in fixed_positions for marker in fixed):
            stage_positions = [fixed_positions[fixed[0]]]
            stage_positions.extend(sample_positions)
            stage_positions.extend(fixed_positions[marker] for marker in fixed[1:])
        else:
            stage_positions = list(fixed_positions.values()) + sample_positions
            stage_positions.sort()

        positions.extend(stage_positions)
        stage_results[stage] = {
            "complete": not sample_errors and all(
                len(_line_positions(lines, marker)) == 1 for marker in fixed
            ),
            "sample_count": len(samples),
            "errors": sample_errors,
        }

    final = _line_positions(lines, "P2SMART:PASS")
    if len(final) != 1:
        if not final:
            errors.append("missing P2SMART:PASS")
        else:
            duplicates.append("P2SMART:PASS")
    else:
        positions.append(final[0])

    order_valid = positions == sorted(positions)
    if not order_valid:
        errors.append("protocol markers are out of order")

    complete = not errors and not duplicates and not failures and order_valid
    return {
        "complete": complete,
        "capabilities": list(capabilities),
        "expected_stages": list(expected),
        "stages": stage_results,
        "errors": errors,
        "duplicates": duplicates,
        "failures": failures,
        "order_valid": order_valid,
        "reset_count": lines.count("P2SMART:BEGIN"),
    }
