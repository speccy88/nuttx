#!/usr/bin/env python3
"""Strict parser and command helpers for the P2 external-PSRAM HIL app."""

import re
from functools import lru_cache
from typing import Dict, Iterator, List, Tuple


PSRAM_SIZE = 32 * 1024 * 1024
PSRAM_CHIP_COUNT = 4
PSRAM_CHIP_SIZE = 8 * 1024 * 1024
PSRAM_WORD_SIZE = 4
PSRAM_TIMEOUT_ERRNO = 110
PSRAM_PROGRESS_STEP = 4 * 1024 * 1024
PSRAM_CE_LIMIT_CYCLES = 1440
PSRAM_MAX_REQUEST = 64 * 1024
PSRAM_QPI_CLOCK_HZ = 5_000_000
PSRAM_BULK_QPI_CLOCK_HZ = 90_000_000
PSRAM_TICK_USEC = 10_000
PSRAM_DEFAULT_TIMEOUT_TICKS = 500
PSRAM_CANCEL_GRACE_TICKS = 100
PSRAM_RANDOM_COUNT = 1024
PSRAM_CONCURRENT_REQUESTS = 64
PSRAM_CONCURRENT_BYTES = PSRAM_CONCURRENT_REQUESTS * (32 * 1024)
PSRAM_COUNTER_HZ = 180_000_000
PSRAM_CONCURRENT_MAX_CYCLES = 5 * PSRAM_COUNTER_HZ
PSRAM_WORK_RATE_SHIFT = 20
PSRAM_TIMEOUT_BYTES = 32 * 1024
PSRAM_TIMEOUT_DEADLINE_TICKS = 1
PSRAM_TIMEOUT_FAULT = "COOPERATIVE_STALL"
PSRAM_FNV_OFFSET = 2166136261
PSRAM_FNV_PRIME = 16777619

FAILURE_PATTERNS = (
    ("P2PSRAM failure", re.compile(r"P2PSRAM:FAIL:[A-Z0-9-]+:[0-9]+")),
)


def normalize_sequence(value: str) -> str:
    """Return the exact eight-uppercase-hex target nonce."""

    if not re.fullmatch(r"[0-9A-F]{8}", value or ""):
        raise ValueError("PSRAM sequence must be exactly eight uppercase hex digits")
    return value


def command_bytes(sequence: str) -> bytes:
    return ("p2psram {}\r".format(normalize_sequence(sequence))).encode("ascii")


def pattern_byte(sequence: str, address: int) -> int:
    """Return the byte required at one address by the locked target pattern."""

    sequence_value = int(normalize_sequence(sequence), 16)
    if address < 0 or address >= PSRAM_SIZE:
        raise ValueError("PSRAM pattern address is outside the 32-MiB device")
    sequence_byte = (sequence_value >> ((address & 3) * 8)) & 0xFF
    return (
        sequence_byte
        + address * 37
        + (address >> 8) * 17
        + (address >> 16)
        + (address >> 24) * 0x5B
    ) & 0xFF


def pattern_stream(sequence: str, address: int, length: int) -> Iterator[int]:
    """Yield the locked byte pattern using the target's recurrence."""

    sequence_value = int(normalize_sequence(sequence), 16)
    if address < 0 or length < 0 or address + length > PSRAM_SIZE:
        raise ValueError("PSRAM pattern range is outside the 32-MiB device")

    sequence_bytes = tuple(
        (sequence_value >> (index * 8)) & 0xFF for index in range(4)
    )
    address_byte = (
        address * 37
        + (address >> 8) * 17
        + (address >> 16)
        + (address >> 24) * 0x5B
    ) & 0xFF

    for _ in range(length):
        yield (sequence_bytes[address & 3] + address_byte) & 0xFF
        address += 1
        address_byte = (address_byte + 37) & 0xFF
        if address & 0xFF == 0:
            address_byte = (address_byte + 17) & 0xFF
        if address & 0xFFFF == 0:
            address_byte = (address_byte + 1) & 0xFF
        if address & 0xFFFFFF == 0:
            address_byte = (address_byte + 0x5B) & 0xFF


@lru_cache(maxsize=32)
def expected_fnv1a(sequence: str) -> int:
    """Compute the nonce-specific FNV-1a for one complete 32-MiB pass."""

    fnv = PSRAM_FNV_OFFSET
    prime = PSRAM_FNV_PRIME
    mask = 0xFFFFFFFF

    for value in pattern_stream(sequence, 0, PSRAM_SIZE):
        fnv = ((fnv ^ value) * prime) & mask

    return fnv


def _line(pattern: str) -> re.Pattern:
    return re.compile(r"^" + pattern + r"\r?(?=\n)", re.MULTILINE)


def marker_patterns(sequence: str) -> Tuple[Tuple[str, re.Pattern], ...]:
    """Markers root's HIL runner must observe in this exact order."""

    sequence = normalize_sequence(sequence)
    return (
        (
            "P2PSRAM begin",
            _line(
                r"(?:nsh> (?:\x1b\[K)?)?P2PSRAM:BEGIN:SEQUENCE="
                + re.escape(sequence)
            ),
        ),
        (
            "P2PSRAM geometry",
            _line(
                r"P2PSRAM:GEOMETRY:SIZE=33554432:CHIPS=4:"
                r"CHIP_SIZE=8388608:WORD=4:MAX_REQUEST=65536:COG=[0-7]"
            ),
        ),
        (
            "P2PSRAM exact profile",
            _line(
                r"P2PSRAM:PROFILE:MAX_REQUEST=65536:QPI_HZ=5000000:"
                r"BULK_QPI_HZ=90000000:TICK_USEC=10000:TIMEOUT_TICKS=500:"
                r"CANCEL_GRACE_TICKS=100"
            ),
        ),
        ("P2PSRAM walking bits", _line(r"P2PSRAM:WALKING:PASS:BITS=32")),
        ("P2PSRAM address lines", _line(r"P2PSRAM:ADDRESS:PASS:LINES=23")),
        ("P2PSRAM boundaries", _line(r"P2PSRAM:BOUNDARY:PASS:COUNT=5")),
        (
            "P2PSRAM random transfers",
            _line(r"P2PSRAM:RANDOM:PASS:COUNT=1024"),
        ),
        (
            "P2PSRAM full coverage",
            _line(r"P2PSRAM:FULL:PASS:BYTES=33554432:FNV1A=[0-9A-F]{8}"),
        ),
        (
            "P2PSRAM throughput",
            _line(
                r"P2PSRAM:THROUGHPUT:WRITE_BPS=[0-9]+:READ_BPS=[0-9]+"
            ),
        ),
        (
            "P2PSRAM concurrent workload",
            _line(
                r"P2PSRAM:CONCURRENT:PASS:REQUESTS=64:BYTES=2097152:"
                r"WORK=[0-9]+:ELAPSED_CYCLES=[0-9]+:BASELINE_WORK=[0-9]+:"
                r"BASELINE_CYCLES=[0-9]+:COUNTER_HZ=180000000:"
                r"CPU_AVAILABLE_PERMILLE=[0-9]+:CPU_OCCUPANCY_PERMILLE=[0-9]+"
            ),
        ),
        (
            "P2PSRAM timeout",
            _line(
                r"P2PSRAM:TIMEOUT:PASS:RESULT=110:BYTES=32768:"
                r"DEADLINE_TICKS=1:FAULT=COOPERATIVE_STALL:TICK_USEC=10000"
            ),
        ),
        ("P2PSRAM recovery", _line(r"P2PSRAM:RECOVERY:PASS")),
        (
            "P2PSRAM CE timing",
            _line(
                r"P2PSRAM:CE_TIMING:PASS:MAX_CYCLES=[0-9]+:"
                r"LIMIT_CYCLES=1440"
            ),
        ),
        (
            "P2PSRAM final pass",
            _line(r"P2PSRAM:PASS:SEQUENCE=" + re.escape(sequence)),
        ),
    )


def _one(text: str, pattern: re.Pattern, label: str, errors: List[str]):
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        errors.append(
            "expected exactly one {} marker, found {}".format(
                label, len(matches)
            )
        )
        return None
    return matches[0]


def parse_psram(text: str, sequence: str) -> Dict[str, object]:
    """Validate one complete, nonce-bound, 32-MiB target transcript."""

    sequence = normalize_sequence(sequence)
    errors: List[str] = []
    positions: List[int] = []
    values: Dict[str, object] = {}
    matches = {}

    for label, pattern in marker_patterns(sequence):
        match = _one(text, pattern, label, errors)
        if match is not None:
            matches[label] = match
            positions.append(match.start())

    if positions != sorted(positions):
        errors.append("PSRAM markers were observed out of order")

    begin = matches.get("P2PSRAM begin")
    final = matches.get("P2PSRAM final pass")
    if begin is not None and final is not None and begin.start() < final.start():
        run_text = text[begin.start() : final.end()]
        run_offset = begin.start()
    else:
        run_text = text
        run_offset = 0

    for label, pattern in FAILURE_PATTERNS:
        match = pattern.search(run_text)
        if match is not None:
            errors.append("{} observed: {}".format(label, match.group(0)))

    geometry = re.search(
        r"P2PSRAM:GEOMETRY:SIZE=(\d+):CHIPS=(\d+):CHIP_SIZE=(\d+):"
        r"WORD=(\d+):MAX_REQUEST=(\d+):COG=(\d+)",
        run_text,
    )
    if geometry is not None:
        keys = ("size", "chips", "chip_size", "word", "max_request", "cog")
        values.update(zip(keys, (int(value) for value in geometry.groups())))
        expected = (
            PSRAM_SIZE,
            PSRAM_CHIP_COUNT,
            PSRAM_CHIP_SIZE,
            PSRAM_WORD_SIZE,
        )
        if tuple(values[key] for key in keys[:4]) != expected:
            errors.append(
                "PSRAM geometry does not describe four interleaved 8-MiB chips"
            )
        if values["max_request"] != PSRAM_MAX_REQUEST:
            errors.append("PSRAM maximum request does not match the locked profile")

    profile = re.search(
        r"P2PSRAM:PROFILE:MAX_REQUEST=(\d+):QPI_HZ=(\d+):"
        r"BULK_QPI_HZ=(\d+):TICK_USEC=(\d+):"
        r"TIMEOUT_TICKS=(\d+):CANCEL_GRACE_TICKS=(\d+)",
        run_text,
    )
    if profile is not None:
        profile_keys = (
            "profile_max_request",
            "qpi_hz",
            "bulk_qpi_hz",
            "tick_usec",
            "timeout_ticks",
            "cancel_grace_ticks",
        )
        values.update(
            zip(profile_keys, (int(value) for value in profile.groups()))
        )
        profile_expected = (
            PSRAM_MAX_REQUEST,
            PSRAM_QPI_CLOCK_HZ,
            PSRAM_BULK_QPI_CLOCK_HZ,
            PSRAM_TICK_USEC,
            PSRAM_DEFAULT_TIMEOUT_TICKS,
            PSRAM_CANCEL_GRACE_TICKS,
        )
        if tuple(values[key] for key in profile_keys) != profile_expected:
            errors.append("PSRAM runtime profile does not match the locked image")

    random_match = re.search(r"P2PSRAM:RANDOM:PASS:COUNT=(\d+)", run_text)
    if random_match is not None:
        values["random_count"] = int(random_match.group(1))
        if values["random_count"] != PSRAM_RANDOM_COUNT:
            errors.append("random transfer count does not match the locked profile")

    throughput = re.search(
        r"P2PSRAM:THROUGHPUT:WRITE_BPS=(\d+):READ_BPS=(\d+)", run_text
    )
    if throughput is not None:
        values["write_bps"], values["read_bps"] = (
            int(value) for value in throughput.groups()
        )
        if values["write_bps"] == 0 or values["read_bps"] == 0:
            errors.append("throughput must be measured and nonzero")

    concurrent = re.search(
        r"P2PSRAM:CONCURRENT:PASS:REQUESTS=(\d+):BYTES=(\d+):WORK=(\d+):"
        r"ELAPSED_CYCLES=(\d+):BASELINE_WORK=(\d+):"
        r"BASELINE_CYCLES=(\d+):COUNTER_HZ=(\d+):"
        r"CPU_AVAILABLE_PERMILLE=(\d+):CPU_OCCUPANCY_PERMILLE=(\d+)",
        run_text,
    )
    if concurrent is not None:
        (
            values["concurrent_requests"],
            values["concurrent_bytes"],
            values["concurrent_work"],
            values["concurrent_cycles"],
            values["baseline_work"],
            values["baseline_cycles"],
            values["counter_hz"],
            values["cpu_available_permille"],
            values["cpu_occupancy_permille"],
        ) = (int(value) for value in concurrent.groups())
        if (
            values["concurrent_requests"] != PSRAM_CONCURRENT_REQUESTS
            or values["concurrent_bytes"] != PSRAM_CONCURRENT_BYTES
            or values["counter_hz"] != PSRAM_COUNTER_HZ
        ):
            errors.append("concurrent workload does not match the locked batch")
        if (
            values["concurrent_work"] == 0
            or values["baseline_work"] == 0
            or not 0 < values["concurrent_cycles"] <= PSRAM_CONCURRENT_MAX_CYCLES
            or not 0 < values["baseline_cycles"] <= PSRAM_CONCURRENT_MAX_CYCLES
        ):
            errors.append("concurrent high-resolution workload evidence is invalid")
        service_rate = (
            values["concurrent_work"] << PSRAM_WORK_RATE_SHIFT
        ) // max(values["concurrent_cycles"], 1)
        baseline_rate = (
            values["baseline_work"] << PSRAM_WORK_RATE_SHIFT
        ) // max(values["baseline_cycles"], 1)
        expected_available = (
            min(1000, service_rate * 1000 // baseline_rate)
            if service_rate > 0 and baseline_rate > 0
            else 0
        )
        if values["cpu_available_permille"] != expected_available:
            errors.append("CPU availability does not match the measured work rates")
        if (
            not 0 < values["cpu_available_permille"] <= 1000
            or values["cpu_available_permille"]
            + values["cpu_occupancy_permille"]
            != 1000
        ):
            errors.append("CPU occupancy measurement is inconsistent")

    ce_timing = re.search(
        r"P2PSRAM:CE_TIMING:PASS:MAX_CYCLES=(\d+):LIMIT_CYCLES=(\d+)",
        run_text,
    )
    if ce_timing is not None:
        values["max_ce_cycles"], values["ce_limit_cycles"] = (
            int(value) for value in ce_timing.groups()
        )
        if not 0 < values["max_ce_cycles"] <= values["ce_limit_cycles"]:
            errors.append("measured CE-low interval exceeds the refresh limit")

    raw_progress = list(
        _line(r"P2PSRAM:PROGRESS:[^\r\n]*").finditer(run_text)
    )
    progress = list(
        _line(
            r"P2PSRAM:PROGRESS:SEQUENCE=([0-9A-F]{8}):(WRITE|READ)=(\d+)"
        ).finditer(run_text)
    )
    if len(raw_progress) != len(progress):
        errors.append("malformed or non-profile PSRAM progress marker observed")

    expected_progress = list(
        range(PSRAM_PROGRESS_STEP, PSRAM_SIZE + 1, PSRAM_PROGRESS_STEP)
    )
    progress_positions = {"WRITE": [], "READ": []}
    for direction in ("WRITE", "READ"):
        direction_matches = [
            match for match in progress if match.group(2) == direction
        ]
        observed = [int(match.group(3)) for match in direction_matches]
        observed_sequences = [match.group(1) for match in direction_matches]
        progress_positions[direction] = [
            run_offset + match.start() for match in direction_matches
        ]
        if observed_sequences != [sequence] * len(expected_progress):
            errors.append(
                "{} progress is not bound to this nonce".format(
                    direction.lower()
                )
            )
        if observed != expected_progress:
            errors.append(
                "{} progress does not prove one complete 32-MiB pass".format(
                    direction.lower()
                )
            )

    random_marker = matches.get("P2PSRAM random transfers")
    full_marker = matches.get("P2PSRAM full coverage")
    if random_marker is not None and full_marker is not None:
        progress_order = (
            [random_marker.start()]
            + progress_positions["WRITE"]
            + progress_positions["READ"]
            + [full_marker.start()]
        )
        if progress_order != sorted(progress_order):
            errors.append("PSRAM progress markers were observed out of order")

    full = re.search(
        r"P2PSRAM:FULL:PASS:BYTES=(\d+):FNV1A=([0-9A-F]{8})", run_text
    )
    if full is not None:
        values["full_bytes"] = int(full.group(1))
        values["fnv1a"] = int(full.group(2), 16)
        values["expected_fnv1a"] = expected_fnv1a(sequence)
        if values["full_bytes"] != PSRAM_SIZE:
            errors.append("full-coverage byte count is not 32 MiB")
        if values["fnv1a"] != values["expected_fnv1a"]:
            errors.append("full-coverage FNV-1a does not match the nonce pattern")

    timeout = re.search(
        r"P2PSRAM:TIMEOUT:PASS:RESULT=(\d+):BYTES=(\d+):"
        r"DEADLINE_TICKS=(\d+):FAULT=([A-Z_]+):TICK_USEC=(\d+)",
        run_text,
    )
    if timeout is not None:
        timeout_keys = (
            "timeout_errno",
            "timeout_bytes",
            "timeout_deadline_ticks",
            "timeout_fault",
            "timeout_tick_usec",
        )
        groups = timeout.groups()
        values.update(
            zip(
                timeout_keys,
                (
                    int(groups[0]),
                    int(groups[1]),
                    int(groups[2]),
                    groups[3],
                    int(groups[4]),
                ),
            )
        )
        timeout_expected = (
            PSRAM_TIMEOUT_ERRNO,
            PSRAM_TIMEOUT_BYTES,
            PSRAM_TIMEOUT_DEADLINE_TICKS,
            PSRAM_TIMEOUT_FAULT,
            PSRAM_TICK_USEC,
        )
        if tuple(values[key] for key in timeout_keys) != timeout_expected:
            errors.append(
                "PSRAM timeout evidence does not prove the forced worker stall"
            )

    return {
        "complete": not errors,
        "errors": errors,
        "sequence": sequence,
        "values": values,
    }
