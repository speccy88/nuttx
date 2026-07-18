#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Strict evidence protocol for P2 raw microSD read benchmarks.

The target reports integer bytes/second.  This module deliberately uses the
decimal storage-performance convention (1 MB/s = 1,000,000 B/s), while also
including binary MiB/s values in its parsed summary.  A challenge pass means
*every* measured pass is strictly faster than 41,000,000 B/s; a peak equal to
the threshold is not sufficient.  This clears evanh's published 40,028-KiB/s
(40,988,672-B/s) result rather than merely rounding it to 40 MB/s.  The record
campaign is seven 256-MiB timed passes.  The target hashes the exact bytes
returned by each timed read call after that call's timing interval, so
verification does not require a second or potentially different transfer.
"""

import re
from typing import Dict, List, Optional, Tuple

PROTOCOL_VERSION = 2
DEVICE = "/dev/mmcsd0"
SECTOR_SIZE = 512
THRESHOLD_BPS = 41_000_000
DEFAULT_BYTES = 256 * 1024 * 1024
DEFAULT_PASSES = 7
MIN_BYTES = 16 * 1024 * 1024
MAX_BYTES = 1024 * 1024 * 1024
MIN_PASSES = 3
MAX_PASSES = 31

_SEQUENCE_RE = re.compile(r"[0-9A-F]{8}")
_BEGIN_RE = re.compile(
    r"P2SDBENCH:BEGIN:VERSION=(?P<version>[1-9][0-9]*):"
    r"MODE=RAW:OP=READ:SEQ=(?P<sequence>[0-9A-F]{8}):"
    r"DEV=(?P<device>/dev/mmcsd0):BYTES=(?P<bytes>[1-9][0-9]*):"
    r"PASSES=(?P<passes>[1-9][0-9]*):"
    r"THRESHOLD_BPS=(?P<threshold>[1-9][0-9]*)"
)
_CONFIG_RE = re.compile(
    r"P2SDBENCH:CONFIG:SYSCLK_HZ=(?P<sysclk_hz>[1-9][0-9]*):"
    r"BUS=(?P<bus>SPI1|SDIO4):"
    r"BUS_WIDTH_BITS=(?P<bus_width_bits>[14]):"
    r"REQUESTED_BUS_CLOCK_HZ=(?P<requested_bus_clock_hz>[1-9][0-9]*):"
    r"BUS_CLOCK_HZ=(?P<bus_clock_hz>[1-9][0-9]*):"
    r"ACTIVE_DIVISOR=(?P<active_divisor>[1-9][0-9]*):"
    r"RAW_CEILING_BPS=(?P<raw_ceiling_bps>[1-9][0-9]*):"
    r"HIGH_SPEED=(?P<high_speed>[01]):"
    r"OVERCLOCKED=(?P<overclocked>[01]):"
    r"PHASE_CALIBRATED=(?P<phase_calibrated>[01]):"
    r"RX_MODE=(?P<rx_mode>NA|ASYNC|SYNC):"
    r"RX_LAG=(?P<rx_lag>[0-9]+):"
    r"PAYLOAD_CRC16=(?P<payload_crc16>CHECKED|UNCHECKED):"
    r"CMD_CRC7=(?P<cmd_crc7>CHECKED|INIT_ONLY|UNCHECKED):"
    r"HIL_REQUIRED=(?P<hil_required>[01]):"
    r"BUFFER_BYTES=(?P<buffer_bytes>[1-9][0-9]*):"
    r"DRIVER=(?P<driver>[A-Z0-9][A-Z0-9_-]{0,63}):"
    r"BUILD=(?P<build>[A-Za-z0-9][A-Za-z0-9._+-]{0,95})"
)
_GEOMETRY_RE = re.compile(
    r"P2SDBENCH:GEOMETRY:PHASE=(?P<phase>BEFORE|AFTER):"
    r"SECTORS=(?P<sectors>[1-9][0-9]*):"
    r"SECTOR_SIZE=(?P<sector_size>[1-9][0-9]*):"
    r"MEDIA_CHANGED=(?P<media_changed>[01])"
)
_TIMER_RE = re.compile(
    r"P2SDBENCH:TIMER:SOURCE=P2_GETCT:"
    r"FREQUENCY_HZ=(?P<frequency_hz>[1-9][0-9]*):"
    r"RESOLUTION_CYCLES=(?P<resolution_cycles>[1-9][0-9]*):"
    r"SCOPE=READ_CALLS:VERIFY=HASH_TIMED_BYTES"
)
_BASELINE_RE = re.compile(
    r"P2SDBENCH:BASELINE:MODE=RAW:OP=READ:"
    r"VERIFY=(?P<verification>CRC16|UNCHECKED):"
    r"BYTES=(?P<bytes>[1-9][0-9]*):FNV1A=(?P<fnv1a>[0-9A-F]{8}):"
    r"SEQ=(?P<sequence>[0-9A-F]{8})"
)
_RESULT_RE = re.compile(
    r"P2SDBENCH:RESULT:MODE=RAW:OP=READ:PASS=(?P<pass>[1-9][0-9]*):"
    r"BYTES=(?P<bytes>[1-9][0-9]*):"
    r"CYCLES=(?P<cycles>[1-9][0-9]*):"
    r"USEC=(?P<usec>[1-9][0-9]*):"
    r"BPS=(?P<bps>[1-9][0-9]*):FNV1A=(?P<fnv1a>[0-9A-F]{8}):"
    r"SEQ=(?P<sequence>[0-9A-F]{8})"
)
_PASS_RE = re.compile(
    r"P2SDBENCH:PASS:MODE=RAW:OP=READ:SEQ=(?P<sequence>[0-9A-F]{8}):"
    r"PASSES=(?P<passes>[1-9][0-9]*):BYTES=(?P<bytes>[1-9][0-9]*):"
    r"MIN_BPS=(?P<min_bps>[1-9][0-9]*):"
    r"MEDIAN_BPS=(?P<median_bps>[1-9][0-9]*):"
    r"MAX_BPS=(?P<max_bps>[1-9][0-9]*):"
    r"THRESHOLD_BPS=(?P<threshold>[1-9][0-9]*)"
)
_DONE_RE = re.compile(r"P2SDBENCH:DONE:SEQ=(?P<sequence>[0-9A-F]{8})")
_FAIL_RE = re.compile(
    r"P2SDBENCH:FAIL:STAGE=(?P<stage>[A-Z0-9][A-Z0-9_-]{0,63}):"
    r"CODE=(?P<code>-?[0-9]+)"
)
_STORAGE_FAIL_RE = re.compile(r"P2STORAGE:FAIL:[A-Z0-9_-]+:-?[0-9]+")
_GENERIC_FAILURE_PATTERNS = (
    ("PANIC", re.compile(r"\bPANIC\b", re.IGNORECASE)),
    ("assertion", re.compile(r"\bASSERT(?:ION)?\b", re.IGNORECASE)),
    ("stack overflow", re.compile(r"STACK\s+OVERFLOW", re.IGNORECASE)),
    ("unexpected IRQ", re.compile(r"UNEXPECTED\s+IRQ", re.IGNORECASE)),
    ("register dump", re.compile(r"REGISTER\s+DUMP", re.IGNORECASE)),
)


def normalize_sequence(value: object) -> str:
    """Return an exact eight-digit uppercase hexadecimal run nonce."""

    if isinstance(value, int):
        if 0 <= value <= 0xFFFFFFFF:
            return "{:08X}".format(value)
        raise ValueError("benchmark sequence integer must fit in 32 bits")
    if not isinstance(value, str) or _SEQUENCE_RE.fullmatch(value) is None:
        raise ValueError("benchmark sequence must be exactly 8 uppercase hex digits")
    return value


def validate_parameters(byte_count: int, passes: int) -> None:
    """Reject benchmark sizes that can produce misleading challenge results."""

    if byte_count < MIN_BYTES:
        raise ValueError("benchmark byte count must be at least {}".format(MIN_BYTES))
    if byte_count > MAX_BYTES:
        raise ValueError("benchmark byte count must be at most {}".format(MAX_BYTES))
    if byte_count % SECTOR_SIZE != 0:
        raise ValueError("benchmark byte count must be a multiple of 512")
    if not MIN_PASSES <= passes <= MAX_PASSES or passes % 2 == 0:
        raise ValueError(
            "benchmark passes must be an odd integer in {}..{}".format(
                MIN_PASSES, MAX_PASSES
            )
        )


def command_line(sequence: object, byte_count: int, passes: int) -> str:
    """Return the exact non-destructive NSH command without its CR."""

    sequence_text = normalize_sequence(sequence)
    validate_parameters(byte_count, passes)
    return "p2storage sd-benchmark-read {} {} {}".format(
        sequence_text, byte_count, passes
    )


def command_bytes(sequence: object, byte_count: int, passes: int) -> bytes:
    return (command_line(sequence, byte_count, passes) + "\r").encode("ascii")


def done_marker(sequence: object) -> str:
    return "P2SDBENCH:DONE:SEQ={}".format(normalize_sequence(sequence))


def transcript_parameters(text: str) -> Tuple[str, int, int]:
    """Extract the unique BEGIN tuple for strict offline re-validation."""

    found = [
        _BEGIN_RE.fullmatch(line.strip())
        for line in text.replace("\r", "\n").split("\n")
    ]
    matches = [item for item in found if item is not None]
    if len(matches) != 1:
        raise ValueError("transcript must contain exactly one P2SDBENCH BEGIN line")
    match = matches[0]
    return (
        normalize_sequence(match.group("sequence")),
        int(match.group("bytes")),
        int(match.group("passes")),
    )


def _rate_bps(byte_count: int, cycles: int, timer_hz: int) -> int:
    """Use the target contract's cycle-counter floor division."""

    return byte_count * timer_hz // cycles


def _single_match(
    matches: List[Tuple[int, re.Match]],
    label: str,
    errors: List[str],
    duplicates: List[str],
) -> Optional[Tuple[int, re.Match]]:
    if not matches:
        errors.append("missing {}".format(label))
        return None
    if len(matches) != 1:
        duplicates.append(label)
        return None
    return matches[0]


def parse_benchmark(
    text: str,
    sequence: object,
    byte_count: int = DEFAULT_BYTES,
    passes: int = DEFAULT_PASSES,
    threshold_bps: int = THRESHOLD_BPS,
) -> Dict[str, object]:
    """Validate one complete raw-read transcript and derive its evidence.

    Only exact, full protocol lines count.  Other NSH/boot output is allowed,
    but any unrecognized ``P2SDBENCH:`` line is rejected so a newer or drifted
    target cannot accidentally satisfy this parser.
    """

    sequence_text = normalize_sequence(sequence)
    validate_parameters(byte_count, passes)
    if threshold_bps != THRESHOLD_BPS:
        raise ValueError(
            "challenge threshold is locked to {} B/s".format(THRESHOLD_BPS)
        )

    lines = [line.strip() for line in text.replace("\r", "\n").split("\n")]
    protocol_lines = [
        (index, line)
        for index, line in enumerate(lines)
        if line.startswith("P2SDBENCH:")
    ]
    errors: List[str] = []
    duplicates: List[str] = []
    failures: List[Dict[str, object]] = []
    recognized = set()

    def matches(pattern: re.Pattern) -> List[Tuple[int, re.Match]]:
        return [
            (index, match)
            for index, line in protocol_lines
            for match in [pattern.fullmatch(line)]
            if match is not None
        ]

    begin = _single_match(matches(_BEGIN_RE), "BEGIN", errors, duplicates)
    config = _single_match(matches(_CONFIG_RE), "CONFIG", errors, duplicates)
    baseline = _single_match(matches(_BASELINE_RE), "BASELINE", errors, duplicates)
    timer = _single_match(matches(_TIMER_RE), "TIMER", errors, duplicates)
    final = _single_match(matches(_PASS_RE), "PASS", errors, duplicates)
    done = _single_match(matches(_DONE_RE), "DONE", errors, duplicates)

    before_candidates = [
        item for item in matches(_GEOMETRY_RE) if item[1].group("phase") == "BEFORE"
    ]
    after_candidates = [
        item for item in matches(_GEOMETRY_RE) if item[1].group("phase") == "AFTER"
    ]
    before = _single_match(before_candidates, "GEOMETRY BEFORE", errors, duplicates)
    after = _single_match(after_candidates, "GEOMETRY AFTER", errors, duplicates)

    fixed = (begin, config, before, baseline, timer, after, final, done)
    for item in fixed:
        if item is not None:
            recognized.add(item[0])

    if begin is not None:
        fields = begin[1].groupdict()
        expected = {
            "version": str(PROTOCOL_VERSION),
            "sequence": sequence_text,
            "device": DEVICE,
            "bytes": str(byte_count),
            "passes": str(passes),
            "threshold": str(threshold_bps),
        }
        for name, value in expected.items():
            if fields[name] != value:
                errors.append(
                    "BEGIN {} is {}, expected {}".format(name, fields[name], value)
                )

    config_values: Dict[str, object] = {}
    if config is not None:
        fields = config[1].groupdict()
        config_values = {
            "sysclk_hz": int(fields["sysclk_hz"]),
            "bus": fields["bus"],
            "bus_width_bits": int(fields["bus_width_bits"]),
            "requested_bus_clock_hz": int(fields["requested_bus_clock_hz"]),
            "bus_clock_hz": int(fields["bus_clock_hz"]),
            "active_divisor": int(fields["active_divisor"]),
            "raw_ceiling_bps": int(fields["raw_ceiling_bps"]),
            "high_speed": bool(int(fields["high_speed"])),
            "overclocked": bool(int(fields["overclocked"])),
            "phase_calibrated": bool(int(fields["phase_calibrated"])),
            "rx_mode": fields["rx_mode"],
            "rx_lag": int(fields["rx_lag"]),
            "payload_crc16": fields["payload_crc16"],
            "cmd_crc7": fields["cmd_crc7"],
            "hil_required": bool(int(fields["hil_required"])),
            "buffer_bytes": int(fields["buffer_bytes"]),
            "driver": fields["driver"],
            "build": fields["build"],
        }
        if config_values["bus_clock_hz"] > config_values["sysclk_hz"]:
            errors.append("bus clock exceeds system clock")
        if config_values["bus_clock_hz"] > config_values["requested_bus_clock_hz"]:
            errors.append("active bus clock exceeds requested bus clock")
        if config_values["bus"] != "SDIO4":
            errors.append("record bus is not native SDIO4")
        expected_width = 4 if config_values["bus"] == "SDIO4" else 1
        if config_values["bus_width_bits"] != expected_width:
            errors.append("bus label and width disagree")
        if (
            config_values["sysclk_hz"] // config_values["active_divisor"]
            != config_values["bus_clock_hz"]
        ):
            errors.append("active divisor does not produce reported bus clock")
        expected_raw = (
            config_values["bus_clock_hz"] * config_values["bus_width_bits"] // 8
        )
        if config_values["raw_ceiling_bps"] != expected_raw:
            errors.append("raw bus ceiling is inconsistent")
        if config_values["raw_ceiling_bps"] <= threshold_bps:
            errors.append("raw bus ceiling cannot beat the challenge threshold")
        expected_overclock = config_values["bus_clock_hz"] > (
            50_000_000 if config_values["high_speed"] else 25_000_000
        )
        if config_values["overclocked"] != expected_overclock:
            errors.append("overclock telemetry is inconsistent")
        if config_values["bus"] == "SDIO4":
            expected_hil = config_values["requested_bus_clock_hz"] > 25_000_000
            if config_values["hil_required"] != expected_hil:
                errors.append("native HIL_REQUIRED telemetry is inconsistent")
            elif not config_values["hil_required"]:
                errors.append("native record did not declare HIL_REQUIRED=1")
            if not config_values["high_speed"]:
                errors.append("native four-bit record did not negotiate high speed")
            if (
                config_values["bus_clock_hz"] > 25_000_000
                and not config_values["phase_calibrated"]
            ):
                errors.append("native high-rate receive phase was not calibrated")
            if config_values["rx_mode"] == "NA":
                errors.append("native receive mode is not reported")
            if config_values["cmd_crc7"] != "CHECKED":
                errors.append("native command CRC7 is not checked")
        elif config_values["rx_mode"] != "NA":
            errors.append("SPI profile reported a native receive mode")
        if config_values["buffer_bytes"] % SECTOR_SIZE != 0:
            errors.append("buffer size is not sector aligned")
        if config_values["buffer_bytes"] > byte_count:
            errors.append("buffer size exceeds benchmark byte count")

    geometry_values: Dict[str, object] = {}
    if before is not None and after is not None:
        before_fields = before[1].groupdict()
        after_fields = after[1].groupdict()
        before_tuple = (
            int(before_fields["sectors"]),
            int(before_fields["sector_size"]),
            int(before_fields["media_changed"]),
        )
        after_tuple = (
            int(after_fields["sectors"]),
            int(after_fields["sector_size"]),
            int(after_fields["media_changed"]),
        )
        geometry_values = {
            "sectors": before_tuple[0],
            "sector_size": before_tuple[1],
            "capacity_bytes": before_tuple[0] * before_tuple[1],
            "media_changed_before": bool(before_tuple[2]),
            "media_changed_after": bool(after_tuple[2]),
            "stable": before_tuple == after_tuple,
        }
        if before_tuple[1] != SECTOR_SIZE:
            errors.append("raw SD sector size is not 512")
        if before_tuple[2] != 0 or after_tuple[2] != 0:
            errors.append("media-changed flag was asserted")
        if before_tuple != after_tuple:
            errors.append("SD geometry changed during the benchmark")
        if byte_count > before_tuple[0] * before_tuple[1]:
            errors.append("benchmark byte count exceeds SD capacity")

    baseline_values: Dict[str, object] = {}
    if baseline is not None:
        fields = baseline[1].groupdict()
        baseline_values = {
            "verification": fields["verification"],
            "bytes": int(fields["bytes"]),
            "fnv1a": fields["fnv1a"],
            "sequence": fields["sequence"],
        }
        if baseline_values["bytes"] != byte_count:
            errors.append("CRC16 baseline has wrong byte count")
        if baseline_values["sequence"] != sequence_text:
            errors.append("CRC16 baseline has stale sequence")
        if baseline_values["verification"] != "CRC16":
            errors.append("baseline transfer did not verify four-lane CRC16")

    timer_values: Dict[str, object] = {}
    if timer is not None:
        frequency_hz = int(timer[1].group("frequency_hz"))
        resolution_cycles = int(timer[1].group("resolution_cycles"))
        timer_values = {
            "source": "P2_GETCT",
            "frequency_hz": frequency_hz,
            "resolution_cycles": resolution_cycles,
            "scope": "READ_CALLS",
            "verification": "HASH_TIMED_BYTES",
        }
        if resolution_cycles != 1:
            errors.append("GETCT resolution is not one cycle")
        if config_values and frequency_hz != config_values["sysclk_hz"]:
            errors.append("GETCT frequency does not match system clock")

    result_matches = matches(_RESULT_RE)
    result_by_pass: Dict[int, List[Tuple[int, re.Match]]] = {}
    for item in result_matches:
        pass_index = int(item[1].group("pass"))
        result_by_pass.setdefault(pass_index, []).append(item)

    measurements: List[Dict[str, object]] = []
    for pass_index in range(1, passes + 1):
        candidates = result_by_pass.get(pass_index, [])
        item = _single_match(
            candidates, "RESULT pass {}".format(pass_index), errors, duplicates
        )
        if item is None:
            continue
        recognized.add(item[0])
        fields = item[1].groupdict()
        measured_bytes = int(fields["bytes"])
        cycles = int(fields["cycles"])
        usec = int(fields["usec"])
        reported_bps = int(fields["bps"])
        timer_hz = int(timer_values.get("frequency_hz", 1))
        calculated_bps = _rate_bps(measured_bytes, cycles, timer_hz)
        calculated_usec = (cycles * 1_000_000 + timer_hz - 1) // timer_hz
        if fields["sequence"] != sequence_text:
            errors.append("RESULT pass {} has stale sequence".format(pass_index))
        if measured_bytes != byte_count:
            errors.append("RESULT pass {} has wrong byte count".format(pass_index))
        if reported_bps != calculated_bps:
            errors.append(
                "RESULT pass {} BPS is {}, recomputed {}".format(
                    pass_index, reported_bps, calculated_bps
                )
            )
        if usec != calculated_usec:
            errors.append(
                "RESULT pass {} USEC is {}, recomputed {}".format(
                    pass_index, usec, calculated_usec
                )
            )
        if reported_bps <= threshold_bps:
            errors.append(
                "RESULT pass {} did not strictly exceed {} B/s".format(
                    pass_index, threshold_bps
                )
            )
        if config_values and reported_bps > config_values["raw_ceiling_bps"]:
            errors.append(
                "RESULT pass {} exceeds the raw bus ceiling".format(pass_index)
            )
        if timer_values and cycles < timer_values["resolution_cycles"]:
            errors.append(
                "RESULT pass {} cycle count is below timer resolution".format(
                    pass_index
                )
            )
        measurements.append(
            {
                "pass": pass_index,
                "bytes": measured_bytes,
                "cycles": cycles,
                "usec": usec,
                "bps": reported_bps,
                "mb_per_s": reported_bps / 1_000_000.0,
                "mib_per_s": reported_bps / float(1024 * 1024),
                "raw_bus_efficiency": (
                    reported_bps / config_values["raw_ceiling_bps"]
                    if config_values
                    else None
                ),
                "fnv1a": fields["fnv1a"],
            }
        )

    unexpected_result_passes = sorted(
        pass_index
        for pass_index in result_by_pass
        if pass_index < 1 or pass_index > passes
    )
    for pass_index in unexpected_result_passes:
        errors.append("unexpected RESULT pass {}".format(pass_index))

    hashes = {str(item["fnv1a"]) for item in measurements}
    if len(hashes) > 1:
        errors.append("raw data FNV1A changed between passes")
    if baseline_values:
        mismatches = [
            int(item["pass"])
            for item in measurements
            if item["fnv1a"] != baseline_values["fnv1a"]
        ]
        if mismatches:
            errors.append(
                "timed hashes differ from CRC16 baseline on passes {}".format(
                    ",".join(str(item) for item in mismatches)
                )
            )

    aggregates: Dict[str, object] = {}
    if len(measurements) == passes:
        rates = sorted(int(item["bps"]) for item in measurements)
        aggregates = {
            "min_bps": rates[0],
            "median_bps": rates[len(rates) // 2],
            "max_bps": rates[-1],
            "min_mb_per_s": rates[0] / 1_000_000.0,
            "median_mb_per_s": rates[len(rates) // 2] / 1_000_000.0,
            "max_mb_per_s": rates[-1] / 1_000_000.0,
            "min_mib_per_s": rates[0] / float(1024 * 1024),
            "median_mib_per_s": rates[len(rates) // 2] / float(1024 * 1024),
            "max_mib_per_s": rates[-1] / float(1024 * 1024),
        }

    if final is not None:
        fields = final[1].groupdict()
        expected_final = {
            "sequence": sequence_text,
            "passes": str(passes),
            "bytes": str(byte_count),
            "threshold": str(threshold_bps),
        }
        for name, value in expected_final.items():
            if fields[name] != value:
                errors.append(
                    "PASS {} is {}, expected {}".format(name, fields[name], value)
                )
        if aggregates:
            for name in ("min_bps", "median_bps", "max_bps"):
                if int(fields[name]) != aggregates[name]:
                    errors.append(
                        "PASS {} is {}, recomputed {}".format(
                            name, fields[name], aggregates[name]
                        )
                    )

    if done is not None and done[1].group("sequence") != sequence_text:
        errors.append("DONE has stale sequence")

    for index, match in matches(_FAIL_RE):
        recognized.add(index)
        failures.append(
            {
                "kind": "P2 SD benchmark failure",
                "stage": match.group("stage"),
                "code": int(match.group("code")),
                "line": lines[index],
            }
        )
    for index, line in enumerate(lines):
        if _STORAGE_FAIL_RE.fullmatch(line):
            failures.append({"kind": "P2 storage failure", "line": line})
            continue
        for kind, pattern in _GENERIC_FAILURE_PATTERNS:
            if pattern.search(line):
                failures.append({"kind": kind, "line": line})
                break

    unexpected = [line for index, line in protocol_lines if index not in recognized]
    errors.extend("unexpected protocol line: {}".format(line) for line in unexpected)

    if not duplicates and all(item is not None for item in fixed):
        ordered_positions = [
            begin[0],
            config[0],
            before[0],
            baseline[0],
            timer[0],
        ]
        ordered_positions.extend(
            result_by_pass[index][0][0]
            for index in range(1, passes + 1)
            if len(result_by_pass.get(index, [])) == 1
        )
        ordered_positions.extend([after[0], final[0], done[0]])
        order_valid = ordered_positions == sorted(ordered_positions)
    else:
        order_valid = False
    if not order_valid:
        errors.append("benchmark protocol markers are out of order")

    complete = (
        not errors
        and not duplicates
        and not failures
        and order_valid
        and len(measurements) == passes
        and bool(aggregates)
        and int(aggregates["min_bps"]) > threshold_bps
    )
    return {
        "complete": complete,
        "status": "PASS" if complete else "FAIL",
        "protocol_version": PROTOCOL_VERSION,
        "mode": "RAW",
        "operation": "READ",
        "sequence": sequence_text,
        "device": DEVICE,
        "command": command_line(sequence_text, byte_count, passes),
        "bytes_per_pass": byte_count,
        "passes": passes,
        "threshold_bps": threshold_bps,
        "threshold_mb_per_s": threshold_bps / 1_000_000.0,
        "threshold_mib_per_s": threshold_bps / float(1024 * 1024),
        "strictly_greater_than_threshold": True,
        "config": config_values,
        "geometry": geometry_values,
        "baseline": baseline_values,
        "timer": timer_values,
        "measurements": measurements,
        "data_fnv1a": next(iter(hashes)) if len(hashes) == 1 else None,
        "aggregates": aggregates,
        "errors": errors,
        "duplicates": duplicates,
        "failures": failures,
        "unexpected": unexpected,
        "order_valid": order_valid,
    }


def first_error(result: Dict[str, object]) -> str:
    details: List[str] = []
    details.extend(str(item) for item in result.get("errors", ()))
    details.extend("duplicate {}".format(item) for item in result.get("duplicates", ()))
    details.extend(str(item.get("line", item)) for item in result.get("failures", ()))
    return "; ".join(details) or "incomplete SD benchmark response"
