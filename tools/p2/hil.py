#!/usr/bin/env python3
"""RAM-load and verify a native P2 standalone or NuttX protocol.

This orchestrator deliberately lets ``loadp2`` be the only process which
opens the serial port.  Its terminal mode provides the console capture after
the RAM download, while ``-e`` sends the protocol byte without a second
serial owner.
"""

import argparse
import codecs
import datetime
import hashlib
import json
import os
import pathlib
import re
import selectors
import shlex
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from bringup_protocol import (
    BRINGUP_FAILURE_PATTERNS as APP_BRINGUP_FAILURE_PATTERNS,
    BRINGUP_MARKERS as APP_BRINGUP_MARKERS,
)
import monitor
from smartpins_protocol import (
    FAILURE_PATTERNS as SMARTPINS_FAILURE_PATTERNS,
    hil_marker_patterns as smartpins_marker_patterns,
    parse_smartpins,
    stages_from_kconfig as smartpins_stages_from_kconfig,
)
from psram_protocol import (
    FAILURE_PATTERNS as PSRAM_FAILURE_PATTERNS,
    command_bytes as psram_command_bytes,
    expected_fnv1a as psram_expected_fnv1a,
    marker_patterns as psram_marker_patterns,
    normalize_sequence as normalize_psram_sequence,
    parse_psram,
)
from storage_protocol import (
    ALTERNATE_TRANSACTIONS as STORAGE_ALTERNATE_TRANSACTIONS,
    BOARD_MARKER_PATTERNS as STORAGE_BOARD_MARKER_PATTERNS,
    FAILURE_PATTERNS as STORAGE_ACTION_FAILURE_PATTERNS,
    FLASH_WRITABLE_ACTIONS,
    SD_DESTRUCTIVE_ACTIONS,
    command_bytes as storage_command_bytes,
    first_error as storage_first_error,
    normalize_sequence as normalize_storage_sequence,
    parse_storage_response,
    response_marker_patterns as storage_response_marker_patterns,
    sequence_required as storage_sequence_required,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_IMAGE = (
    REPO_ROOT / "tools" / "p2" / "standalone" / "hello" / "build" / "p2hello.elf"
)
DEFAULT_CONTEXT_IMAGE = (
    REPO_ROOT / "tools" / "p2" / "standalone" / "context" / "build" / "p2context.elf"
)
DEFAULT_NUTTX_IMAGE = REPO_ROOT / "nuttx"
DEFAULT_LOCK_FILE = pathlib.Path("/tmp/nuttx-p2-hil.lock")
DEFAULT_TOOLCHAIN_LOCK = REPO_ROOT / "tools" / "p2" / "toolchain.lock"
LOADP2_FIFO_BYTES = 16384
LOADP2_SCRIPT = "pausems(500)send(?)"
NSH_COMMANDS = (
    "help",
    "echo P2NSH:HELP=OK",
    "uname -a",
    "echo P2NSH:UNAME=OK",
    "ps",
    "echo P2NSH:PS=OK",
    "free",
    "echo P2NSH:FREE=OK",
    "uptime",
    "echo P2NSH:UPTIME=OK",
    "sleep 1",
    "echo P2NSH:SLEEP=OK",
    "ls /dev",
    "echo P2NSH:LSDEV=OK",
    "mount",
    "echo P2NSH:MOUNT=OK",
    "echo P2_NSH_OK",
)
NSH_COMMAND_BYTES = ("\r".join(NSH_COMMANDS) + "\r").encode("ascii")
NSH_SLEEP_START_LABEL = "NSH sleep 1 command echo"
NSH_SLEEP_DONE_LABEL = "NSH sleep 1 returned and sentinel prompt"
NSH_SLEEP_MIN_SECONDS = 0.75
NSH_SLEEP_MAX_SECONDS = 3.0
NSH_PROMPT_PATTERN = r"nsh> (?:\x1b\[K)?"

EXIT_OK = 0
EXIT_SAFETY = 2
EXIT_HIL_FAILURE = 3
EXIT_LOCK_BUSY = 9
EXIT_INTERRUPTED = 130

OSTEST_PROFILES = {
    "ostest-pi-assert": (True, True),
    "ostest-pi-production": (True, False),
    "ostest-cond-assert": (False, True),
    "ostest-cond-production": (False, False),
}
OSTEST_CONFIG_ROOT = (
    REPO_ROOT
    / "boards"
    / "p2"
    / "p2x8c4m64p"
    / "p2-ec32mb"
    / "configs"
)

SMARTPINS_DIRECT_CONFIG = (
    ("CONFIG_P2_EC32MB_GPIO", "y"),
    ("CONFIG_P2_EC32MB_GPIO_OUT_PIN", "0"),
    ("CONFIG_P2_EC32MB_GPIO_IN_PIN", "1"),
    ("CONFIG_P2_EC32MB_UART1", "y"),
    ("CONFIG_P2_EC32MB_UART1_TX_PIN", "2"),
    ("CONFIG_P2_EC32MB_UART1_RX_PIN", "3"),
    ("CONFIG_P2_EC32MB_UART1_BAUD", "115200"),
    ("CONFIG_P2_EC32MB_PWM", "y"),
    ("CONFIG_P2_EC32MB_PWM_PIN", "4"),
    ("CONFIG_P2_EC32MB_CAPTURE", "y"),
    ("CONFIG_P2_EC32MB_CAPTURE_PIN", "5"),
    ("CONFIG_SPI_BITBANG", "y"),
    ("CONFIG_SPI_DRIVER", "y"),
    ("CONFIG_SPI_EXCHANGE", "y"),
    ("CONFIG_P2_EC32MB_SPI", "y"),
    ("CONFIG_P2_EC32MB_SPI_MOSI_PIN", "6"),
    ("CONFIG_P2_EC32MB_SPI_MISO_PIN", "7"),
    ("CONFIG_P2_EC32MB_SPI_SCK_PIN", "8"),
    ("CONFIG_P2_EC32MB_SPI_CS_PIN", "9"),
    ("CONFIG_P2_EC32MB_SPI_MAX_FREQUENCY", "100000"),
)

STORAGE_REQUIRED_CONFIG = (
    ("CONFIG_BOARD_LATE_INITIALIZE", "y"),
    ("CONFIG_P2_STORAGE", "y"),
    ("CONFIG_P2_EC32MB_STORAGE_BINDINGS", "y"),
    ("CONFIG_P2_EC32MB_W25_PROBE_FREQUENCY", "400000"),
    ("CONFIG_P2_STORAGE_MAX_FREQUENCY", "2000000"),
    ("CONFIG_MTD_W25", "y"),
    ("CONFIG_W25_SPIMODE", "3"),
    ("CONFIG_W25_SPIFREQUENCY", "2000000"),
    ("CONFIG_MMCSD", "y"),
    ("CONFIG_MMCSD_SPI", "y"),
    ("CONFIG_MMCSD_HAVE_CARDDETECT", "n"),
    ("CONFIG_MMCSD_HAVE_WRITEPROTECT", "n"),
    ("CONFIG_MMCSD_READONLY", "n"),
    ("CONFIG_MMCSD_IDMODE_CLOCK", "400000"),
    ("CONFIG_MMCSD_SPICLOCK", "2000000"),
    ("CONFIG_MMCSD_SPIMODE", "0"),
)

STORAGE_ACTION_REQUIRED_CONFIG = (
    ("CONFIG_BUILTIN", "y"),
    ("CONFIG_NSH_BUILTIN_APPS", "y"),
    ("CONFIG_TESTING_P2STORAGE", "y"),
    ("CONFIG_TESTING_P2STORAGE_DESTRUCTIVE", "y"),
    ("CONFIG_TESTING_P2STORAGE_FLASH_DEVPATH", '"/dev/smart0"'),
    ("CONFIG_TESTING_P2STORAGE_FLASH_MOUNTPOINT", '"/mnt/flash"'),
    ("CONFIG_TESTING_P2STORAGE_SD_DEVPATH", '"/dev/mmcsd0"'),
    ("CONFIG_TESTING_P2STORAGE_SD_MOUNTPOINT", '"/mnt/sd"'),
    ("CONFIG_TESTING_P2STORAGE_RECORD_SIZE", "256"),
    ("CONFIG_TESTING_P2STORAGE_STREAM_SIZE", "1048576"),
    ("CONFIG_TESTING_P2STORAGE_FLASH_CYCLE_COUNT", "16"),
    ("CONFIG_TESTING_P2STORAGE_SD_STRESS_COUNT", "64"),
    ("CONFIG_TESTING_P2STORAGE_BUS_ALTERNATE_COUNT", "1000"),
    ("CONFIG_TESTING_P2STORAGE_FLASH_FULL_MAX_BYTES", "20971520"),
    ("CONFIG_TESTING_P2STORAGE_INTERRUPT_HOLD_MSEC", "10000"),
)

PSRAM_REQUIRED_CONFIG = (
    ("CONFIG_BOARD_LATE_INITIALIZE", "y"),
    ("CONFIG_USEC_PER_TICK", "10000"),
    ("CONFIG_P2_SMARTPIN", "y"),
    ("CONFIG_P2_EC32MB_PSRAM", "y"),
    ("CONFIG_P2_EC32MB_PSRAM_COG_STACKSIZE", "3072"),
    ("CONFIG_P2_EC32MB_PSRAM_MAX_REQUEST", "65536"),
    ("CONFIG_P2_EC32MB_PSRAM_TIMEOUT_TICKS", "500"),
    ("CONFIG_P2_EC32MB_PSRAM_CANCEL_GRACE_TICKS", "100"),
    ("CONFIG_TESTING_P2PSRAM", "y"),
    ("CONFIG_TESTING_P2PSRAM_STACKSIZE", "4096"),
    ("CONFIG_TESTING_P2PSRAM_WORKER_STACKSIZE", "2048"),
    ("CONFIG_TESTING_P2PSRAM_RANDOM_COUNT", "1024"),
    ("CONFIG_SYSTEM_DD", "n"),
)

PANIC_PATTERNS = (
    ("PANIC", re.compile(r"PANIC", re.IGNORECASE)),
    ("ASSERT", re.compile(r"ASSERT", re.IGNORECASE)),
    ("STACK OVERFLOW", re.compile(r"STACK\s+OVERFLOW", re.IGNORECASE)),
    ("UNEXPECTED IRQ", re.compile(r"UNEXPECTED\s+IRQ", re.IGNORECASE)),
    ("REGISTER DUMP", re.compile(r"REGISTER\s+DUMP", re.IGNORECASE)),
)

PROTOCOL_FAILURE_PATTERNS = (
    ("P2HELLO:DATA=FAIL", re.compile(r"P2HELLO:DATA=FAIL")),
    ("P2HELLO:BSS=FAIL", re.compile(r"P2HELLO:BSS=FAIL")),
    ("P2HELLO:ECHO=INVALID", re.compile(r"P2HELLO:ECHO=INVALID")),
)

DISCONNECT_PATTERNS = (
    ("Could not find a P2", re.compile(r"Could not find a P2", re.IGNORECASE)),
    (
        "device disconnected",
        re.compile(r"device\s+(?:was\s+)?disconnected", re.IGNORECASE),
    ),
    ("device not configured", re.compile(r"device not configured", re.IGNORECASE)),
    ("input/output error", re.compile(r"input/output error", re.IGNORECASE)),
)


@dataclass(frozen=True)
class MarkerSpec:
    label: str
    pattern: re.Pattern
    repeatable: bool = False


BRINGUP_APP_MARKERS = tuple(
    MarkerSpec(
        marker,
        re.compile(r"(?:^|[\r\n])" + re.escape(marker) + r"\r?\n", re.MULTILINE),
    )
    for marker in APP_BRINGUP_MARKERS
)


def nsh_command_result_marker(
    label: str, command: str, output_pattern: str, sentinel: str
) -> MarkerSpec:
    """Match one echoed command, its output, and its sentinel prompt pair."""

    pattern = (
        r"(?:^|[\r\n])(?:"
        + NSH_PROMPT_PATTERN
        + r")?"
        + re.escape(command)
        + r"\r?\n[\s\S]*?"
        + output_pattern
        + r"[\s\S]*?\r?\n+"
        + NSH_PROMPT_PATTERN
        + r"echo "
        + re.escape(sentinel)
        + r"\r?\n+"
        + re.escape(sentinel)
        + r"\r?\n+"
        + NSH_PROMPT_PATTERN
    )
    return MarkerSpec(label, re.compile(pattern, re.MULTILINE))


HELLO_MARKERS = (
    MarkerSpec("P2HELLO:ENTRY", re.compile(r"P2HELLO:ENTRY")),
    MarkerSpec("P2HELLO:DATA=OK", re.compile(r"P2HELLO:DATA=OK")),
    MarkerSpec("P2HELLO:BSS=OK", re.compile(r"P2HELLO:BSS=OK")),
    MarkerSpec(
        "P2HELLO:PTRA=0x........",
        re.compile(r"P2HELLO:PTRA=(?P<ptra>0x[0-9A-Fa-f]{8})"),
    ),
    MarkerSpec(
        "P2HELLO:COUNTER=0x........",
        re.compile(r"P2HELLO:COUNTER=(?P<counter>0x[0-9A-Fa-f]{8})"),
    ),
    MarkerSpec("P2HELLO:READY", re.compile(r"P2HELLO:READY")),
    MarkerSpec("P2HELLO:ECHO=?", re.compile(r"P2HELLO:ECHO=\?")),
)

CONTEXT_MARKERS = (
    MarkerSpec("P2CTX:START", re.compile(r"P2CTX:START")),
    MarkerSpec(
        "P2CTX:SWITCHES=1000000",
        re.compile(r"P2CTX:SWITCHES=1000000"),
    ),
    MarkerSpec("P2CTX:REGS=OK", re.compile(r"P2CTX:REGS=OK")),
    MarkerSpec("P2CTX:STACKS=OK", re.compile(r"P2CTX:STACKS=OK")),
    MarkerSpec("P2CTX:PASS", re.compile(r"P2CTX:PASS")),
)

CONTEXT_FAILURE_PATTERNS = (
    (
        "P2CTX failure",
        re.compile(r"P2CTX:FAIL MASK=[0-9]+\r?\n", re.IGNORECASE),
    ),
)

BOOT_MARKERS = (
    MarkerSpec("P2BOOT:ENTRY", re.compile(r"P2BOOT:ENTRY")),
    MarkerSpec("P2BOOT:DATA=OK", re.compile(r"P2BOOT:DATA=OK")),
    MarkerSpec("P2BOOT:BSS=OK", re.compile(r"P2BOOT:BSS=OK")),
    MarkerSpec("P2BOOT:NX_START", re.compile(r"P2BOOT:NX_START")),
)

STORAGE_MARKERS = BOOT_MARKERS + (
    MarkerSpec("P2STORAGE:W25=PRIVATE", re.compile(r"P2STORAGE:W25=PRIVATE")),
    MarkerSpec(
        "P2STORAGE:MMCSD=/dev/mmcsd0",
        re.compile(r"P2STORAGE:MMCSD=/dev/mmcsd0"),
    ),
    MarkerSpec("nsh> prompt", re.compile(r"(?:^|[\r\n])nsh> ", re.MULTILINE)),
)

STORAGE_ACTION_BOOT_MARKERS = BOOT_MARKERS + tuple(
    MarkerSpec(label, pattern) for label, pattern in STORAGE_BOARD_MARKER_PATTERNS
) + (
    MarkerSpec(
        "nsh> prompt",
        re.compile(r"(?:^|[\r\n])nsh> ", re.MULTILINE),
        repeatable=True,
    ),
)

STORAGE_FAILURE_PATTERNS = (
    ("P2BOOT:DATA=FAIL", re.compile(r"P2BOOT:DATA=FAIL")),
    ("P2BOOT:BSS=FAIL", re.compile(r"P2BOOT:BSS=FAIL")),
    ("P2 storage binding failure", re.compile(r"P2STORAGE:[A-Z0-9_]+=FAIL:")),
) + STORAGE_ACTION_FAILURE_PATTERNS

NSH_COMMAND_MARKERS = (
    nsh_command_result_marker(
        "NSH help output, sentinel, and prompts",
        "help",
        r"help usage:[^\r\n]*",
        "P2NSH:HELP=OK",
    ),
    nsh_command_result_marker(
        "NSH uname -a output, sentinel, and prompts",
        "uname -a",
        r"(?:^|[\r\n])NuttX[^\r\n]+",
        "P2NSH:UNAME=OK",
    ),
    nsh_command_result_marker(
        "NSH ps output, sentinel, and prompts",
        "ps",
        r"TID\s+PID\s+PPID(?:\s+CPU)?\s+PRI\s+POLICY\s+TYPE[^\r\n]*"
        r"\r?\n\s*[0-9]+\s+[0-9]+\s+[0-9]+",
        "P2NSH:PS=OK",
    ),
    nsh_command_result_marker(
        "NSH free output, sentinel, and prompts",
        "free",
        r"total\s+used\s+free\s+maxused\s+maxfree\s+nused\s+nfree\s+name"
        r"[^\r\n]*\r?\n\s*[0-9]+\s+[0-9]+\s+[0-9]+[^\r\n]*"
        r"\b(?:Umem|Kmem)\b",
        "P2NSH:FREE=OK",
    ),
    nsh_command_result_marker(
        "NSH uptime output, sentinel, and prompts",
        "uptime",
        r"[0-9]{2}:[0-9]{2}:[0-9]{2}\s+up\s+",
        "P2NSH:UPTIME=OK",
    ),
    MarkerSpec(
        NSH_SLEEP_START_LABEL,
        re.compile(
            r"(?:^|[\r\n])(?:" + NSH_PROMPT_PATTERN + r")?sleep 1\r?\n",
            re.MULTILINE,
        ),
    ),
    MarkerSpec(
        NSH_SLEEP_DONE_LABEL,
        re.compile(
            r"(?:^|[\r\n])"
            + NSH_PROMPT_PATTERN
            + r"echo P2NSH:SLEEP=OK\r?\n+"
            r"P2NSH:SLEEP=OK\r?\n+"
            + NSH_PROMPT_PATTERN,
            re.MULTILINE,
        ),
    ),
    nsh_command_result_marker(
        "NSH ls /dev output, sentinel, and prompts",
        "ls /dev",
        r"(?:^|[\r\n])/dev:\r?\n[\s\S]*?"
        r"(?:^|[\r\n])\s*(?:console|ttyS0)[^\r\n]*",
        "P2NSH:LSDEV=OK",
    ),
    nsh_command_result_marker(
        "NSH mount output, sentinel, and prompts",
        "mount",
        r"(?:^|[\r\n])\s*/proc\s+type\s+procfs[^\r\n]*",
        "P2NSH:MOUNT=OK",
    ),
    MarkerSpec(
        "NSH final P2_NSH_OK output and prompt",
        re.compile(
            r"(?:^|[\r\n])(?:"
            + NSH_PROMPT_PATTERN
            + r")?echo P2_NSH_OK\r?\n+"
            r"P2_NSH_OK\r?\n+"
            + NSH_PROMPT_PATTERN,
            re.MULTILINE,
        ),
    ),
)

NSH_MARKERS = BOOT_MARKERS + (
    MarkerSpec("nsh> prompt", re.compile(r"(?:^|[\r\n])nsh> ", re.MULTILINE)),
) + NSH_COMMAND_MARKERS

BOOT_FAILURE_PATTERNS = (
    ("P2BOOT:DATA=FAIL", re.compile(r"P2BOOT:DATA=FAIL")),
    ("P2BOOT:BSS=FAIL", re.compile(r"P2BOOT:BSS=FAIL")),
)

BRINGUP_FAILURE_PATTERNS = BOOT_FAILURE_PATTERNS + tuple(
    APP_BRINGUP_FAILURE_PATTERNS
)

NSH_FAILURE_PATTERNS = BOOT_FAILURE_PATTERNS + (
    (
        "required NSH command not found",
        re.compile(
            r"nsh:\s+(?:help|uname|ps|free|uptime|sleep|ls|mount|echo):\s+"
            r"command not found",
            re.IGNORECASE,
        ),
    ),
    (
        "required NSH command failed",
        re.compile(
            r"nsh:\s+(?:help|uname|ps|free|uptime|sleep|ls|mount|echo):"
            r"[^\r\n]*\bfailed:",
            re.IGNORECASE,
        ),
    ),
    ("P2NSH failure sentinel", re.compile(r"P2NSH:[A-Z0-9_]+=FAIL")),
    ("P2_NSH_FAIL", re.compile(r"P2_NSH_FAIL")),
)


def ostest_line_marker(
    label: str, prefix: bool = False, repeatable: bool = False
) -> MarkerSpec:
    """Return an ostest marker constrained to one complete console line."""

    ending = r"[^\r\n]*" if prefix else ""
    return MarkerSpec(
        label,
        re.compile(
            r"(?:^|[\r\n])" + re.escape(label) + ending + r"\r?\n",
            re.MULTILINE,
        ),
        repeatable,
    )


OSTEST_FAILURE_EXCEPTIONS = (
    r"[^\r\n]*P2BOOT:(?:DATA|BSS)=FAIL",
    r"[^\r\n]*\bPASS\b[^\r\n]*failed with ECHILD",
    r"[^\r\n]*\bPASS\b[^\r\n]*pthread_join failed[^\r\n]*ESRCH",
    r"[^\r\n]*\bRoundrobin\s+Failed\b",
)
OSTEST_FAILURE_EXCEPTION = r"(?!(?:{}))".format(
    "|".join(OSTEST_FAILURE_EXCEPTIONS)
)

OSTEST_FAILURE_PATTERNS = BOOT_FAILURE_PATTERNS + (
    (
        "ostest Roundrobin Failed",
        re.compile(
            r"(?:^|[\r\n])[^\r\n]*\bRoundrobin\s+Failed\b[^\r\n]*\r?\n",
            re.IGNORECASE,
        ),
    ),
    (
        "ostest ERROR/ERRROR output",
        re.compile(
            r"(?:^|[\r\n])[^\r\n]*\bER{2,}OR\b[^\r\n]*\r?\n",
        ),
    ),
    (
        "ostest FAIL/FAILED output",
        re.compile(
            r"(?:^|[\r\n])"
            + OSTEST_FAILURE_EXCEPTION
            + r"[^\r\n]*\bFAIL(?:ED|URE)?\b[^\r\n]*\r?\n",
            re.IGNORECASE,
        ),
    ),
    (
        "ostest nonzero nerrors",
        re.compile(
            r"(?:^|[\r\n])[^\r\n]*\bnerrors\s*=\s*[+-]?(?!0\b)\d+"
            r"[^\r\n]*\r?\n",
            re.IGNORECASE,
        ),
    ),
    (
        "ostest nonzero final status",
        re.compile(
            r"(?:^|[\r\n])ostest_main:\s+Exiting with status\s+"
            r"-?(?!0\b)\d+[^\r\n]*\r?\n"
        ),
    ),
)

OSTEST_WARNING_PATTERNS = (
    (
        "ostest hrtimer timing WARNING",
        re.compile(
            r"(?:hrtimer_test|hrtimer_test_period):[^\r\n]*\[WARNING\]",
            re.IGNORECASE,
        ),
    ),
)

OSTEST_UNEXPECTED_SKIP_PATTERN = (
    "unexpected ostest Skipping output",
    re.compile(r"(?:^|[\r\n])[^\r\n]*\bSkipping\b[^\r\n]*\r?\n"),
)


def read_kconfig(path: pathlib.Path) -> Dict[str, str]:
    """Read a generated NuttX ``.config`` without evaluating shell text."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SafetyError(
            "generated configuration is unavailable: {}".format(exc)
        ) from exc

    values: Dict[str, str] = {}
    assignment = re.compile(r"^(CONFIG_[A-Za-z0-9_]+)=(.*)$")
    disabled = re.compile(r"^# (CONFIG_[A-Za-z0-9_]+) is not set$")
    for line in lines:
        match = assignment.match(line)
        if match is not None:
            values[match.group(1)] = match.group(2)
            continue
        match = disabled.match(line)
        if match is not None:
            values[match.group(1)] = "n"
    return values


def kconfig_enabled(values: Mapping[str, str], name: str) -> bool:
    return values.get(name) == "y"


def kconfig_integer(
    values: Mapping[str, str], name: str, default: int = 0
) -> int:
    try:
        return int(values.get(name, str(default)), 0)
    except ValueError as exc:
        raise SafetyError("{} must be an integer in .config".format(name)) from exc


def validate_smartpins_config(values: Mapping[str, str]) -> Tuple[str, ...]:
    """Validate the exact direct-jumper image and return its protocol stages."""

    stages = smartpins_stages_from_kconfig(dict(values))
    required = ("GPIO", "UART", "PWM_CAPTURE", "SPI")
    missing = tuple(stage for stage in required if stage not in stages)
    if missing:
        raise SafetyError(
            "smartpins image is missing required direct-loopback stages: {}".format(
                ", ".join(missing)
            )
        )

    mismatches = [
        "{}={} (required {})".format(name, values.get(name, "<unset>"), expected)
        for name, expected in SMARTPINS_DIRECT_CONFIG
        if values.get(name) != expected
    ]
    if mismatches:
        raise SafetyError(
            "smartpins image does not match installed direct jumpers: {}".format(
                ", ".join(mismatches)
            )
        )
    if "DAC_ADC" in stages:
        raise SafetyError("DAC_ADC must be disabled for the direct P4-P5 jumper")
    return stages


def validate_storage_config(values: Mapping[str, str]) -> None:
    """Require the exact private-W25 and generic-MMC/SD binding profile."""

    mismatches = [
        "{}={} (required {})".format(name, values.get(name, "<unset>"), expected)
        for name, expected in STORAGE_REQUIRED_CONFIG
        if values.get(name) != expected
    ]
    if mismatches:
        raise SafetyError(
            "storage image does not match the binding profile: {}".format(
                ", ".join(mismatches)
            )
        )


def validate_storage_action_config(values: Mapping[str, str]) -> None:
    """Require every value which changes the destructive console protocol."""

    mismatches = [
        "{}={} (required {})".format(
            name, values.get(name, "<unset>"), expected
        )
        for name, expected in STORAGE_ACTION_REQUIRED_CONFIG
        if values.get(name) != expected
    ]
    if mismatches:
        raise SafetyError(
            "storage action image does not match the locked protocol: {}".format(
                ", ".join(mismatches)
            )
        )


def validate_psram_config(values: Mapping[str, str]) -> None:
    """Require the exact bounded external-PSRAM service profile."""

    mismatches = [
        "{}={} (required {})".format(name, values.get(name, "<unset>"), expected)
        for name, expected in PSRAM_REQUIRED_CONFIG
        if values.get(name) != expected
    ]
    if mismatches:
        raise SafetyError(
            "psram image does not match the locked service profile: {}".format(
                ", ".join(mismatches)
            )
        )


def ostest_profile_path(profile: str) -> pathlib.Path:
    if profile not in OSTEST_PROFILES:
        raise SafetyError("unknown ostest profile: {}".format(profile))
    return OSTEST_CONFIG_ROOT / profile / "defconfig"


def validate_ostest_profile_values(
    values: Mapping[str, str], profile: str
) -> None:
    """Require every value pinned by the selected profile defconfig."""

    expected = read_kconfig(ostest_profile_path(profile))
    mismatches = []
    for name, wanted in expected.items():
        actual = values.get(name, "n")
        if actual != wanted:
            mismatches.append("{}={} (expected {})".format(name, actual, wanted))
    if mismatches:
        raise SafetyError(
            "generated .config does not match {}: {}".format(
                profile, "; ".join(mismatches)
            )
        )


def ostest_failure_patterns(
    values: Mapping[str, str]
) -> Tuple[Tuple[str, re.Pattern], ...]:
    """Return profile-specific failures while allowing the PI-only skip."""

    if kconfig_enabled(values, "CONFIG_PRIORITY_INHERITANCE"):
        return OSTEST_FAILURE_PATTERNS
    return OSTEST_FAILURE_PATTERNS + (OSTEST_UNEXPECTED_SKIP_PATTERN,)


def validate_ostest_config(
    values: Mapping[str, str],
    assertion_mode: str = "any",
    profile: Optional[str] = None,
) -> None:
    """Refuse a reduced or ambiguous image for the full P2 ostest matrix."""

    required = [
        "CONFIG_BUILD_FLAT",
        "CONFIG_CANCELLATION_POINTS",
        "CONFIG_DEV_NULL",
        "CONFIG_ENABLE_ALL_SIGNALS",
        "CONFIG_FS_NAMED_SEMAPHORES",
        "CONFIG_HRTIMER",
        "CONFIG_P2_BOOT_TRACE",
        "CONFIG_PTHREAD_MUTEX_BOTH",
        "CONFIG_PTHREAD_MUTEX_TYPES",
        "CONFIG_SCHED_EVENTS",
        "CONFIG_SCHED_SPORADIC",
        "CONFIG_SCHED_WAITPID",
        "CONFIG_SCHED_WORKQUEUE",
        "CONFIG_SIG_EVTHREAD",
        "CONFIG_TESTING_OSTEST",
        "CONFIG_TESTING_OSTEST_WAITRESULT",
    ]
    if profile is None:
        priority_inheritance = True
        expected_assertions = None
        required.append("CONFIG_PRIORITY_INHERITANCE")
    else:
        try:
            priority_inheritance, expected_assertions = OSTEST_PROFILES[profile]
        except KeyError as exc:
            raise SafetyError("unknown ostest profile: {}".format(profile)) from exc
        required.append("CONFIG_DEBUG_FULLOPT")
        required.append(
            "CONFIG_PTHREAD_MUTEX_DEFAULT_PRIO_INHERIT"
            if priority_inheritance
            else "CONFIG_PTHREAD_MUTEX_DEFAULT_PRIO_NONE"
        )
        if priority_inheritance:
            required.append("CONFIG_PRIORITY_INHERITANCE")

    missing = [name for name in required if not kconfig_enabled(values, name)]
    forbidden = (
        "CONFIG_BUILD_KERNEL",
        "CONFIG_DISABLE_ALL_SIGNALS",
        "CONFIG_DISABLE_ENVIRON",
        "CONFIG_DISABLE_MQUEUE",
        "CONFIG_DISABLE_POSIX_TIMERS",
        "CONFIG_DISABLE_PTHREAD",
        "CONFIG_STDIO_DISABLE_BUFFERING",
    )
    enabled_forbidden = [
        name for name in forbidden if kconfig_enabled(values, name)
    ]
    if profile is not None and not priority_inheritance:
        enabled_forbidden.extend(
            name
            for name in (
                "CONFIG_PRIORITY_INHERITANCE",
                "CONFIG_PTHREAD_MUTEX_DEFAULT_PRIO_INHERIT",
            )
            if kconfig_enabled(values, name)
        )
    if missing or enabled_forbidden:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if enabled_forbidden:
            details.append("forbidden " + ", ".join(enabled_forbidden))
        raise SafetyError(
            "ostest matrix configuration was reduced: " + "; ".join(details)
        )

    if values.get("CONFIG_INIT_ENTRYPOINT") != '"ostest_main"':
        raise SafetyError('CONFIG_INIT_ENTRYPOINT must be "ostest_main"')
    if kconfig_integer(values, "CONFIG_TESTING_OSTEST_LOOPS") != 1:
        raise SafetyError("CONFIG_TESTING_OSTEST_LOOPS must be exactly 1")
    for name in ("CONFIG_RR_INTERVAL", "CONFIG_TLS_NELEM", "CONFIG_TLS_NCLEANUP"):
        if kconfig_integer(values, name) <= 0:
            raise SafetyError("{} must be greater than zero".format(name))

    assertions = kconfig_enabled(values, "CONFIG_DEBUG_ASSERTIONS")
    if expected_assertions is not None and assertions != expected_assertions:
        qualifier = "enabled" if expected_assertions else "disabled"
        raise SafetyError(
            "{} requires CONFIG_DEBUG_ASSERTIONS {}".format(profile, qualifier)
        )
    if expected_assertions is not None and assertion_mode != "any":
        requested = assertion_mode == "enabled"
        if requested != expected_assertions:
            raise SafetyError(
                "{} contradicts --ostest-assertions {}".format(
                    profile, assertion_mode
                )
            )
    if assertion_mode == "enabled" and not assertions:
        raise SafetyError("assertion run requires CONFIG_DEBUG_ASSERTIONS=y")
    if assertion_mode == "disabled" and assertions:
        raise SafetyError("production run requires CONFIG_DEBUG_ASSERTIONS disabled")


def ostest_markers(values: Mapping[str, str]) -> Tuple[MarkerSpec, ...]:
    """Derive the canonical enabled ostest groups from the captured config."""

    markers = list(BOOT_MARKERS)

    def add(
        label: str, prefix: bool = False, repeatable: bool = False
    ) -> None:
        markers.append(
            ostest_line_marker(label, prefix=prefix, repeatable=repeatable)
        )

    add("stdio_test: write fd=1")
    add("stdio_test: Standard I/O Check: printf")
    add("stdio_test: write fd=2")
    if kconfig_enabled(values, "CONFIG_FILE_STREAM"):
        add("stdio_test: Standard I/O Check: fprintf to stderr")
    if not kconfig_enabled(values, "CONFIG_DISABLE_ENVIRON"):
        add("ostest_main: putenv", prefix=True)
    add("ostest_main: Started user_main", prefix=True)
    add("user_main: Begin argument test")
    add("user_main: getopt() test")
    add("user_main: libc tests")
    if kconfig_integer(values, "CONFIG_TLS_NELEM") > 0:
        # The TLS test reports one successful line for each value written.

        add("tls: Successfully set", prefix=True, repeatable=True)
    if kconfig_enabled(values, "CONFIG_SCHED_THREAD_LOCAL"):
        add("user_main: sched_thread_local test")
    if not kconfig_enabled(values, "CONFIG_STDIO_DISABLE_BUFFERING"):
        add("user_main: setvbuf test")
    if kconfig_enabled(values, "CONFIG_DEV_NULL"):
        add("user_main: /dev/null test")
    if kconfig_enabled(values, "CONFIG_TESTING_OSTEST_AIO"):
        add("user_main: AIO test")
    if (
        kconfig_enabled(values, "CONFIG_ARCH_FPU")
        and not kconfig_enabled(values, "CONFIG_TESTING_OSTEST_FPUTESTDISABLE")
        and kconfig_enabled(values, "CONFIG_BUILD_FLAT")
    ):
        add("user_main: FPU test")
    if not kconfig_enabled(values, "CONFIG_BUILD_KERNEL"):
        add("user_main: task_restart test")
    if (
        kconfig_enabled(values, "CONFIG_SCHED_WAITPID")
        and not kconfig_enabled(values, "CONFIG_BUILD_KERNEL")
    ):
        add("user_main: waitpid test")
    if (
        kconfig_enabled(values, "CONFIG_TESTING_OSTEST_MULTIUSER")
        and kconfig_enabled(values, "CONFIG_SCHED_USER_IDENTITY")
    ):
        add("user_main: multi-user test")

    pthreads = not kconfig_enabled(values, "CONFIG_DISABLE_PTHREAD")
    flat = kconfig_enabled(values, "CONFIG_BUILD_FLAT")
    if pthreads and flat and kconfig_enabled(values, "CONFIG_SCHED_WORKQUEUE"):
        add("user_main: wqueue test")
    if pthreads:
        add("user_main: mutex test")
        add("user_main: timed mutex test")
    if pthreads and kconfig_enabled(values, "CONFIG_PTHREAD_MUTEX_TYPES"):
        add("user_main: recursive mutex test")
    if pthreads and kconfig_integer(values, "CONFIG_TLS_NELEM") > 0:
        add("user_main: pthread-specific data test")
    if pthreads:
        add("user_main: cancel test")
        if not kconfig_enabled(values, "CONFIG_PTHREAD_MUTEX_UNSAFE"):
            add("user_main: robust test")
        add("user_main: semaphore test")
        add("user_main: timed semaphore test")
        if kconfig_enabled(values, "CONFIG_FS_NAMED_SEMAPHORES"):
            add("user_main: Named semaphore test")
        add("user_main: condition variable test")
        if kconfig_enabled(values, "CONFIG_PRIORITY_INHERITANCE"):
            markers.append(
                MarkerSpec(
                    "Skipping, Test logic incompatible with priority inheritance",
                    re.compile(
                        r"Skipping,\s+Test logic incompatible with priority inheritance"
                    ),
                )
            )
        else:
            add("cond_test: Initializing mutex")
            markers.append(
                MarkerSpec(
                    "cond_test: Errors 0 0",
                    re.compile(
                        r"(?:^|[\r\n])cond_test:\s+Errors\s+0\s+0\r?\n",
                        re.MULTILINE,
                    ),
                )
            )
        if kconfig_enabled(values, "CONFIG_SCHED_WAITPID"):
            add("user_main: pthread_exit() test")
        add("user_main: pthread_rwlock test")
        add("user_main: pthread_rwlock_cancel test")
        if kconfig_integer(values, "CONFIG_TLS_NCLEANUP") > 0:
            add("user_main: pthread_cleanup test")
        add("user_main: timed wait test")

    mqueue = not kconfig_enabled(values, "CONFIG_DISABLE_MQUEUE")
    signals = not kconfig_enabled(values, "CONFIG_DISABLE_ALL_SIGNALS")
    timers = not kconfig_enabled(values, "CONFIG_DISABLE_POSIX_TIMERS")
    if mqueue and pthreads:
        add("user_main: timed message queue test")
    if signals:
        add("user_main: sigprocmask test")
        if mqueue and pthreads:
            add("user_main: message queue test")
        if (
            kconfig_enabled(values, "CONFIG_SIG_SIGSTOP_ACTION")
            and kconfig_enabled(values, "CONFIG_SIG_SIGKILL_ACTION")
            and not kconfig_enabled(values, "CONFIG_BUILD_KERNEL")
        ):
            add("user_main: signal action test")
    if kconfig_enabled(values, "CONFIG_ENABLE_ALL_SIGNALS"):
        add("user_main: signal handler test")
        add("user_main: nested signal handler test")
        if timers:
            add("user_main: POSIX timer test")
    if flat:
        add("user_main: spinlock test")
        add("user_main: wdog test")
        if kconfig_enabled(values, "CONFIG_HRTIMER"):
            add("user_main: hrtimer test")
            add("hrtimer_test end...")
    if timers and kconfig_enabled(values, "CONFIG_SIG_EVTHREAD"):
        add("user_main: SIGEV_THREAD timer test")
    if pthreads and kconfig_integer(values, "CONFIG_RR_INTERVAL") > 0:
        add("user_main: round-robin scheduler test")
    if pthreads and kconfig_enabled(values, "CONFIG_SCHED_SPORADIC"):
        add("user_main: sporadic scheduler test")
        add("user_main: Dual sporadic thread test")
    if pthreads:
        add("user_main: barrier test")
    if kconfig_enabled(values, "CONFIG_ARCH_SETJMP_H"):
        add("user_main: setjmp test")
    if kconfig_enabled(values, "CONFIG_PRIORITY_INHERITANCE") and pthreads:
        add("user_main: priority inheritance test")
    if pthreads:
        add("user_main: scheduler lock test")
    if (
        kconfig_enabled(values, "CONFIG_ARCH_HAVE_FORK")
        and kconfig_enabled(values, "CONFIG_SCHED_WAITPID")
        and not kconfig_enabled(values, "CONFIG_ARCH_SIM")
    ):
        add("user_main: vfork() test")
    if kconfig_enabled(values, "CONFIG_SMP") and flat:
        add("user_main: smp call test")
    if kconfig_enabled(values, "CONFIG_SCHED_EVENTS") and flat:
        add("user_main: nxevent test")
    if (
        kconfig_enabled(values, "CONFIG_ARCH_PERF_EVENTS")
        and not kconfig_enabled(values, "CONFIG_ARCH_PERF_EVENTS_USER_ACCESS")
    ):
        add("user_main: performance event time counter test")
    add("Final memory usage:")
    add("user_main: Exiting")
    add("ostest_main: Exiting with status 0")
    return tuple(markers)


class SafetyError(ValueError):
    """The requested HIL operation is not sufficiently constrained."""


@dataclass(frozen=True)
class HilConfig:
    protocol: str
    port: str
    image: pathlib.Path
    loadp2: pathlib.Path
    toolchain_lock: pathlib.Path
    artifact_dir: pathlib.Path
    board_lock: pathlib.Path
    loader_baud: int
    console_baud: int
    reset_flag: str
    cycles: int
    timeout: float
    lock_timeout: float
    expected: Tuple[MarkerSpec, ...]
    reset_pattern: re.Pattern
    protocol_failure_patterns: Tuple[Tuple[str, re.Pattern], ...]
    protocol_warning_patterns: Tuple[Tuple[str, re.Pattern], ...]
    loadp2_script: str
    send_after_label: str
    send_payload: bytes
    require_after_send: Tuple[str, ...]
    reject_duplicate_markers: bool
    ostest_profile: str
    ostest_config_sha256: str
    ostest_debug_assertions: Optional[bool]
    smartpins_config_sha256: str
    smartpins_stages: Tuple[str, ...]
    storage_config_sha256: str
    storage_action: str
    storage_sequence: str
    storage_alternate_count: int
    psram_config_sha256: str
    psram_sequence: str
    psram_expected_fnv1a: str
    image_sha256: str
    loadp2_sha256: str


@dataclass(frozen=True)
class CycleResult:
    passed: bool
    reason: str
    elapsed: float
    raw_bytes: int
    loader_returncode: Optional[int]
    intentionally_terminated: bool
    warning_counts: Dict[str, int]


class MarkerParser:
    """Streaming marker parser which detects split markers and bad output."""

    def __init__(
        self,
        expected: Sequence[MarkerSpec],
        reset_pattern: re.Pattern = HELLO_MARKERS[0].pattern,
        protocol_failure_patterns: Sequence[
            Tuple[str, re.Pattern]
        ] = PROTOCOL_FAILURE_PATTERNS,
        warning_patterns: Sequence[Tuple[str, re.Pattern]] = (),
        reject_duplicates: bool = False,
    ) -> None:
        self.expected = tuple(expected)
        self.reset_pattern = reset_pattern
        self.protocol_failure_patterns = tuple(protocol_failure_patterns)
        self.warning_patterns = tuple(warning_patterns)
        self.found: Dict[str, int] = {}
        self.captures: Dict[str, str] = {}
        self.panic_marker: Optional[str] = None
        self.protocol_failure: Optional[str] = None
        self.disconnect_marker: Optional[str] = None
        self.duplicate_marker: Optional[str] = None
        self.reset_count = 0
        self.order_valid = True
        self.reject_duplicates = reject_duplicates
        self._marker_counts: Dict[str, int] = {}
        self._marker_starts: Dict[str, set] = {}
        self.warning_counts: Dict[str, int] = {}
        all_patterns = [spec.pattern for spec in self.expected]
        all_patterns.extend(pattern for _, pattern in PANIC_PATTERNS)
        all_patterns.extend(pattern for _, pattern in self.protocol_failure_patterns)
        all_patterns.extend(pattern for _, pattern in self.warning_patterns)
        all_patterns.extend(pattern for _, pattern in DISCONNECT_PATTERNS)
        longest = max((len(pattern.pattern) for pattern in all_patterns), default=64)
        self._overlap = max(4096, longest * 2)
        self._tail = ""
        self._total = 0

    def feed(self, text: str) -> None:
        if not text:
            return
        previous_total = self._total
        combined = self._tail + text
        base = previous_total - len(self._tail)

        for spec in self.expected:
            for match in spec.pattern.finditer(combined):
                absolute_start = base + match.start()
                absolute_end = base + match.end()
                if absolute_end > previous_total:
                    starts = self._marker_starts.setdefault(spec.label, set())
                    if absolute_start in starts:
                        # A line-ending pattern can first match at the end of
                        # one read, then match the same line again when CR/LF
                        # arrives in the next read.  Refresh captures if that
                        # match grew, but do not count one wire occurrence
                        # twice merely because it crossed a read boundary.

                        if self.found.get(spec.label) == absolute_start:
                            for name, value in match.groupdict().items():
                                if value is not None:
                                    self.captures[name] = value
                        continue

                    starts.add(absolute_start)
                    self._marker_counts[spec.label] = (
                        self._marker_counts.get(spec.label, 0) + 1
                    )
                    if spec.label not in self.found:
                        self.found[spec.label] = absolute_start
                        for name, value in match.groupdict().items():
                            if value is not None:
                                self.captures[name] = value
                    elif (
                        self.reject_duplicates
                        and not spec.repeatable
                        and self.duplicate_marker is None
                    ):
                        self.duplicate_marker = spec.label

        for match in self.reset_pattern.finditer(combined):
            if base + match.end() > previous_total:
                self.reset_count += 1

        if self.panic_marker is None:
            self.panic_marker = self._first_new_match(
                combined, base, previous_total, PANIC_PATTERNS
            )
        if self.protocol_failure is None:
            self.protocol_failure = self._first_new_match(
                combined, base, previous_total, self.protocol_failure_patterns
            )
        if self.disconnect_marker is None:
            self.disconnect_marker = self._first_new_match(
                combined, base, previous_total, DISCONNECT_PATTERNS
            )
        for label, pattern in self.warning_patterns:
            for match in pattern.finditer(combined):
                if base + match.end() > previous_total:
                    self.warning_counts[label] = (
                        self.warning_counts.get(label, 0) + 1
                    )

        self._total += len(text)
        self._tail = combined[-self._overlap :]
        offsets = [
            self.found[spec.label] for spec in self.expected if spec.label in self.found
        ]
        self.order_valid = offsets == sorted(offsets)

    @staticmethod
    def _first_new_match(
        combined: str,
        base: int,
        previous_total: int,
        patterns: Iterable[Tuple[str, re.Pattern]],
    ) -> Optional[str]:
        matches = []
        for label, pattern in patterns:
            for match in pattern.finditer(combined):
                if base + match.end() > previous_total:
                    matches.append((base + match.start(), label))
                    break
        return min(matches)[1] if matches else None

    @property
    def missing(self) -> Tuple[str, ...]:
        return tuple(
            spec.label for spec in self.expected if spec.label not in self.found
        )

    @property
    def complete(self) -> bool:
        return not self.missing and self.order_valid

    @property
    def failure_reason(self) -> Optional[str]:
        if self.panic_marker is not None:
            return "panic/assert marker observed: {}".format(self.panic_marker)
        if self.protocol_failure is not None:
            return "protocol failure observed: {}".format(self.protocol_failure)
        if self.disconnect_marker is not None:
            return "serial disconnect/load failure observed: {}".format(
                self.disconnect_marker
            )
        if self.reset_count > 1:
            return "unexpected entry/reset repetition: count={}".format(
                self.reset_count
            )
        if self.duplicate_marker is not None:
            return "duplicate protocol marker observed: {}".format(
                self.duplicate_marker
            )
        if not self.order_valid:
            return "protocol markers were observed out of order"
        return None

    def as_dict(self) -> Dict[str, object]:
        return {
            "complete": self.complete,
            "found": [spec.label for spec in self.expected if spec.label in self.found],
            "missing": list(self.missing),
            "captures": dict(sorted(self.captures.items())),
            "panic_marker": self.panic_marker,
            "protocol_failure": self.protocol_failure,
            "disconnect_marker": self.disconnect_marker,
            "duplicate_marker": self.duplicate_marker,
            "marker_counts": dict(sorted(self._marker_counts.items())),
            "warning_counts": dict(sorted(self.warning_counts.items())),
            "reset_count": self.reset_count,
            "order_valid": self.order_valid,
        }


class NormalizedLog:
    """Incrementally decode and UTC-prefix CR, LF, and CRLF console lines."""

    def __init__(self, output, utc_now: Callable[[], datetime.datetime]) -> None:
        self.output = output
        self.utc_now = utc_now
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.line: List[str] = []
        self.last_was_cr = False

    def feed(self, data: bytes) -> str:
        text = self.decoder.decode(data)
        self._consume(text)
        return text

    def finish(self) -> str:
        text = self.decoder.decode(b"", final=True)
        self._consume(text)
        if self.line:
            self._write_line("".join(self.line))
            self.line = []
        self.last_was_cr = False
        return text

    def _consume(self, text: str) -> None:
        for character in text:
            if character == "\n":
                if self.last_was_cr:
                    self.last_was_cr = False
                    continue
                self._write_line("".join(self.line))
                self.line = []
            elif character == "\r":
                self._write_line("".join(self.line))
                self.line = []
                self.last_was_cr = True
            else:
                self.last_was_cr = False
                self.line.append(character)

    def _write_line(self, line: str) -> None:
        self.output.write("[{}] {}\n".format(utc_timestamp(self.utc_now()), line))
        self.output.flush()


class PopenSession:
    """Nonblocking combined-output view of one loadp2 subprocess."""

    def __init__(self, command: Sequence[str]) -> None:
        self.process = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            close_fds=True,
        )
        if self.process.stdout is None:
            raise RuntimeError("loadp2 stdout pipe was not created")
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)

    def read(self, timeout: float) -> Optional[bytes]:
        events = self.selector.select(max(0.0, timeout))
        if not events:
            return b""
        data = os.read(self.process.stdout.fileno(), 65536)
        return data if data else None

    def poll(self) -> Optional[int]:
        return self.process.poll()

    def write(self, data: bytes) -> None:
        if self.process.stdin is None:
            raise RuntimeError("loadp2 stdin pipe was not created")
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def terminate(self) -> None:
        self.process.terminate()

    def kill(self) -> None:
        self.process.kill()

    def wait(self, timeout: Optional[float] = None) -> int:
        return self.process.wait(timeout=timeout)

    def close(self) -> None:
        self.selector.close()
        if self.process.stdin is not None:
            self.process.stdin.close()
        if self.process.stdout is not None:
            self.process.stdout.close()


def default_process_factory(command: Sequence[str]):
    return PopenSession(command)


def utc_timestamp(now: datetime.datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return (
        now.astimezone(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def run_stamp(now: datetime.datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return now.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_character_device(path: str) -> bool:
    try:
        return stat.S_ISCHR(os.stat(path).st_mode)
    except OSError:
        return False


def pinned_sha256(executable: pathlib.Path, lock_path: pathlib.Path) -> str:
    try:
        lines = lock_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SafetyError(
            "pinned toolchain lock is unavailable: {}".format(exc)
        ) from exc

    actual_path = executable.resolve()
    pattern = re.compile(r"^sha256=([0-9a-fA-F]{64})\s+(.+)$")
    expected = None
    for line in lines:
        match = pattern.match(line)
        if match is None:
            continue
        candidate = pathlib.Path(match.group(2)).expanduser()
        try:
            candidate = candidate.resolve()
        except OSError:
            continue
        if candidate == actual_path:
            expected = match.group(1).lower()
            break
    if expected is None:
        raise SafetyError(
            "LOADP2 is not pinned by {}: {}".format(lock_path, executable)
        )
    actual = sha256_file(executable)
    if actual != expected:
        raise SafetyError(
            "LOADP2 SHA-256 does not match {} (expected {}, got {})".format(
                lock_path, expected, actual
            )
        )
    return actual


def write_json(path: pathlib.Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def preserve_hil_inputs(config: HilConfig) -> Tuple[str, ...]:
    """Copy the exact volatile image inputs into the HIL artifact bundle."""

    input_dir = config.artifact_dir / "inputs"
    input_dir.mkdir()
    candidates = [config.image, config.toolchain_lock]
    copied = []

    for suffix in (".bin", ".map"):
        candidate = config.image.with_suffix(suffix)
        if candidate.is_file():
            candidates.append(candidate)

    if config.protocol == "context":
        context_dir = REPO_ROOT / "tools" / "p2" / "standalone" / "context"
        candidates.extend(
            context_dir / name
            for name in (
                "Makefile",
                "README.md",
                "context.c",
                "context.ld",
                "context_switch.S",
                "test_verify.py",
                "verify.py",
            )
        )
        candidates.extend(
            (
                REPO_ROOT / "arch" / "p2" / "include" / "context.h",
                REPO_ROOT / "arch" / "p2" / "src" / "common" / "p2_softarith.c",
                REPO_ROOT / "tools" / "p2" / "hil.py",
                REPO_ROOT / "tools" / "p2" / "test-context.py",
            )
        )

    if config.protocol in (
        "boot",
        "bringup",
        "nsh",
        "ostest",
        "smartpins",
        "storage",
        "psram",
    ):
        candidates.extend(
            (
                REPO_ROOT / ".config",
                REPO_ROOT / "System.map",
                REPO_ROOT / "tools" / "p2" / "bringup_protocol.py",
                REPO_ROOT / "tools" / "p2" / "hil.py",
                REPO_ROOT / "tools" / "p2" / "test-boot.py",
                REPO_ROOT / "tools" / "p2" / "test-bringup.py",
                REPO_ROOT / "tools" / "p2" / "test-nsh.py",
                REPO_ROOT / "tools" / "p2" / "test-ostest.py",
                REPO_ROOT / "tools" / "p2" / "test-smartpins.py",
                REPO_ROOT / "tools" / "p2" / "test-storage.py",
                REPO_ROOT / "tools" / "p2" / "smartpins_protocol.py",
                REPO_ROOT / "tools" / "p2" / "storage_plan.py",
                REPO_ROOT / "tools" / "p2" / "storage_protocol.py",
                REPO_ROOT / "tools" / "p2" / "test-flashfs.py",
                REPO_ROOT / "tools" / "p2" / "test-sd.py",
                REPO_ROOT / "tools" / "p2" / "test-psram.py",
                REPO_ROOT / "tools" / "p2" / "psram_protocol.py",
                REPO_ROOT / "tools" / "p2" / "verify-elf.py",
            )
        )

    if config.protocol == "ostest" and config.ostest_profile:
        candidates.append(ostest_profile_path(config.ostest_profile))

    if config.protocol == "storage" and config.storage_action:
        p2storage_dir = REPO_ROOT.parent / "apps" / "testing" / "p2storage"
        candidates.extend(
            p2storage_dir / name
            for name in ("Kconfig", "Make.defs", "Makefile", "p2storage_main.c")
        )

    if config.protocol == "psram":
        p2psram_dir = REPO_ROOT.parent / "apps" / "testing" / "p2psram"
        candidates.extend(
            p2psram_dir / name
            for name in ("Kconfig", "Make.defs", "Makefile", "p2psram_main.c")
        )
        board_dir = (
            REPO_ROOT / "boards" / "p2" / "p2x8c4m64p" / "p2-ec32mb"
        )
        candidates.extend(
            (
                board_dir / "Kconfig",
                board_dir / "configs" / "psram" / "defconfig",
                board_dir / "include" / "board.h",
                board_dir / "include" / "p2_ec32mb_psram.h",
                board_dir / "src" / "Makefile",
                board_dir / "src" / "p2_ec32mb_boot.c",
                board_dir / "src" / "p2_ec32mb_pins.c",
                board_dir / "src" / "p2_ec32mb_pins.h",
                board_dir / "src" / "p2_ec32mb_psram.c",
                board_dir / "src" / "p2_ec32mb_psram_logic.h",
                board_dir / "src" / "p2_ec32mb_psram_service.S",
                board_dir / "src" / "p2_ec32mb_psram_wire.h",
            )
        )

    used_names = set()
    for source in candidates:
        if not source.is_file():
            continue
        name = source.name
        if name in used_names:
            name = "{}-{}".format(source.parent.name, name)
        if name in used_names:
            raise SafetyError("duplicate HIL input basename: {}".format(name))
        used_names.add(name)
        destination = input_dir / name
        shutil.copy2(source, destination)
        copied.append(str(destination.relative_to(config.artifact_dir)))

    return tuple(copied)


def read_environment_file(
    path: pathlib.Path, values: Mapping[str, str]
) -> Dict[str, str]:
    """Read the simple assignment format emitted by the P2 bootstrap.

    This is intentionally not a shell evaluator.  Command substitutions,
    backticks, compound commands, and malformed assignments are refused.
    Previously parsed variables may be referenced as ``$NAME`` or
    ``${NAME}``, which is enough for both ``~/.p2-nuttx-env`` and
    ``.p2-hil.env``.
    """

    parsed = dict(values)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return parsed
    except OSError as exc:
        raise SafetyError(
            "cannot read environment file {}: {}".format(path, exc)
        ) from exc

    variable = re.compile(
        r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))"
    )
    assignment = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
    for number, original in enumerate(lines, 1):
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        match = assignment.match(line)
        if match is None or "$(" in line or "`" in line or ";" in line:
            raise SafetyError("unsupported assignment in {}:{}".format(path, number))
        name, encoded = match.groups()
        try:
            words = shlex.split(encoded, posix=True)
        except ValueError as exc:
            raise SafetyError("malformed value in {}:{}".format(path, number)) from exc
        if len(words) > 1:
            raise SafetyError("ambiguous value in {}:{}".format(path, number))
        value = words[0] if words else ""
        value = variable.sub(
            lambda found: parsed.get(found.group(1) or found.group(2), ""), value
        )
        parsed[name] = os.path.expanduser(value)
    return parsed


def local_environment(process_environment: Mapping[str, str]) -> Dict[str, str]:
    """Merge bootstrap and board env files below the real process env."""

    process_values = dict(process_environment)
    values = read_environment_file(pathlib.Path.home() / ".p2-nuttx-env", {})
    values = read_environment_file(REPO_ROOT / ".p2-hil.env", values)
    values.update(process_values)
    return values


def default_owner_probe(port: str) -> Tuple[int, ...]:
    try:
        result = subprocess.run(
            ["lsof", "-t", port],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise SafetyError(
            "cannot inspect serial ownership with lsof: {}".format(exc)
        ) from exc
    if result.returncode == 1:
        return ()
    if result.returncode != 0:
        detail = result.stderr.strip() or "exit {}".format(result.returncode)
        raise SafetyError("lsof serial-owner check failed: {}".format(detail))
    owners = []
    for line in result.stdout.splitlines():
        try:
            owners.append(int(line.strip()))
        except ValueError as exc:
            raise SafetyError(
                "lsof returned a non-PID owner: {!r}".format(line)
            ) from exc
    return tuple(owners)


def default_build_runner(protocol: str = "hello") -> int:
    if protocol in ("boot", "nsh"):
        return subprocess.run(
            [str(REPO_ROOT / "tools" / "p2" / "build.sh"), "nsh"],
            cwd=str(REPO_ROOT),
            check=False,
        ).returncode

    if protocol == "bringup":
        return subprocess.run(
            [str(REPO_ROOT / "tools" / "p2" / "build.sh"), "bringup"],
            cwd=str(REPO_ROOT),
            check=False,
        ).returncode

    if protocol == "smartpins":
        return subprocess.run(
            [str(REPO_ROOT / "tools" / "p2" / "build.sh"), "smartpins"],
            cwd=str(REPO_ROOT),
            check=False,
        ).returncode

    if protocol == "storage":
        return subprocess.run(
            [str(REPO_ROOT / "tools" / "p2" / "build.sh"), "storage"],
            cwd=str(REPO_ROOT),
            check=False,
        ).returncode

    if protocol == "psram":
        return subprocess.run(
            [str(REPO_ROOT / "tools" / "p2" / "build.sh"), "psram"],
            cwd=str(REPO_ROOT),
            check=False,
        ).returncode

    if protocol in OSTEST_PROFILES:
        return subprocess.run(
            [str(REPO_ROOT / "tools" / "p2" / "build.sh"), protocol],
            cwd=str(REPO_ROOT),
            check=False,
        ).returncode

    standalone = "context" if protocol == "context" else "hello"
    return subprocess.run(
        ["make", "-C", str(REPO_ROOT / "tools" / "p2" / "standalone" / standalone)],
        cwd=str(REPO_ROOT),
        check=False,
    ).returncode


def exact_hello_markers(extra_literals: Sequence[str]) -> Tuple[MarkerSpec, ...]:
    markers = list(HELLO_MARKERS)
    labels = {marker.label for marker in markers}
    for literal in extra_literals:
        if not literal:
            raise SafetyError("--expect cannot be empty")
        label = "literal:{}".format(literal)
        if label in labels:
            raise SafetyError("duplicate --expect marker: {}".format(literal))
        labels.add(label)
        markers.append(MarkerSpec(label, re.compile(re.escape(literal))))
    return tuple(markers)


def exact_protocol_markers(
    protocol: str,
    extra_literals: Sequence[str],
    ostest_config: Optional[Mapping[str, str]] = None,
    smartpins_stages: Sequence[str] = (),
    storage_action: str = "",
    storage_sequence: str = "",
    storage_alternate_count: int = STORAGE_ALTERNATE_TRANSACTIONS,
    psram_sequence: str = "",
) -> Tuple[MarkerSpec, ...]:
    if protocol == "hello":
        markers = list(HELLO_MARKERS)
    elif protocol == "context":
        markers = list(CONTEXT_MARKERS)
    elif protocol == "boot":
        markers = list(BOOT_MARKERS)
    elif protocol == "bringup":
        markers = list(BOOT_MARKERS + BRINGUP_APP_MARKERS)
    elif protocol == "nsh":
        markers = list(NSH_MARKERS)
    elif protocol == "ostest":
        if ostest_config is None:
            raise SafetyError("ostest requires the exact generated .config")
        markers = list(ostest_markers(ostest_config))
    elif protocol == "smartpins":
        markers = [
            MarkerSpec(label, pattern)
            for label, pattern in smartpins_marker_patterns(smartpins_stages)
        ]
    elif protocol == "storage":
        if storage_action:
            markers = list(STORAGE_ACTION_BOOT_MARKERS)
            markers.extend(
                MarkerSpec(label, pattern)
                for label, pattern in storage_response_marker_patterns(
                    storage_action,
                    storage_sequence or None,
                    storage_alternate_count,
                )
            )
        else:
            markers = list(STORAGE_MARKERS)
    elif protocol == "psram":
        markers = list(BOOT_MARKERS)
        markers.append(
            MarkerSpec(
                "nsh> prompt",
                re.compile(r"(?:^|[\r\n])nsh> ", re.MULTILINE),
                repeatable=True,
            )
        )
        markers.extend(
            MarkerSpec(label, pattern)
            for label, pattern in psram_marker_patterns(psram_sequence)
        )
    else:
        raise SafetyError("unsupported HIL protocol: {}".format(protocol))

    labels = {marker.label for marker in markers}
    for literal in extra_literals:
        if not literal:
            raise SafetyError("--expect cannot be empty")
        label = "literal:{}".format(literal)
        if label in labels:
            raise SafetyError("duplicate --expect marker: {}".format(literal))
        labels.add(label)
        markers.append(MarkerSpec(label, re.compile(re.escape(literal))))
    return tuple(markers)


def build_command(config: HilConfig) -> Tuple[str, ...]:
    command = [
        str(config.loadp2),
        "-p",
        config.port,
        "-l",
        str(config.loader_baud),
        "-b",
        str(config.console_baud),
        "-FIFO",
        str(LOADP2_FIFO_BYTES),
        "-ZERO",
        "-v",
        config.reset_flag,
    ]
    if config.loadp2_script:
        command.extend(("-e", config.loadp2_script))
    command.extend(("-t", str(config.image)))
    command = tuple(command)
    forbidden = {"-PATCH", "-FLASH"}
    if forbidden.intersection(command):
        raise SafetyError("forbidden loadp2 option entered the RAM-only command")
    if command[0] != str(config.loadp2) or command[-1] != str(config.image):
        raise SafetyError("loadp2 command path or image changed unexpectedly")
    if command.count("-DTR") + command.count("-RTS") != 1:
        raise SafetyError("loadp2 command must contain exactly one reset flag")
    return command


class HilRunner:
    def __init__(
        self,
        config: HilConfig,
        process_factory: Callable[[Sequence[str]], object] = default_process_factory,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(
            datetime.timezone.utc
        ),
        lock_factory: Callable[..., object] = monitor.BoardLock,
        owner_probe: Callable[[str], Tuple[int, ...]] = default_owner_probe,
    ) -> None:
        self.config = config
        self.process_factory = process_factory
        self.monotonic = monotonic
        self.utc_now = utc_now
        self.lock_factory = lock_factory
        self.owner_probe = owner_probe

    def run(self) -> bool:
        config = self.config
        if config.protocol == "ostest":
            config_path = REPO_ROOT / ".config"
            if sha256_file(config_path) != config.ostest_config_sha256:
                raise SafetyError(
                    "ostest .config changed after protocol derivation; refusing to load"
                )
        if config.protocol == "smartpins":
            config_path = REPO_ROOT / ".config"
            if sha256_file(config_path) != config.smartpins_config_sha256:
                raise SafetyError(
                    "smartpins .config changed after protocol derivation; refusing to load"
                )
        if config.protocol == "storage":
            config_path = REPO_ROOT / ".config"
            if sha256_file(config_path) != config.storage_config_sha256:
                raise SafetyError(
                    "storage .config changed after protocol derivation; refusing to load"
                )
        if config.protocol == "psram":
            config_path = REPO_ROOT / ".config"
            if sha256_file(config_path) != config.psram_config_sha256:
                raise SafetyError(
                    "psram .config changed after protocol derivation; refusing to load"
                )
        config.artifact_dir.mkdir(parents=True, exist_ok=False)
        preserved_inputs = preserve_hil_inputs(config)
        preserved_input_sha256 = {
            path: sha256_file(config.artifact_dir / path)
            for path in preserved_inputs
        }
        if config.protocol == "psram":
            copied_config = config.artifact_dir / "inputs" / ".config"
            if (
                not copied_config.is_file()
                or sha256_file(copied_config) != config.psram_config_sha256
            ):
                raise SafetyError(
                    "preserved psram .config does not match the validated profile"
                )
        started = self.utc_now()
        overall = {
            "status": "RUNNING",
            "started_utc": utc_timestamp(started),
            "cycles_requested": config.cycles,
            "protocol": config.protocol,
            "cycles_passed": 0,
            "port": config.port,
            "image": str(config.image),
            "image_sha256": config.image_sha256,
            "loadp2": str(config.loadp2),
            "loadp2_sha256": config.loadp2_sha256,
            "toolchain_lock": str(config.toolchain_lock),
            "board_lock": str(config.board_lock),
            "loader_baud": config.loader_baud,
            "loadp2_fifo_bytes": LOADP2_FIFO_BYTES,
            "console_baud": config.console_baud,
            "reset_flag": config.reset_flag,
            "timeout_seconds_per_cycle": config.timeout,
            "preserved_inputs": preserved_inputs,
            "preserved_input_sha256": preserved_input_sha256,
        }
        if config.protocol == "nsh":
            overall.update(
                {
                    "interactive_commands": list(NSH_COMMANDS),
                    "sleep_min_seconds": NSH_SLEEP_MIN_SECONDS,
                    "sleep_max_seconds": NSH_SLEEP_MAX_SECONDS,
                }
            )
        elif config.protocol == "ostest":
            overall.update(
                {
                    "ostest_profile": config.ostest_profile,
                    "debug_assertions": config.ostest_debug_assertions,
                    "ostest_config_sha256": config.ostest_config_sha256,
                    "required_groups": [marker.label for marker in config.expected],
                    "warning_policy": "hrtimer timing warnings are counted, not fatal",
                    "warning_counts": {},
                }
            )
        elif config.protocol == "smartpins":
            overall.update(
                {
                    "smartpins_config_sha256": config.smartpins_config_sha256,
                    "smartpins_stages": list(config.smartpins_stages),
                    "required_direct_loopbacks": [
                        "GPIO:P0-P1",
                        "UART:P2-P3",
                        "PWM_CAPTURE:P4-P5",
                        "SPI:P6-MOSI to P7-MISO; P8-SCK and P9-CS unconnected",
                    ],
                    "dac_adc_status": (
                        "DISABLED: direct P4-P5 jumper has no verified series resistance"
                    ),
                    "spi_status": (
                        "ENABLED: standard /dev/spi0 100-kHz mode-0 loopback"
                    ),
                }
            )
        elif config.protocol == "storage":
            overall.update(
                {
                    "storage_config_sha256": config.storage_config_sha256,
                    "storage_action": config.storage_action or None,
                    "storage_sequence": config.storage_sequence or None,
                    "storage_alternate_transactions": (
                        config.storage_alternate_count
                        if config.storage_action == "alternate"
                        else None
                    ),
                    "w25_raw_exposure": "private",
                    "smartfs_exposure": "/dev/smart0 data partition only",
                    "mmcsd_exposure": "/dev/mmcsd0 generic block device",
                    "filesystem_action": (
                        config.storage_action or "none; no mount or format"
                    ),
                    "automatic_format": False,
                    "flash_binding_side_effect": (
                        "w25_initialize may clear hardware protection status bits"
                    ),
                }
            )
        elif config.protocol == "psram":
            overall.update(
                {
                    "psram_config_sha256": config.psram_config_sha256,
                    "psram_sequence": config.psram_sequence,
                    "psram_expected_fnv1a": config.psram_expected_fnv1a,
                    "device": "/dev/psram0",
                    "external_bytes": 33554432,
                    "destructive": True,
                    "native_memory": False,
                }
            )
        write_json(config.artifact_dir / "metadata.json", overall)
        passed = 0
        try:
            with self.lock_factory(
                config.board_lock,
                timeout=config.lock_timeout,
                monotonic=self.monotonic,
            ):
                for cycle in range(1, config.cycles + 1):
                    owners = self.owner_probe(config.port)
                    if owners:
                        raise SafetyError(
                            "serial port is already owned by PID(s): {}".format(
                                ", ".join(str(owner) for owner in owners)
                            )
                        )
                    if sha256_file(config.image) != config.image_sha256:
                        raise SafetyError(
                            "image changed after validation; refusing to load"
                        )
                    if (
                        config.protocol == "ostest"
                        and sha256_file(REPO_ROOT / ".config")
                        != config.ostest_config_sha256
                    ):
                        raise SafetyError(
                            "ostest .config changed during the run; refusing to load"
                        )
                    if (
                        config.protocol == "smartpins"
                        and sha256_file(REPO_ROOT / ".config")
                        != config.smartpins_config_sha256
                    ):
                        raise SafetyError(
                            "smartpins .config changed during the run; refusing to load"
                        )
                    if (
                        config.protocol == "storage"
                        and sha256_file(REPO_ROOT / ".config")
                        != config.storage_config_sha256
                    ):
                        raise SafetyError(
                            "storage .config changed during the run; refusing to load"
                        )
                    if (
                        config.protocol == "psram"
                        and sha256_file(REPO_ROOT / ".config")
                        != config.psram_config_sha256
                    ):
                        raise SafetyError(
                            "psram .config changed during the run; refusing to load"
                        )
                    result = self._run_cycle(cycle)
                    for label, count in result.warning_counts.items():
                        warning_counts = overall.setdefault("warning_counts", {})
                        warning_counts[label] = warning_counts.get(label, 0) + count
                    if not result.passed:
                        overall["failure_reason"] = result.reason
                        break
                    passed += 1
        except (SafetyError, monitor.ConfigurationError) as exc:
            overall["failure_reason"] = monitor.safe_error(exc)
            raise
        finally:
            overall["cycles_passed"] = passed
            overall["ended_utc"] = utc_timestamp(self.utc_now())
            overall["status"] = "PASS" if passed == config.cycles else "FAIL"
            if config.protocol == "ostest":
                write_json(config.artifact_dir / "metadata.json", overall)
            write_json(config.artifact_dir / "status.json", overall)
        return passed == config.cycles

    def _run_cycle(self, cycle: int) -> CycleResult:
        config = self.config
        cycle_dir = config.artifact_dir / "cycle-{:03d}".format(cycle)
        cycle_dir.mkdir(parents=False, exist_ok=False)
        command = build_command(config)
        started_utc = self.utc_now()
        started = self.monotonic()
        parser = MarkerParser(
            config.expected,
            config.reset_pattern,
            config.protocol_failure_patterns,
            warning_patterns=config.protocol_warning_patterns,
            reject_duplicates=config.reject_duplicate_markers,
        )
        protocol_text: List[str] = []
        smartpins_result = None
        storage_result = None
        psram_result = None
        raw_bytes = 0
        returncode = None
        intentionally_terminated = False
        interactive_send_completed = False
        marker_elapsed: Dict[str, float] = {}
        nsh_sleep_elapsed = None
        passed = False
        reason = "loadp2 did not start"
        session = None

        command_record = {
            "argv": list(command),
            "shell_escaped": shlex.join(command),
            "loadp2_script": config.loadp2_script,
            "interactive_send_after": config.send_after_label or None,
            "interactive_send_ascii": (
                config.send_payload.decode("ascii") if config.send_payload else None
            ),
            "interactive_commands": (
                list(NSH_COMMANDS)
                if config.protocol == "nsh"
                else (
                    [config.send_payload.decode("ascii").rstrip("\r")]
                    if config.storage_action or config.protocol == "psram"
                    else None
                )
            ),
        }
        write_json(cycle_dir / "command.json", command_record)
        metadata = {
            "cycle": cycle,
            "started_utc": utc_timestamp(started_utc),
            "port": config.port,
            "image": str(config.image),
            "image_sha256": config.image_sha256,
            "image_size": config.image.stat().st_size,
            "loadp2": str(config.loadp2),
            "loadp2_sha256": config.loadp2_sha256,
            "loader_baud": config.loader_baud,
            "loadp2_fifo_bytes": LOADP2_FIFO_BYTES,
            "console_baud": config.console_baud,
            "reset_flag": config.reset_flag,
            "timeout_seconds": config.timeout,
        }
        if config.protocol == "nsh":
            metadata.update(
                {
                    "sleep_min_seconds": NSH_SLEEP_MIN_SECONDS,
                    "sleep_max_seconds": NSH_SLEEP_MAX_SECONDS,
                }
            )
        elif config.storage_action:
            metadata.update(
                {
                    "storage_action": config.storage_action,
                    "storage_sequence": config.storage_sequence or None,
                    "automatic_format": False,
                }
            )
        elif config.protocol == "psram":
            metadata.update(
                {
                    "psram_sequence": config.psram_sequence,
                    "psram_expected_fnv1a": config.psram_expected_fnv1a,
                    "device": "/dev/psram0",
                    "external_bytes": 33554432,
                }
            )
        write_json(cycle_dir / "metadata.json", metadata)

        try:
            with (cycle_dir / "console.raw").open("wb") as raw_log, (
                cycle_dir / "console.log"
            ).open("w", encoding="utf-8", newline="\n") as normalized_file:
                normalizer = NormalizedLog(normalized_file, self.utc_now)
                try:
                    session = self.process_factory(command)
                    deadline = started + config.timeout
                    while True:
                        remaining = deadline - self.monotonic()
                        if remaining <= 0:
                            reason = "bounded timeout; missing {}".format(
                                ", ".join(parser.missing)
                            )
                            break
                        chunk = session.read(min(0.10, remaining))
                        if chunk is None:
                            returncode = self._wait_after_eof(session)
                            if returncode not in (None, 0):
                                reason = "loadp2 exited with code {}".format(returncode)
                            else:
                                reason = (
                                    "loadp2 terminal disconnected before protocol "
                                    "completed"
                                )
                            break
                        if chunk:
                            if not isinstance(chunk, (bytes, bytearray)):
                                reason = "loadp2 output reader returned non-bytes"
                                break
                            data = bytes(chunk)
                            raw_log.write(data)
                            raw_log.flush()
                            raw_bytes += len(data)
                            decoded = normalizer.feed(data)
                            if (config.protocol in ("smartpins", "psram") or
                                    config.storage_action):
                                protocol_text.append(decoded)
                            previously_found = set(parser.found)
                            parser.feed(decoded)
                            observed = self.monotonic()
                            for label in parser.found:
                                if label not in previously_found:
                                    marker_elapsed[label] = max(0.0, observed - started)
                            failure = parser.failure_reason
                            if failure is not None:
                                reason = failure
                                break
                            if (
                                config.send_after_label
                                and not interactive_send_completed
                                and config.send_after_label in parser.found
                            ):
                                # Do not accept response-looking text that was
                                # received before this cycle's command was sent.
                                for label in config.require_after_send:
                                    parser.found.pop(label, None)
                                    marker_elapsed.pop(label, None)
                                if config.protocol == "psram":
                                    protocol_text.clear()
                                session.write(config.send_payload)
                                interactive_send_completed = True
                            if (
                                config.protocol == "nsh"
                                and NSH_SLEEP_START_LABEL in marker_elapsed
                                and NSH_SLEEP_DONE_LABEL in marker_elapsed
                            ):
                                nsh_sleep_elapsed = (
                                    marker_elapsed[NSH_SLEEP_DONE_LABEL]
                                    - marker_elapsed[NSH_SLEEP_START_LABEL]
                                )
                                if not (
                                    NSH_SLEEP_MIN_SECONDS
                                    <= nsh_sleep_elapsed
                                    <= NSH_SLEEP_MAX_SECONDS
                                ):
                                    reason = (
                                        "sleep 1 timing outside [{:.2f}, {:.2f}] "
                                        "seconds: {:.6f} seconds"
                                    ).format(
                                        NSH_SLEEP_MIN_SECONDS,
                                        NSH_SLEEP_MAX_SECONDS,
                                        nsh_sleep_elapsed,
                                    )
                                    break
                            if parser.complete:
                                if config.protocol == "smartpins":
                                    smartpins_result = parse_smartpins(
                                        "".join(protocol_text),
                                        config.smartpins_stages,
                                    )
                                    if not smartpins_result["complete"]:
                                        reason = (
                                            "Smart Pin protocol validation failed: {}"
                                        ).format(
                                            "; ".join(
                                                smartpins_result["errors"]
                                                + smartpins_result["duplicates"]
                                                + [
                                                    item["line"]
                                                    for item in smartpins_result[
                                                        "failures"
                                                    ]
                                                ]
                                            )
                                        )
                                        break
                                if config.storage_action:
                                    storage_result = parse_storage_response(
                                        "".join(protocol_text),
                                        config.storage_action,
                                        config.storage_sequence or None,
                                        config.storage_alternate_count,
                                    )
                                    if not storage_result["complete"]:
                                        reason = (
                                            "storage protocol validation failed: {}"
                                        ).format(storage_first_error(storage_result))
                                        break
                                if config.protocol == "psram":
                                    psram_result = parse_psram(
                                        "".join(protocol_text),
                                        config.psram_sequence,
                                    )
                                    if not psram_result["complete"]:
                                        reason = (
                                            "PSRAM protocol validation failed: {}"
                                        ).format("; ".join(psram_result["errors"]))
                                        break
                                returncode = session.poll()
                                if returncode is not None:
                                    if returncode != 0:
                                        reason = "loadp2 exited with code {}".format(
                                            returncode
                                        )
                                    else:
                                        reason = (
                                            "loadp2 terminal disconnected after markers"
                                        )
                                    break
                                passed = True
                                reason = "all required {} markers observed".format(
                                    config.protocol
                                )
                                intentionally_terminated = True
                                break
                        else:
                            returncode = session.poll()
                            if returncode is not None:
                                if returncode != 0:
                                    reason = "loadp2 exited with code {}".format(
                                        returncode
                                    )
                                else:
                                    reason = (
                                        "loadp2 terminal disconnected before protocol "
                                        "completed"
                                    )
                                break
                except (OSError, RuntimeError, ValueError) as exc:
                    reason = "loadp2 process I/O failed: {}".format(
                        monitor.safe_error(exc)
                    )
                finally:
                    trailing = normalizer.finish()
                    if trailing:
                        parser.feed(trailing)
                        if (config.protocol in ("smartpins", "psram") or
                                config.storage_action):
                            protocol_text.append(trailing)
        finally:
            if session is not None:
                returncode = self._stop_session(session, returncode)

        elapsed = max(0.0, self.monotonic() - started)
        marker_status = parser.as_dict()
        if config.protocol == "smartpins":
            if smartpins_result is None:
                smartpins_result = parse_smartpins(
                    "".join(protocol_text), config.smartpins_stages
                )
            marker_status["smartpins_protocol"] = smartpins_result
        if config.storage_action:
            if storage_result is None:
                storage_result = parse_storage_response(
                    "".join(protocol_text),
                    config.storage_action,
                    config.storage_sequence or None,
                    config.storage_alternate_count,
                )
            marker_status["storage_protocol"] = storage_result
        if config.protocol == "psram":
            if psram_result is None:
                psram_result = parse_psram(
                    "".join(protocol_text), config.psram_sequence
                )
            marker_status["psram_protocol"] = psram_result
        marker_status["observed_after_start_seconds"] = {
            label: round(value, 6) for label, value in marker_elapsed.items()
        }
        marker_status["nsh_sleep_elapsed_seconds"] = (
            round(nsh_sleep_elapsed, 6) if nsh_sleep_elapsed is not None else None
        )
        write_json(cycle_dir / "markers.json", marker_status)
        status = {
            "status": "PASS" if passed else "FAIL",
            "reason": reason,
            "elapsed_seconds": round(elapsed, 6),
            "raw_bytes": raw_bytes,
            "loader_returncode": returncode,
            "intentionally_terminated": intentionally_terminated,
            "interactive_send_completed": interactive_send_completed,
            "warning_counts": dict(sorted(parser.warning_counts.items())),
            "nsh_sleep_elapsed_seconds": (
                round(nsh_sleep_elapsed, 6)
                if nsh_sleep_elapsed is not None
                else None
            ),
            "ended_utc": utc_timestamp(self.utc_now()),
        }
        write_json(cycle_dir / "status.json", status)
        return CycleResult(
            passed,
            reason,
            elapsed,
            raw_bytes,
            returncode,
            intentionally_terminated,
            dict(sorted(parser.warning_counts.items())),
        )

    @staticmethod
    def _wait_after_eof(session) -> Optional[int]:
        result = session.poll()
        if result is not None:
            return result
        try:
            return session.wait(timeout=0.20)
        except subprocess.TimeoutExpired:
            return None

    @staticmethod
    def _stop_session(session, known_returncode: Optional[int]) -> Optional[int]:
        try:
            current = session.poll()
            if current is None:
                session.terminate()
                try:
                    current = session.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    session.kill()
                    current = session.wait(timeout=1.0)
            return current if current is not None else known_returncode
        finally:
            session.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RAM-load and verify a native P2 standalone or NuttX protocol",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--protocol",
        choices=(
            "hello",
            "context",
            "boot",
            "bringup",
            "nsh",
            "ostest",
            "smartpins",
            "storage",
            "psram",
        ),
        default="hello",
    )
    parser.add_argument("--port")
    parser.add_argument("--image")
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--lock-timeout", type=float, default=0.0)
    parser.add_argument("--loader-baud", type=int)
    parser.add_argument("--console-baud", type=int)
    parser.add_argument("--reset-method", choices=("loadp2", "dtr", "rts"))
    parser.add_argument("--artifact-dir")
    parser.add_argument(
        "--build-standalone",
        "--build",
        dest="build_standalone",
        action="store_true",
        help="build the selected standalone test or NuttX configuration",
    )
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="LITERAL",
        help="additional literal marker required after the fixed protocol",
    )
    parser.add_argument(
        "--ostest-assertions",
        choices=("any", "enabled", "disabled"),
        default="any",
        help="require the generated ostest image assertion mode",
    )
    parser.add_argument(
        "--ostest-profile",
        choices=tuple(OSTEST_PROFILES),
        help="exact ostest defconfig identity to build and verify",
    )
    parser.add_argument(
        "--storage-action",
        choices=(
            "probe",
            "flash-format",
            "flash-write",
            "flash-verify",
            "flash-cycle",
            "flash-full",
            "flash-interrupt-arm",
            "flash-interrupt-verify",
            "sd-format",
            "sd-write",
            "sd-verify",
            "sd-rename-delete",
            "sd-stress",
            "alternate",
        ),
        help="one exact p2storage command to send after the NSH prompt",
    )
    parser.add_argument(
        "--storage-sequence",
        help="exact 8-uppercase-hex nonce for a sequenced storage action",
    )
    parser.add_argument(
        "--psram-sequence",
        help="exact 8-uppercase-hex nonce for the destructive PSRAM protocol",
    )
    return parser


def config_from_args(
    args,
    env: Mapping[str, str],
    utc_now: Callable[[], datetime.datetime],
    port_validator: Callable[[str], bool],
) -> HilConfig:
    ostest_config = None
    ostest_config_sha = ""
    ostest_debug_assertions = None
    smartpins_config_sha = ""
    smartpins_stages: Tuple[str, ...] = ()
    storage_config_sha = ""
    storage_action = args.storage_action or ""
    storage_sequence = ""
    psram_config_sha = ""
    psram_sequence = ""
    psram_expected_hash = ""
    if args.protocol == "ostest":
        if args.ostest_profile is None:
            raise SafetyError("ostest requires an explicit --ostest-profile")
        ostest_config_path = REPO_ROOT / ".config"
        ostest_config = read_kconfig(ostest_config_path)
        validate_ostest_config(
            ostest_config, args.ostest_assertions, args.ostest_profile
        )
        validate_ostest_profile_values(ostest_config, args.ostest_profile)
        ostest_config_sha = sha256_file(ostest_config_path)
        ostest_debug_assertions = kconfig_enabled(
            ostest_config, "CONFIG_DEBUG_ASSERTIONS"
        )
    elif args.ostest_assertions != "any":
        raise SafetyError("--ostest-assertions is valid only for ostest")
    elif args.ostest_profile is not None:
        raise SafetyError("--ostest-profile is valid only for ostest")

    if args.protocol == "smartpins":
        if env.get("P2_ALLOW_LOOPBACK_TESTS", "0") != "1":
            raise SafetyError(
                "smartpins requires P2_ALLOW_LOOPBACK_TESTS=1"
            )
        smartpins_config_path = REPO_ROOT / ".config"
        smartpins_config = read_kconfig(smartpins_config_path)
        smartpins_stages = validate_smartpins_config(smartpins_config)
        smartpins_config_sha = sha256_file(smartpins_config_path)

    if args.protocol == "storage":
        if env.get("P2_ALLOW_FLASH_WRITE", "0") != "1":
            raise SafetyError(
                "storage binding requires P2_ALLOW_FLASH_WRITE=1 because W25 initialization may clear protection bits"
            )
        if storage_action:
            if args.cycles != 1:
                raise SafetyError("a storage action requires exactly one reset cycle")
            needs_sequence = storage_sequence_required(storage_action)
            if needs_sequence and not args.storage_sequence:
                raise SafetyError(
                    "{} requires --storage-sequence".format(storage_action)
                )
            if not needs_sequence and args.storage_sequence:
                raise SafetyError(
                    "{} does not accept --storage-sequence".format(storage_action)
                )
            if args.storage_sequence:
                try:
                    storage_sequence = normalize_storage_sequence(
                        args.storage_sequence
                    )
                except ValueError as exc:
                    raise SafetyError(str(exc)) from exc
            if storage_action in FLASH_WRITABLE_ACTIONS:
                if env.get("P2_ALLOW_FLASH_ERASE", "0") != "1":
                    raise SafetyError(
                        "{} requires P2_ALLOW_FLASH_WRITE=1 and "
                        "P2_ALLOW_FLASH_ERASE=1".format(storage_action)
                    )
            if (
                storage_action in SD_DESTRUCTIVE_ACTIONS
                and env.get("P2_ALLOW_SD_DESTRUCTIVE", "0") != "1"
            ):
                raise SafetyError(
                    "{} requires P2_ALLOW_SD_DESTRUCTIVE=1".format(
                        storage_action
                    )
                )
        storage_config_path = REPO_ROOT / ".config"
        storage_config = read_kconfig(storage_config_path)
        validate_storage_config(storage_config)
        if storage_action:
            validate_storage_action_config(storage_config)
        storage_config_sha = sha256_file(storage_config_path)
    elif args.storage_action is not None or args.storage_sequence is not None:
        raise SafetyError(
            "--storage-action and --storage-sequence are valid only for storage"
        )

    if args.protocol == "psram":
        if env.get("P2_ALLOW_PSRAM_WRITE", "0") != "1":
            raise SafetyError("psram requires P2_ALLOW_PSRAM_WRITE=1")
        if args.cycles != 1:
            raise SafetyError("the destructive PSRAM protocol requires one cycle")
        if args.psram_sequence is None:
            raise SafetyError("psram requires --psram-sequence")
        try:
            psram_sequence = normalize_psram_sequence(args.psram_sequence)
        except ValueError as exc:
            raise SafetyError(str(exc)) from exc
        psram_config_path = REPO_ROOT / ".config"
        psram_config = read_kconfig(psram_config_path)
        validate_psram_config(psram_config)
        psram_config_sha = sha256_file(psram_config_path)
    elif args.psram_sequence is not None:
        raise SafetyError("--psram-sequence is valid only for psram")

    env_port = env.get("P2_PORT", "")
    if not env_port:
        raise SafetyError("P2_PORT must name the exact serial device")
    port = args.port or env_port
    if port != env_port:
        raise SafetyError("--port must exactly match P2_PORT")
    if not pathlib.Path(port).is_absolute():
        raise SafetyError("P2_PORT must be an absolute device path")
    if not port_validator(port):
        raise SafetyError(
            "serial device is absent or not a character device: {}".format(port)
        )

    loadp2_text = env.get("LOADP2", "")
    if not loadp2_text:
        raise SafetyError("LOADP2 must name the pinned loader executable")
    loadp2 = pathlib.Path(loadp2_text).expanduser()
    if not loadp2.is_absolute():
        raise SafetyError("LOADP2 must be an absolute path")
    try:
        loadp2 = loadp2.resolve(strict=True)
    except OSError as exc:
        raise SafetyError("pinned LOADP2 is unavailable: {}".format(exc)) from exc
    if not loadp2.is_file() or not os.access(loadp2, os.X_OK):
        raise SafetyError("pinned LOADP2 is not an executable file: {}".format(loadp2))

    toolchain_lock = pathlib.Path(
        env.get("P2_TOOLCHAIN_LOCK", str(DEFAULT_TOOLCHAIN_LOCK))
    ).expanduser()
    try:
        toolchain_lock = toolchain_lock.resolve(strict=True)
    except OSError as exc:
        raise SafetyError("toolchain lock is unavailable: {}".format(exc)) from exc
    loadp2_sha = pinned_sha256(loadp2, toolchain_lock)

    if args.image is not None:
        image_text = args.image
    elif args.protocol == "context":
        image_text = str(DEFAULT_CONTEXT_IMAGE)
    elif args.protocol in (
        "boot",
        "bringup",
        "nsh",
        "ostest",
        "smartpins",
        "storage",
        "psram",
    ):
        image_text = str(DEFAULT_NUTTX_IMAGE)
    else:
        image_text = str(DEFAULT_IMAGE)

    image = pathlib.Path(image_text).expanduser()
    try:
        image = image.resolve(strict=True)
    except OSError as exc:
        raise SafetyError("P2 image is unavailable: {}".format(exc)) from exc
    if not image.is_file() or image.stat().st_size == 0:
        raise SafetyError("P2 image is missing or empty: {}".format(image))
    with image.open("rb") as source:
        if source.read(4) != b"\x7fELF":
            raise SafetyError("P2 image is not an ELF file: {}".format(image))
    if image == loadp2:
        raise SafetyError("P2 image cannot be the LOADP2 executable")
    image_sha = sha256_file(image)
    if args.protocol == "psram":
        psram_expected_hash = "{:08X}".format(
            psram_expected_fnv1a(psram_sequence)
        )

    loader_baud = args.loader_baud or int(env.get("P2_LOADER_BAUD", "2000000"))
    console_baud = args.console_baud or int(env.get("P2_CONSOLE_BAUD", "230400"))
    if loader_baud <= 0 or console_baud <= 0:
        raise SafetyError("loader and console baud must be greater than zero")
    if args.cycles <= 0 or args.cycles > 100:
        raise SafetyError("--cycles must be in the range 1..100")
    maximum_timeout = (
        3600
        if args.protocol == "storage"
        else (
            3600
            if args.protocol == "ostest"
            else (1800 if args.protocol == "psram" else 600)
        )
    )
    if args.timeout <= 0 or args.timeout > maximum_timeout:
        raise SafetyError(
            "--timeout must be in the range (0, {}]".format(maximum_timeout)
        )
    if args.lock_timeout < 0:
        raise SafetyError("--lock-timeout cannot be negative")

    env_reset = env.get("P2_RESET_METHOD", "loadp2").lower()
    reset_method = args.reset_method or env_reset
    if args.reset_method is not None and args.reset_method != env_reset:
        raise SafetyError("--reset-method must exactly match P2_RESET_METHOD")
    if reset_method in ("loadp2", "dtr"):
        reset_flag = "-DTR"
    elif reset_method == "rts":
        reset_flag = "-RTS"
    else:
        raise SafetyError("P2_RESET_METHOD must be loadp2, dtr, or rts")

    board_lock = (
        pathlib.Path(env.get("P2_LOCK_FILE", str(DEFAULT_LOCK_FILE)))
        .expanduser()
        .resolve()
    )
    if args.artifact_dir:
        artifact_dir = pathlib.Path(args.artifact_dir).expanduser().resolve()
    else:
        artifact_dir = (
            REPO_ROOT
            / "artifacts"
            / "hil"
            / "{}-{}".format(run_stamp(utc_now()), args.protocol)
        )
    if artifact_dir.exists():
        raise SafetyError("artifact directory already exists: {}".format(artifact_dir))

    return HilConfig(
        protocol=args.protocol,
        port=port,
        image=image,
        loadp2=loadp2,
        toolchain_lock=toolchain_lock,
        artifact_dir=artifact_dir,
        board_lock=board_lock,
        loader_baud=loader_baud,
        console_baud=console_baud,
        reset_flag=reset_flag,
        cycles=args.cycles,
        timeout=args.timeout,
        lock_timeout=args.lock_timeout,
        expected=exact_protocol_markers(
            args.protocol,
            args.expect,
            ostest_config=ostest_config,
            smartpins_stages=smartpins_stages,
            storage_action=storage_action,
            storage_sequence=storage_sequence,
            psram_sequence=psram_sequence,
        ),
        reset_pattern=(
            CONTEXT_MARKERS[0].pattern
            if args.protocol == "context"
            else (
                BOOT_MARKERS[0].pattern
                if args.protocol in (
                    "boot",
                    "bringup",
                    "nsh",
                    "ostest",
                    "storage",
                    "psram",
                )
                else (
                    smartpins_marker_patterns(smartpins_stages)[0][1]
                    if args.protocol == "smartpins"
                    else HELLO_MARKERS[0].pattern
                )
            )
        ),
        protocol_failure_patterns=(
            CONTEXT_FAILURE_PATTERNS
            if args.protocol == "context"
            else (
                NSH_FAILURE_PATTERNS
                if args.protocol == "nsh"
                else (
                    ostest_failure_patterns(ostest_config)
                    if args.protocol == "ostest"
                    else (
                        BRINGUP_FAILURE_PATTERNS
                        if args.protocol == "bringup"
                        else (
                            BOOT_FAILURE_PATTERNS
                            if args.protocol == "boot"
                            else (
                                STORAGE_FAILURE_PATTERNS
                                if args.protocol == "storage"
                                else (
                                    BOOT_FAILURE_PATTERNS + PSRAM_FAILURE_PATTERNS
                                    if args.protocol == "psram"
                                    else (
                                        SMARTPINS_FAILURE_PATTERNS
                                        if args.protocol == "smartpins"
                                        else PROTOCOL_FAILURE_PATTERNS
                                    )
                                )
                            )
                        )
                    )
                )
            )
        ),
        protocol_warning_patterns=(
            OSTEST_WARNING_PATTERNS if args.protocol == "ostest" else ()
        ),
        loadp2_script=LOADP2_SCRIPT if args.protocol == "hello" else "",
        send_after_label=(
            "nsh> prompt"
            if (args.protocol == "nsh" or storage_action or
                args.protocol == "psram")
            else ""
        ),
        send_payload=(
            NSH_COMMAND_BYTES
            if args.protocol == "nsh"
            else (
                storage_command_bytes(
                    storage_action, storage_sequence or None
                )
                if storage_action
                else (
                    psram_command_bytes(psram_sequence)
                    if args.protocol == "psram"
                    else b""
                )
            )
        ),
        require_after_send=(
            tuple(marker.label for marker in NSH_COMMAND_MARKERS)
            if args.protocol == "nsh"
            else (
                tuple(
                    label
                    for label, pattern in storage_response_marker_patterns(
                        storage_action,
                        storage_sequence or None,
                        STORAGE_ALTERNATE_TRANSACTIONS,
                    )
                )
                if storage_action
                else (
                    tuple(label for label, _pattern in
                          psram_marker_patterns(psram_sequence))
                    if args.protocol == "psram"
                    else ()
                )
            )
        ),
        reject_duplicate_markers=args.protocol in (
            "ostest",
            "smartpins",
            "storage",
            "psram",
        ),
        ostest_profile=args.ostest_profile or "",
        ostest_config_sha256=ostest_config_sha,
        ostest_debug_assertions=ostest_debug_assertions,
        smartpins_config_sha256=smartpins_config_sha,
        smartpins_stages=smartpins_stages,
        storage_config_sha256=storage_config_sha,
        storage_action=storage_action,
        storage_sequence=storage_sequence,
        storage_alternate_count=STORAGE_ALTERNATE_TRANSACTIONS,
        psram_config_sha256=psram_config_sha,
        psram_sequence=psram_sequence,
        psram_expected_fnv1a=psram_expected_hash,
        image_sha256=image_sha,
        loadp2_sha256=loadp2_sha,
    )


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
    process_factory: Callable[[Sequence[str]], object] = default_process_factory,
    monotonic: Callable[[], float] = time.monotonic,
    utc_now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(
        datetime.timezone.utc
    ),
    lock_factory: Callable[..., object] = monitor.BoardLock,
    owner_probe: Callable[[str], Tuple[int, ...]] = default_owner_probe,
    build_runner: Callable[[str], int] = default_build_runner,
    port_validator: Callable[[str], bool] = is_character_device,
) -> int:
    args = build_parser().parse_args(argv)
    environment = local_environment(os.environ) if env is None else env
    if not args.execute:
        print(
            "DRY-RUN: no build, serial open, reset, or load was performed; "
            "pass --execute",
            file=sys.stderr,
        )
        return EXIT_SAFETY
    if environment.get("P2_HIL", "0") != "1":
        print("HIL REQUIRED: set P2_HIL=1 before --execute", file=sys.stderr)
        return EXIT_SAFETY

    try:
        if args.build_standalone:
            build_target = (
                args.ostest_profile
                if args.protocol == "ostest"
                else args.protocol
            )
            if build_target is None:
                raise SafetyError("ostest build requires --ostest-profile")
            build_rc = build_runner(build_target)
            if build_rc != 0:
                raise SafetyError(
                    "{} build failed with exit code {}".format(
                        build_target, build_rc
                    )
                )
        config = config_from_args(args, environment, utc_now, port_validator)
        runner = HilRunner(
            config,
            process_factory=process_factory,
            monotonic=monotonic,
            utc_now=utc_now,
            lock_factory=lock_factory,
            owner_probe=owner_probe,
        )
        return EXIT_OK if runner.run() else EXIT_HIL_FAILURE
    except monitor.LockBusyError as exc:
        print("LOCK BUSY: {}".format(exc), file=sys.stderr)
        return EXIT_LOCK_BUSY
    except (SafetyError, monitor.ConfigurationError) as exc:
        print("SAFETY REFUSAL: {}".format(exc), file=sys.stderr)
        return EXIT_SAFETY
    except KeyboardInterrupt:
        print("INTERRUPTED", file=sys.stderr)
        return EXIT_INTERRUPTED
    except OSError as exc:
        print("I/O ERROR: {}".format(monitor.safe_error(exc)), file=sys.stderr)
        return EXIT_HIL_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
