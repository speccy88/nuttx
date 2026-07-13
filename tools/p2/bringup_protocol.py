#!/usr/bin/env python3
"""Strict marker protocol for the deterministic P2 NuttX bring-up app."""

import re
from typing import Dict, List, Tuple


BRINGUP_MARKERS = (
    "P2NUTTX:BOOT",
    "P2NUTTX:DATA=OK",
    "P2NUTTX:BSS=OK",
    "P2NUTTX:HEAP=OK",
    "P2NUTTX:TICK=OK",
    "P2NUTTX:TASKS=OK",
    "P2NUTTX:SEMAPHORE=OK",
    "P2NUTTX:STACKS=OK",
    "P2NUTTX:PASS",
)

BRINGUP_FAILURE_PATTERNS = (
    ("P2NUTTX failure", re.compile(r"P2NUTTX:FAIL(?:[:=]|$)")),
    ("PANIC", re.compile(r"\bPANIC\b", re.IGNORECASE)),
    ("assertion", re.compile(r"\bASSERT(?:ION)?\b", re.IGNORECASE)),
    ("error", re.compile(r"\bERROR\b", re.IGNORECASE)),
    ("stack overflow", re.compile(r"STACK\s+OVERFLOW", re.IGNORECASE)),
    ("unexpected IRQ", re.compile(r"UNEXPECTED\s+IRQ", re.IGNORECASE)),
    ("register dump", re.compile(r"REGISTER\s+DUMP", re.IGNORECASE)),
)


def parse_bringup(text: str) -> Dict[str, object]:
    """Parse one reset's console text and require exact ordered marker lines."""

    lines = [line.strip() for line in text.replace("\r", "\n").split("\n")]
    positions: List[int] = []
    found: List[str] = []
    missing: List[str] = []
    duplicates: List[str] = []

    for marker in BRINGUP_MARKERS:
        indices = [index for index, line in enumerate(lines) if line == marker]
        if not indices:
            missing.append(marker)
            continue

        found.append(marker)
        positions.append(indices[0])
        if len(indices) != 1:
            duplicates.append(marker)

    failures: List[Tuple[str, str]] = []
    for line in lines:
        for label, pattern in BRINGUP_FAILURE_PATTERNS:
            if pattern.search(line):
                failures.append((label, line))
                break

    order_valid = positions == sorted(positions)
    complete = not missing and not duplicates and not failures and order_valid
    return {
        "complete": complete,
        "found": found,
        "missing": missing,
        "duplicates": duplicates,
        "failures": [
            {"kind": label, "line": line} for label, line in failures
        ],
        "order_valid": order_valid,
        "reset_count": lines.count(BRINGUP_MARKERS[0]),
    }
