#!/usr/bin/env python3
"""Strict console protocol for the P2 scheduler stress application."""

import re
from typing import Dict, List, Tuple


STAGES = (
    ("PRIORITY", 2000),
    ("ROUNDROBIN", 100000),
    ("SEMAPHORE", 600000),
    ("PI_MUTEX", 2000),
    ("CONDITION", 100000),
    ("MQUEUE", 100000),
    ("SIGNAL", 100000),
    ("TIMER", 10),
    ("PTHREAD", 4),
    ("TASK_RECREATE", 64),
)
TOTAL_EVENTS = 1_004_078
STACK_CHECKS = 3
HEAP_CHECKS = 5
HEAP_ALLOCATION_BYTES = 4096
HEAP_CONCURRENCY_THREADS = 2
HEAP_CONCURRENCY_ROUNDS = 256
HEAP_CONCURRENCY_COUNT = 512

BOOT_MARKER = "P2SCHED:BOOT"
PROFILE_MARKER = "P2SCHED:PROFILE:MODE=FLAT-UP:RAM=524288"
STACK_START_MARKER = "P2SCHED:STACK:START"
HEAP_START_MARKER = "P2SCHED:HEAP:START"
HEAP_CONCURRENCY_START_MARKER = (
    "P2SCHED:HEAP_CONCURRENCY:START:THREADS=2:ROUNDS=256:TARGET=512"
)
HEAP_CONCURRENCY_PASS_MARKER = "P2SCHED:HEAP_CONCURRENCY:PASS:COUNT=512"
TOTAL_MARKER = "P2SCHED:TOTAL:PASS:COUNT=1004078"
PASS_MARKER = "P2SCHED:PASS:COUNT=1004078"

STACK_PATTERN = re.compile(
    r"^P2SCHED:STACK:PASS:CHECKS=3:SIZE=(\d+):USED=(\d+)$"
)
HEAP_PATTERN = re.compile(
    r"^P2SCHED:HEAP:PASS:CHECKS=5:"
    r"BEFORE=(\d+):DURING=(\d+):AFTER=(\d+)$"
)

FAILURE_PATTERNS = (
    (
        "P2 scheduler stress failure",
        re.compile(
            r"(?:^|[\r\n])P2SCHED:FAIL:[A-Z0-9_]+:CODE=-?\d+"
            r"(?:\r?\n|$)"
        ),
    ),
    ("PANIC", re.compile(r"\bPANIC\b", re.IGNORECASE)),
    ("assertion", re.compile(r"\bASSERT(?:ION)?\b", re.IGNORECASE)),
    ("stack overflow", re.compile(r"STACK\s+OVERFLOW", re.IGNORECASE)),
    ("unexpected IRQ", re.compile(r"UNEXPECTED\s+IRQ", re.IGNORECASE)),
    ("register dump", re.compile(r"REGISTER\s+DUMP", re.IGNORECASE)),
)


def _line_pattern(literal: str) -> re.Pattern:
    return re.compile(
        r"(?:^|[\r\n])" + re.escape(literal) + r"\r?\n"
    )


def _variable_line_pattern(pattern: re.Pattern) -> re.Pattern:
    text = pattern.pattern
    if text.startswith("^"):
        text = text[1:]
    if text.endswith("$"):
        text = text[:-1]
    return re.compile(r"(?:^|[\r\n])" + text + r"\r?\n")


def marker_patterns() -> Tuple[Tuple[str, re.Pattern], ...]:
    """Return every ordered streaming marker for one complete run."""

    markers: List[Tuple[str, re.Pattern]] = [
        (BOOT_MARKER, _line_pattern(BOOT_MARKER)),
        (PROFILE_MARKER, _line_pattern(PROFILE_MARKER)),
    ]
    for stage, count in STAGES:
        start = "P2SCHED:{}:START:TARGET={}".format(stage, count)
        passed = "P2SCHED:{}:PASS:COUNT={}".format(stage, count)
        markers.extend(
            ((start, _line_pattern(start)), (passed, _line_pattern(passed)))
        )
    markers.extend(
        (
            (STACK_START_MARKER, _line_pattern(STACK_START_MARKER)),
            (
                "P2SCHED:STACK:PASS:CHECKS=3",
                _variable_line_pattern(STACK_PATTERN),
            ),
            (HEAP_START_MARKER, _line_pattern(HEAP_START_MARKER)),
            (
                "P2SCHED:HEAP:PASS:CHECKS=5",
                _variable_line_pattern(HEAP_PATTERN),
            ),
            (
                HEAP_CONCURRENCY_START_MARKER,
                _line_pattern(HEAP_CONCURRENCY_START_MARKER),
            ),
            (
                HEAP_CONCURRENCY_PASS_MARKER,
                _line_pattern(HEAP_CONCURRENCY_PASS_MARKER),
            ),
            (TOTAL_MARKER, _line_pattern(TOTAL_MARKER)),
            (PASS_MARKER, _line_pattern(PASS_MARKER)),
        )
    )
    return tuple(markers)


def parse_schedstress(text: str) -> Dict[str, object]:
    """Validate one exact scheduler-stress transcript.

    Heap-concurrency operations are reported separately and deliberately do
    not inflate the fixed scheduler/synchronization event total.
    """

    lines = [line.strip() for line in text.replace("\r", "\n").split("\n")]
    errors: List[str] = []
    duplicates: List[str] = []
    failures: List[Dict[str, str]] = []
    positions: List[int] = []
    values: Dict[str, object] = {
        "stage_counts": {stage: count for stage, count in STAGES},
        "expected_total_events": TOTAL_EVENTS,
        "heap_concurrency_counted_in_total": False,
    }

    for line in lines:
        for label, pattern in FAILURE_PATTERNS:
            if pattern.search(line):
                failures.append({"kind": label, "line": line})
                break

    expected_patterns: List[Tuple[str, re.Pattern]] = [
        (BOOT_MARKER, re.compile(re.escape(BOOT_MARKER))),
        (PROFILE_MARKER, re.compile(re.escape(PROFILE_MARKER))),
    ]
    for stage, count in STAGES:
        for state, field in (("START", "TARGET"), ("PASS", "COUNT")):
            literal = "P2SCHED:{}:{}:{}={}".format(
                stage, state, field, count
            )
            expected_patterns.append((literal, re.compile(re.escape(literal))))

    expected_patterns.append(
        (STACK_START_MARKER, re.compile(re.escape(STACK_START_MARKER)))
    )
    expected_patterns.append(
        ("P2SCHED:STACK:PASS:CHECKS=3", STACK_PATTERN)
    )
    expected_patterns.append(
        (HEAP_START_MARKER, re.compile(re.escape(HEAP_START_MARKER)))
    )
    expected_patterns.append(("P2SCHED:HEAP:PASS:CHECKS=5", HEAP_PATTERN))
    for literal in (
        HEAP_CONCURRENCY_START_MARKER,
        HEAP_CONCURRENCY_PASS_MARKER,
        TOTAL_MARKER,
        PASS_MARKER,
    ):
        expected_patterns.append((literal, re.compile(re.escape(literal))))

    recognized_lines = set()
    matches_by_label: Dict[str, List[Tuple[int, re.Match]]] = {}
    for label, pattern in expected_patterns:
        found = [
            (index, match)
            for index, line in enumerate(lines)
            for match in [pattern.fullmatch(line)]
            if match is not None
        ]
        matches_by_label[label] = found
        recognized_lines.update(index for index, _match in found)
        if not found:
            errors.append("missing {}".format(label))
        elif len(found) != 1:
            duplicates.append(label)
        else:
            positions.append(found[0][0])

    protocol_lines = [
        (index, line)
        for index, line in enumerate(lines)
        if line.startswith("P2SCHED:")
    ]
    failure_line_indexes = {
        index
        for index, line in protocol_lines
        if line.startswith("P2SCHED:FAIL:")
    }
    unexpected = [
        line
        for index, line in protocol_lines
        if index not in recognized_lines and index not in failure_line_indexes
    ]
    if unexpected:
        errors.extend(
            "unexpected protocol line: {}".format(line)
            for line in unexpected
        )

    stack_found = matches_by_label.get("P2SCHED:STACK:PASS:CHECKS=3", [])
    if len(stack_found) == 1:
        stack_size, stack_used = (
            int(value) for value in stack_found[0][1].groups()
        )
        values.update(
            {
                "stack_checks": STACK_CHECKS,
                "stack_size": stack_size,
                "stack_used": stack_used,
            }
        )
        if stack_size <= 0:
            errors.append("stack size must be greater than zero")
        if stack_used <= 0 or stack_used > stack_size:
            errors.append("stack used must be in the range 1..stack size")

    heap_found = matches_by_label.get("P2SCHED:HEAP:PASS:CHECKS=5", [])
    if len(heap_found) == 1:
        before, during, after = (
            int(value) for value in heap_found[0][1].groups()
        )
        values.update(
            {
                "heap_checks": HEAP_CHECKS,
                "heap_before": before,
                "heap_during": during,
                "heap_after": after,
            }
        )
        if during < before + HEAP_ALLOCATION_BYTES:
            errors.append(
                "heap during usage did not grow by at least {} bytes".format(
                    HEAP_ALLOCATION_BYTES
                )
            )
        if after > during:
            errors.append("heap after usage exceeds during usage")

    values.update(
        {
            "heap_concurrency_threads": HEAP_CONCURRENCY_THREADS,
            "heap_concurrency_rounds": HEAP_CONCURRENCY_ROUNDS,
            "heap_concurrency_count": HEAP_CONCURRENCY_COUNT,
            "total_events": TOTAL_EVENTS,
        }
    )
    stage_sum = sum(count for _stage, count in STAGES)
    if stage_sum != TOTAL_EVENTS:
        errors.append(
            "host stage sum {} does not equal {}".format(stage_sum, TOTAL_EVENTS)
        )

    order_valid = positions == sorted(positions)
    if not order_valid:
        errors.append("protocol markers are out of order")

    reset_count = len(matches_by_label.get(BOOT_MARKER, []))
    complete = (
        not errors
        and not duplicates
        and not failures
        and order_valid
        and reset_count == 1
    )
    return {
        "complete": complete,
        "errors": errors,
        "duplicates": duplicates,
        "failures": failures,
        "order_valid": order_valid,
        "reset_count": reset_count,
        "values": values,
    }
