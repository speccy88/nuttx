#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the exact EC32MB showcase image through one guarded HIL session.

The loader owns the serial port from reset through the final prompt.  This is
intentional: opening the PropPlug a second time can toggle DTR and discard a
RAM-loaded image.  The runner never formats or writes flash or microSD.
"""

import argparse
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


P2_TOOLS = pathlib.Path(__file__).resolve().parent
REPO_ROOT = P2_TOOLS.parents[1]
sys.path.insert(0, str(P2_TOOLS))

import build_artifact  # noqa: E402
import hil  # noqa: E402
import i2c_protocol  # noqa: E402
import monitor  # noqa: E402
import psram_protocol  # noqa: E402
import smartpins_protocol  # noqa: E402
import storage_protocol  # noqa: E402


FORMAT = "p2-showcase-hil-v1"
EXIT_OK = 0
EXIT_CONFIGURATION = 2
EXIT_HIL_FAILURE = 3
BOARD = "p2-ec32mb"
PROFILE = "showcase"
EXPECTED_SMARTPIN_CAPS = (
    "GPIO",
    "EDGE",
    "UART",
    "PWM_CAPTURE",
    "DAC_ADC",
    "SPI",
)
PROMPT_PATTERN = re.compile(r"^(?:\x1b\[K)?nsh> ", re.MULTILINE)

BOOT_PATTERNS = (
    ("P2BOOT:ENTRY", re.compile(r"^P2BOOT:ENTRY\r?$", re.MULTILINE)),
    ("P2BOOT:DATA=OK", re.compile(r"^P2BOOT:DATA=OK\r?$", re.MULTILINE)),
    ("P2BOOT:BSS=OK", re.compile(r"^P2BOOT:BSS=OK\r?$", re.MULTILINE)),
    ("P2BOOT:NX_START", re.compile(r"^P2BOOT:NX_START\r?$", re.MULTILINE)),
    (
        "P2SHOWCASE:READY",
        re.compile(
            r"^P2SHOWCASE:READY:BOARD=p2-ec32mb:RUN=p2help\r?$",
            re.MULTILINE,
        ),
    ),
    ("nsh> prompt", PROMPT_PATTERN),
)

FAILURE_PATTERNS = (
    ("P2 boot data/BSS failure", re.compile(r"P2BOOT:(?:DATA|BSS)=FAIL")),
    ("P2 showcase failure", re.compile(r"P2SHOWCASE:[^\r\n]*FAIL")),
    ("P2 Smart Pin failure", re.compile(r"P2SMART:FAIL(?:[:=]|$)")),
    ("P2 I2C failure", re.compile(r"P2I2C:FAIL(?:[:=]|$)")),
    ("P2 storage failure", re.compile(r"P2STORAGE:[^\r\n]*FAIL")),
    ("P2 flash-boot mount failure", re.compile(r"P2FLASHBOOT:[^\r\n]*FAIL")),
    ("P2 PSRAM failure", re.compile(r"P2PSRAM:FAIL(?:[:=]|$)")),
    ("LED driver error", re.compile(r"led_daemon: ERROR", re.IGNORECASE)),
    ("PWM device error", re.compile(r"pwm_main:[^\r\n]*failed", re.IGNORECASE)),
    ("panic", re.compile(r"\bPANIC\b", re.IGNORECASE)),
    ("assertion", re.compile(r"\bASSERT(?:ION)?\b", re.IGNORECASE)),
    ("stack overflow", re.compile(r"STACK\s+OVERFLOW", re.IGNORECASE)),
    ("unexpected IRQ", re.compile(r"UNEXPECTED\s+IRQ", re.IGNORECASE)),
    ("register dump", re.compile(r"REGISTER\s+DUMP", re.IGNORECASE)),
    (
        "required command not found",
        re.compile(
            r"nsh:\s+(?:uname|p2help|leds|kill|echo|sleep|pwm|p2smartpins|"
            r"p2i2c|p2storage|p2psram):\s+command not found",
            re.IGNORECASE,
        ),
    ),
    (
        "loadp2 could not find board",
        re.compile(r"Could not find a P2", re.IGNORECASE),
    ),
    (
        "serial device disconnected",
        re.compile(r"device\s+(?:was\s+)?disconnected", re.IGNORECASE),
    ),
)

SHOWCASE_REQUIRED_CONFIG = (
    ("CONFIG_ARCH", '"p2"'),
    ("CONFIG_ARCH_BOARD", '"p2-ec32mb"'),
    ("CONFIG_ARCH_BOARD_P2_EC32MB", "y"),
    ("CONFIG_BUILD_FLAT", "y"),
    ("CONFIG_INIT_ENTRYPOINT", '"nsh_main"'),
    ("CONFIG_NSH_READLINE", "y"),
    ("CONFIG_READLINE_TABCOMPLETION", "y"),
    ("CONFIG_READLINE_CMD_HISTORY", "y"),
    ("CONFIG_TTY_SIGINT", "y"),
    ("CONFIG_TTY_SIGINT_CHAR", "0x03"),
    ("CONFIG_USERLED_LOWER", "y"),
    ("CONFIG_EXAMPLES_LEDS", "y"),
    ("CONFIG_EXAMPLES_LEDS_LEDSET", "0x03"),
    ("CONFIG_P2_EC32MB_GPIO", "y"),
    ("CONFIG_P2_EC32MB_UART1", "y"),
    ("CONFIG_P2_EC32MB_PWM", "y"),
    ("CONFIG_P2_EC32MB_CAPTURE", "y"),
    ("CONFIG_P2_EC32MB_DAC", "y"),
    ("CONFIG_P2_EC32MB_ADC", "y"),
    ("CONFIG_P2_EC32MB_SPI", "y"),
    ("CONFIG_P2_EC32MB_I2C", "y"),
    ("CONFIG_P2_EC32MB_I2C_SDA_PIN", "24"),
    ("CONFIG_P2_EC32MB_I2C_SCL_PIN", "25"),
    ("CONFIG_P2_EC32MB_BMP180", "y"),
    ("CONFIG_P2_EC32MB_PSRAM", "y"),
    ("CONFIG_SYSTEM_P2HELP", "y"),
    ("CONFIG_TESTING_P2SMARTPINS", "y"),
    ("CONFIG_TESTING_P2SMARTPINS_EDGE", "y"),
    ("CONFIG_TESTING_P2SMARTPINS_UART", "y"),
    ("CONFIG_TESTING_P2SMARTPINS_PWM_CAPTURE", "y"),
    ("CONFIG_TESTING_P2SMARTPINS_DAC_ADC", "y"),
    ("CONFIG_TESTING_P2SMARTPINS_SPI", "y"),
    ("CONFIG_TESTING_P2I2C", "y"),
    ("CONFIG_TESTING_P2STORAGE", "y"),
    ("CONFIG_TESTING_P2STORAGE_DESTRUCTIVE", "n"),
    ("CONFIG_TESTING_P2PSRAM", "y"),
    ("CONFIG_FSUTILS_MKFATFS", "n"),
    ("CONFIG_FSUTILS_MKSMARTFS", "n"),
    ("CONFIG_SYSTEM_DD", "n"),
)


class ShowcaseError(RuntimeError):
    """A safety, protocol, or live HIL requirement failed."""


@dataclass(frozen=True)
class ShowcaseConfig:
    build: build_artifact.BuildArtifact
    source_loadp2: pathlib.Path
    loadp2_sha256: str
    port: str
    artifact_dir: pathlib.Path
    board_lock: pathlib.Path
    loader_baud: int
    console_baud: int
    reset_flag: str
    stage_timeout: float
    boot_timeout: float
    interrupt_timeout: float
    psram_timeout: float
    lock_timeout: float
    include_psram: bool
    psram_sequence: str
    config_sha256: str


@dataclass(frozen=True)
class Capture:
    start: int
    end: int
    matches: Mapping[str, str]


def _line_literal(value: str) -> re.Pattern:
    return re.compile(r"^" + re.escape(value) + r"\r?$", re.MULTILINE)


def _async_line_pattern(body: str) -> re.Pattern:
    """Match daemon output even when NSH rendered its prompt first."""

    return re.compile(
        r"^(?:" + hil.NSH_PROMPT_PATTERN + r")?" + body + r"\r?$",
        re.MULTILINE,
    )


def _capture_segment(text: str, start: int, end: int) -> str:
    """Keep the newline required by strict line-oriented response parsers."""

    if end < len(text) and text[end] == "\n":
        end += 1
    return text[start:end]


def _is_relative_to(path: pathlib.Path, parent: pathlib.Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_showcase_config(path: pathlib.Path) -> str:
    values = hil.read_kconfig(path)
    expected_path = (
        REPO_ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb/configs/showcase/defconfig"
    )
    expected = hil.read_kconfig(expected_path)
    mismatches = []
    for name, wanted in expected.items():
        actual = values.get(name, "n")
        if actual != wanted:
            mismatches.append("{}={} (expected {})".format(name, actual, wanted))
    for name, wanted in SHOWCASE_REQUIRED_CONFIG:
        actual = values.get(name, "n")
        if actual != wanted:
            mismatches.append("{}={} (required {})".format(name, actual, wanted))
    if values.get("CONFIG_SMP", "n") != "n":
        mismatches.append("CONFIG_SMP must be disabled for the flat UP showcase")
    if mismatches:
        raise ShowcaseError(
            "build artifact is not the exact EC32MB showcase profile: {}".format(
                ", ".join(dict.fromkeys(mismatches))
            )
        )
    return hil.sha256_file(path)


def build_load_command(
    config: ShowcaseConfig, loader: pathlib.Path, image: pathlib.Path
) -> Tuple[str, ...]:
    command = (
        str(loader),
        "-p",
        config.port,
        "-l",
        str(config.loader_baud),
        "-b",
        str(config.console_baud),
        "-FIFO",
        str(hil.LOADP2_FIFO_BYTES),
        "-ZERO",
        "-v",
        config.reset_flag,
        "-t",
        str(image),
    )
    if {"-FLASH", "-PATCH"}.intersection(command):
        raise ShowcaseError("a forbidden non-RAM loadp2 option was requested")
    if command.count("-DTR") + command.count("-RTS") != 1:
        raise ShowcaseError("loadp2 command must contain exactly one reset flag")
    if command[-2] != "-t" or pathlib.Path(command[-1]) != image:
        raise ShowcaseError("loadp2 must terminal-load the sealed exact ELF")
    return command


def smartpin_patterns(stage: str) -> Tuple[Tuple[str, re.Pattern], ...]:
    if stage not in ("GPIO", "EDGE", "UART", "DAC_ADC", "SPI"):
        raise ShowcaseError("unsupported showcase Smart Pin stage: {}".format(stage))
    patterns: List[Tuple[str, re.Pattern]] = [
        ("P2SMART:BEGIN", _line_literal("P2SMART:BEGIN")),
        (
            smartpins_protocol.WIRING_MARKER,
            _line_literal(smartpins_protocol.WIRING_MARKER),
        ),
        (
            "P2SMART:CAPS",
            _line_literal("P2SMART:CAPS=" + ",".join(EXPECTED_SMARTPIN_CAPS)),
        ),
    ]
    fixed = smartpins_protocol.STAGE_FIXED_MARKERS[stage]
    patterns.append((fixed[0], _line_literal(fixed[0])))
    if stage == "GPIO":
        for index, value in enumerate(smartpins_protocol.GPIO_PATTERN):
            marker = "P2SMART:GPIO:SAMPLE={}:TX={}:RX={}".format(index, value, value)
            patterns.append((marker, _line_literal(marker)))
    elif stage == "DAC_ADC":
        for index in range(3):
            patterns.append(
                (
                    "P2SMART:DAC_ADC:SAMPLE={}".format(index),
                    re.compile(
                        r"^P2SMART:DAC_ADC:SAMPLE={}:"
                        r"DAC=-?\d+:ADC=-?\d+\r?$".format(index),
                        re.MULTILINE,
                    ),
                )
            )
    for marker in fixed[1:]:
        patterns.append((marker, _line_literal(marker)))
    patterns.append(("P2SMART:PASS", _line_literal("P2SMART:PASS")))
    return tuple(patterns)


def parse_smartpin_command(text: str, stage: str) -> Dict[str, object]:
    """Strictly validate one selected-stage invocation from the showcase app."""

    lines = [line.strip() for line in text.replace("\r", "\n").split("\n")]
    errors: List[str] = []
    failures = []
    for line in lines:
        for label, pattern in smartpins_protocol.FAILURE_PATTERNS:
            if pattern.search(line):
                failures.append({"kind": label, "line": line})
                break

    required = [
        "P2SMART:BEGIN",
        smartpins_protocol.WIRING_MARKER,
        "P2SMART:CAPS=" + ",".join(EXPECTED_SMARTPIN_CAPS),
    ]
    required.extend(smartpins_protocol.STAGE_FIXED_MARKERS[stage])
    required.append("P2SMART:PASS")
    positions = []
    duplicates = []
    for marker in required:
        found = [index for index, line in enumerate(lines) if line == marker]
        if not found:
            errors.append("missing {}".format(marker))
        elif len(found) != 1:
            duplicates.append(marker)
        else:
            positions.append(found[0])

    for other_stage in smartpins_protocol.STAGE_ORDER:
        if other_stage == stage:
            continue
        prefix = "P2SMART:{}:".format(other_stage)
        unexpected = [line for line in lines if line.startswith(prefix)]
        if unexpected:
            errors.append(
                "{} command emitted unexpected {} stage markers".format(
                    stage, other_stage
                )
            )

    samples = []
    pattern = smartpins_protocol.SAMPLE_PATTERNS.get(stage)
    if pattern is not None:
        samples = [
            (index, match)
            for index, line in enumerate(lines)
            for match in [pattern.fullmatch(line)]
            if match is not None
        ]
        validator = smartpins_protocol.SAMPLE_VALIDATORS[stage]
        errors.extend(validator(samples))
        begin_marker = smartpins_protocol.STAGE_FIXED_MARKERS[stage][0]
        safe_marker = smartpins_protocol.STAGE_FIXED_MARKERS[stage][1]
        try:
            begin = lines.index(begin_marker)
            safe = lines.index(safe_marker)
            if not all(begin < index < safe for index, match in samples):
                errors.append("{} samples are outside the stage markers".format(stage))
        except ValueError:
            pass

    order_valid = positions == sorted(positions)
    if not order_valid:
        errors.append("Smart Pin command markers are out of order")
    return {
        "complete": not errors and not failures and not duplicates,
        "stage": stage,
        "capabilities": list(EXPECTED_SMARTPIN_CAPS),
        "sample_count": len(samples),
        "errors": errors,
        "failures": failures,
        "duplicates": duplicates,
        "order_valid": order_valid,
    }


class Console:
    """Stream, preserve, and strictly inspect one loadp2 terminal session."""

    def __init__(
        self,
        session,
        raw_output,
        normalized_output,
        transcript_output,
        monotonic: Callable[[], float],
        utc_now: Callable[[], datetime.datetime],
        started_monotonic: float,
    ) -> None:
        self.session = session
        self.raw_output = raw_output
        self.normalizer = hil.NormalizedLog(normalized_output, utc_now)
        self.transcript_output = transcript_output
        self.monotonic = monotonic
        self.utc_now = utc_now
        self.started_monotonic = started_monotonic
        self.text = ""
        self.raw_bytes = 0
        self._failure_checked = 0
        self.inputs: List[Dict[str, object]] = []

    def _timestamp(self) -> str:
        return hil.utc_timestamp(self.utc_now())

    def send(self, stage: str, label: str, data: bytes) -> None:
        entry = {
            "utc": self._timestamp(),
            "elapsed_seconds": round(self.monotonic() - self.started_monotonic, 6),
            "stage": stage,
            "label": label,
            "hex": data.hex(),
            "ascii": "".join(
                chr(value) if 32 <= value <= 126 else "<{:02X}>".format(value)
                for value in data
            ),
        }
        self.session.write(data)
        self.inputs.append(entry)
        self.transcript_output.write(json.dumps(entry, sort_keys=True) + "\n")
        self.transcript_output.flush()

    def _check_failures(self) -> None:
        search_start = max(0, self._failure_checked - 256)
        for label, pattern in FAILURE_PATTERNS:
            for match in pattern.finditer(self.text, search_start):
                if match.end() > self._failure_checked:
                    raise ShowcaseError(
                        "{} observed: {}".format(label, match.group(0).strip())
                    )
        self._failure_checked = len(self.text)

    def read(self, timeout: float) -> None:
        data = self.session.read(timeout)
        if data is None:
            returncode = self.session.poll()
            raise ShowcaseError(
                "loadp2 exited before the showcase proof completed (exit {})".format(
                    returncode
                )
            )
        if not data:
            return
        self.raw_output.write(data)
        self.raw_output.flush()
        self.raw_bytes += len(data)
        self.text += self.normalizer.feed(data)
        self._check_failures()

    def wait_sequence(
        self,
        stage: str,
        patterns: Iterable[Tuple[str, re.Pattern]],
        timeout: float,
        start: Optional[int] = None,
    ) -> Capture:
        begin = len(self.text) if start is None else start
        cursor = begin
        deadline = self.monotonic() + timeout
        captures: Dict[str, str] = {}
        for label, pattern in patterns:
            while True:
                match = pattern.search(self.text, cursor)
                if match is not None:
                    captures[label] = match.group(0).strip()
                    for name, value in match.groupdict().items():
                        if value is not None:
                            captures[name] = value
                    cursor = match.end()
                    break
                remaining = deadline - self.monotonic()
                if remaining <= 0:
                    raise ShowcaseError(
                        "{} timed out waiting for {}".format(stage, label)
                    )
                self.read(min(0.25, remaining))
        return Capture(begin, cursor, captures)

    def sync_prompt(self, stage: str, timeout: float) -> Capture:
        self.send(stage, "empty command for fresh prompt", b"\r")
        return self.wait_sequence(
            stage, (("fresh nsh> prompt", PROMPT_PATTERN),), timeout
        )

    def finish(self) -> None:
        self.text += self.normalizer.finish()


class ShowcaseRunner:
    def __init__(
        self,
        config: ShowcaseConfig,
        process_factory: Callable[
            [Sequence[str]], object
        ] = hil.default_process_factory,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(
            datetime.timezone.utc
        ),
        sleep: Callable[[float], None] = time.sleep,
        lock_factory=monitor.BoardLock,
        owner_probe: Callable[[str], Tuple[int, ...]] = hil.default_owner_probe,
    ) -> None:
        self.config = config
        self.process_factory = process_factory
        self.monotonic = monotonic
        self.utc_now = utc_now
        self.sleep = sleep
        self.lock_factory = lock_factory
        self.owner_probe = owner_probe
        self.started_monotonic = monotonic()
        self.status: Dict[str, object] = {
            "format": FORMAT,
            "status": "RUNNING",
            "exit_code": None,
            "reason": "HIL session has not completed",
            "board": BOARD,
            "profile": PROFILE,
            "smp_enabled": False,
            "port": config.port,
            "started_utc": hil.utc_timestamp(utc_now()),
            "ended_utc": None,
            "stages": [],
            "omissions": [
                {
                    "stage": "p2smartpins pwm",
                    "reason": (
                        "SKIPPED waveform qualification only: p2smartpins pwm "
                        "requires a direct digital P4/P5 link, while the installed "
                        "fixture is 1k-series/100nF RC. A safe /dev/pwm0 open/start/"
                        "stop smoke is still required."
                    ),
                }
            ],
            "storage_actions": ["probe"],
            "destructive_storage_actions": [],
            "single_serial_owner": True,
            "serial_processes_started": 0,
            "intentionally_terminated": False,
            "gates": {
                "P2_HIL": True,
                "P2_ALLOW_RESET": True,
                "P2_ALLOW_LOOPBACK_TESTS": True,
                "P2_ALLOW_PSRAM_WRITE": config.include_psram,
            },
            "build": {
                **config.build.as_dict(),
                "config_sha256": config.config_sha256,
                "elf_sha256": config.build.elf_sha256,
                "raw_binary_sha256": config.build.binary_sha256,
            },
            "loadp2_source": str(config.source_loadp2),
            "loadp2_sha256": config.loadp2_sha256,
        }
        if not config.include_psram:
            self.status["omissions"].append(
                {
                    "stage": "p2psram",
                    "reason": (
                        "SKIPPED by default because the optional full test writes "
                        "all volatile external PSRAM; use --include-psram with "
                        "P2_ALLOW_PSRAM_WRITE=1"
                    ),
                }
            )

    @property
    def status_path(self) -> pathlib.Path:
        return self.config.artifact_dir / "status.json"

    def _write_status(self) -> None:
        hil.write_json(self.status_path, self.status)

    def _stage(self, name: str, action: Callable[[], Dict[str, object]]) -> None:
        record: Dict[str, object] = {
            "name": name,
            "status": "RUNNING",
            "started_utc": hil.utc_timestamp(self.utc_now()),
        }
        self.status["stages"].append(record)
        self._write_status()
        stage_start = self.monotonic()
        try:
            details = action()
        except Exception as exc:
            record.update(
                {
                    "status": "FAIL",
                    "ended_utc": hil.utc_timestamp(self.utc_now()),
                    "duration_seconds": round(self.monotonic() - stage_start, 6),
                    "reason": str(exc),
                }
            )
            self._write_status()
            raise
        record.update(details)
        record.update(
            {
                "status": "PASS",
                "ended_utc": hil.utc_timestamp(self.utc_now()),
                "duration_seconds": round(self.monotonic() - stage_start, 6),
            }
        )
        self._write_status()

    def _prepare_artifact(self) -> Tuple[pathlib.Path, pathlib.Path]:
        directory = self.config.artifact_dir
        directory.mkdir(parents=True)
        self._write_status()
        inputs = directory / "inputs"
        inputs.mkdir()
        build_copy = inputs / "build"
        shutil.copytree(self.config.build.path, build_copy, copy_function=shutil.copy2)
        copied = build_artifact.load(build_copy, require_clean=True)
        if (
            copied.board != BOARD
            or copied.profile != PROFILE
            or copied.elf_sha256 != self.config.build.elf_sha256
            or copied.binary_sha256 != self.config.build.binary_sha256
            or copied.status_sha256 != self.config.build.status_sha256
        ):
            raise ShowcaseError("sealed build artifact changed while it was copied")

        loader_copy = inputs / "loadp2"
        shutil.copy2(self.config.source_loadp2, loader_copy)
        loader_copy.chmod(loader_copy.stat().st_mode | 0o111)
        if hil.sha256_file(loader_copy) != self.config.loadp2_sha256:
            raise ShowcaseError("sealed loadp2 changed while it was copied")
        for source in (
            pathlib.Path(__file__),
            P2_TOOLS / "smartpins_protocol.py",
            P2_TOOLS / "i2c_protocol.py",
            P2_TOOLS / "storage_protocol.py",
            P2_TOOLS / "psram_protocol.py",
        ):
            shutil.copy2(source, inputs / source.name)
        self.status.update(
            {
                "preserved_build_artifact": "inputs/build",
                "preserved_loadp2": "inputs/loadp2",
                "preserved_inputs": sorted(
                    path.relative_to(directory).as_posix()
                    for path in inputs.rglob("*")
                    if path.is_file()
                ),
            }
        )
        self._write_status()
        return loader_copy.resolve(), (build_copy / "nuttx").resolve()

    def _terminate_session(self, session) -> None:
        """Stop the sole loader while the caller still holds the board lock."""

        if session.poll() is None:
            session.terminate()
            self.status["intentionally_terminated"] = True
            try:
                session.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                session.kill()
                session.wait(timeout=0.5)
        self.status["loadp2_exit_code"] = session.poll()

    def _command_stage(
        self,
        console: Console,
        name: str,
        command: bytes,
        patterns: Iterable[Tuple[str, re.Pattern]],
        parser: Optional[Callable[[str], Dict[str, object]]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, object]:
        start = len(console.text)
        console.send(name, "command", command)
        capture = console.wait_sequence(
            name,
            patterns,
            self.config.stage_timeout if timeout is None else timeout,
            start=start,
        )
        segment = _capture_segment(console.text, start, capture.end)
        protocol = parser(segment) if parser is not None else {"complete": True}
        if protocol.get("complete") is not True:
            raise ShowcaseError("{} strict parser failed: {}".format(name, protocol))
        console.sync_prompt(name, self.config.stage_timeout)
        return {
            "command_hex": command.hex(),
            "markers": dict(capture.matches),
            "protocol": protocol,
        }

    def _run_session(self, console: Console) -> None:
        self._stage(
            "ordered boot and showcase readiness",
            lambda: {
                "markers": dict(
                    console.wait_sequence(
                        "boot", BOOT_PATTERNS, self.config.boot_timeout, start=0
                    ).matches
                )
            },
        )

        self._stage(
            "p2help",
            lambda: self._command_stage(
                console,
                "p2help",
                b"p2help\r",
                (
                    (
                        "P2SHOWCASE board/profile",
                        _line_literal("P2SHOWCASE:BOARD=p2-ec32mb:PROFILE=showcase"),
                    ),
                    (
                        "/dev/userleds help",
                        re.compile(
                            r"^\s*/dev/userleds\s+two active-high "
                            r"buffered LEDs \(LED switch ON\)\r?$",
                            re.MULTILINE,
                        ),
                    ),
                    ("P2SHOWCASE:PASS", _line_literal("P2SHOWCASE:PASS")),
                ),
            ),
        )

        def userleds() -> Dict[str, object]:
            start = len(console.text)
            console.send("user LEDs", "start leds daemon", b"leds\r")
            capture = console.wait_sequence(
                "user LEDs",
                (
                    (
                        "leds main start",
                        _async_line_pattern(
                            re.escape("leds_main: Starting the led_daemon")
                        ),
                    ),
                    (
                        "led daemon pid",
                        _async_line_pattern(
                            r"led_daemon \(pid# "
                            r"(?P<led_pid>[1-9][0-9]*)\): Running"
                        ),
                    ),
                    (
                        "open /dev/userleds",
                        _async_line_pattern(
                            re.escape("led_daemon: Opening /dev/userleds")
                        ),
                    ),
                    (
                        "two LEDs supported",
                        _async_line_pattern(
                            re.escape("led_daemon: Supported LEDs 0x03")
                        ),
                    ),
                    (
                        "LED write through driver",
                        _async_line_pattern(r"led_daemon: LED set 0x0[1-3]"),
                    ),
                ),
                self.config.stage_timeout,
                start=start,
            )
            pid = capture.matches["led_pid"]
            console.sync_prompt("user LEDs", self.config.stage_timeout)
            console.send(
                "user LEDs",
                "stop leds daemon",
                "kill -15 {}\r".format(pid).encode("ascii"),
            )
            stopped = console.wait_sequence(
                "user LEDs",
                (
                    (
                        "SIGTERM received",
                        _async_line_pattern(re.escape("SIGTERM received")),
                    ),
                    (
                        "daemon terminated",
                        _async_line_pattern(re.escape("led_daemon: Terminated.")),
                    ),
                ),
                self.config.stage_timeout,
            )
            console.sync_prompt("user LEDs", self.config.stage_timeout)
            return {
                "device": "/dev/userleds",
                "supported_led_mask": "0x03",
                "daemon_pid": int(pid),
                "markers": {**capture.matches, **stopped.matches},
            }

        self._stage("/dev/userleds and leds driver path", userleds)

        self._stage(
            "shell Tab completion",
            lambda: self._command_stage(
                console,
                "shell Tab completion",
                b"unam\t -a\r",
                (
                    (
                        "uname -a result",
                        re.compile(r"^NuttX[^\r\n]+\r?$", re.MULTILINE),
                    ),
                ),
            ),
        )

        def history() -> Dict[str, object]:
            start = len(console.text)
            marker = "P2SHOWCASE:HISTORY=PASS"
            console.send(
                "shell Up-arrow history",
                "seed history",
                ("echo " + marker + "\r").encode("ascii"),
            )
            console.wait_sequence(
                "shell Up-arrow history",
                (("first history output", _line_literal(marker)),),
                self.config.stage_timeout,
                start=start,
            )
            console.sync_prompt("shell Up-arrow history", self.config.stage_timeout)
            second_start = len(console.text)
            console.send("shell Up-arrow history", "Up-arrow then Enter", b"\x1b[A\r")
            console.wait_sequence(
                "shell Up-arrow history",
                (("recalled history output", _line_literal(marker)),),
                self.config.stage_timeout,
                start=second_start,
            )
            console.sync_prompt("shell Up-arrow history", self.config.stage_timeout)
            segment = console.text[start:]
            count = len(
                re.findall(r"(?:^|[\r\n])" + re.escape(marker) + r"\r?\n", segment)
            )
            if count != 2:
                raise ShowcaseError(
                    "history proof expected two command outputs, found {}".format(count)
                )
            return {"marker": marker, "output_count": count}

        self._stage("shell Up-arrow history", history)

        def ctrl_c() -> Dict[str, object]:
            start = len(console.text)
            console.send("Ctrl-C interrupt", "start 30-second sleep", b"sleep 30\r")
            console.wait_sequence(
                "Ctrl-C interrupt",
                (("sleep command echo", re.compile(r"sleep 30\r?\n")),),
                self.config.stage_timeout,
                start=start,
            )
            self.sleep(0.25)
            prompt_start = len(console.text)
            interrupt_start = self.monotonic()
            console.send("Ctrl-C interrupt", "Ctrl-C", b"\x03")
            console.wait_sequence(
                "Ctrl-C interrupt",
                (("prompt after Ctrl-C", PROMPT_PATTERN),),
                self.config.interrupt_timeout,
                start=prompt_start,
            )
            elapsed = self.monotonic() - interrupt_start
            if elapsed >= self.config.interrupt_timeout or elapsed >= 10.0:
                raise ShowcaseError(
                    "Ctrl-C did not return to the prompt quickly ({:.3f}s)".format(
                        elapsed
                    )
                )
            return {
                "command": "sleep 30",
                "interrupt_hex": "03",
                "prompt_return_seconds": round(elapsed, 6),
                "deadline_seconds": self.config.interrupt_timeout,
            }

        self._stage("Ctrl-C interrupt and prompt return", ctrl_c)

        for command_name, stage in (
            ("gpio", "GPIO"),
            ("edge", "EDGE"),
            ("uart", "UART"),
            ("analog", "DAC_ADC"),
        ):
            display_name = "p2smartpins {}".format(command_name)
            self._stage(
                display_name,
                lambda display_name=display_name,
                command_name=command_name,
                stage=stage: (
                    self._command_stage(
                        console,
                        display_name,
                        (display_name + "\r").encode("ascii"),
                        smartpin_patterns(stage),
                        parser=lambda text, stage=stage: parse_smartpin_command(
                            text, stage
                        ),
                    )
                ),
            )

        def external_pwm_ctrl_c() -> Dict[str, object]:
            name = "external PWM Ctrl-C and prompt return"
            command = b"pwm -f 1000 -d 50 -t 30\r"
            start = len(console.text)
            console.send(name, "start 30-second foreground PWM", command)
            console.wait_sequence(
                name,
                (
                    (
                        "foreground PWM started",
                        re.compile(
                            r"^pwm_main: starting output with frequency: 1000 "
                            r"channel: -?\d+ duty: [0-9a-fA-F]{8}\r?$",
                            re.MULTILINE,
                        ),
                    ),
                ),
                self.config.stage_timeout,
                start=start,
            )
            self.sleep(0.25)
            prompt_start = len(console.text)
            interrupt_start = self.monotonic()
            console.send(name, "Ctrl-C", b"\x03")
            console.wait_sequence(
                name,
                (("prompt after external-app Ctrl-C", PROMPT_PATTERN),),
                self.config.interrupt_timeout,
                start=prompt_start,
            )
            elapsed = self.monotonic() - interrupt_start
            if elapsed >= self.config.interrupt_timeout or elapsed >= 10.0:
                raise ShowcaseError(
                    "external PWM Ctrl-C did not return quickly ({:.3f}s)".format(
                        elapsed
                    )
                )
            return {
                "command": command.decode("ascii").rstrip("\r"),
                "interrupt_hex": "03",
                "prompt_return_seconds": round(elapsed, 6),
                "deadline_seconds": self.config.interrupt_timeout,
                "proof": (
                    "foreground external application received default SIGINT "
                    "after the shell sleep proof"
                ),
            }

        self._stage("external PWM Ctrl-C and prompt return", external_pwm_ctrl_c)

        self._stage(
            "/dev/pwm0 RC-safe open/start/stop smoke",
            lambda: self._command_stage(
                console,
                "/dev/pwm0 RC-safe open/start/stop smoke",
                b"pwm -f 1000 -d 50 -t 1\r",
                (
                    (
                        "PWM 1-kHz 50-percent start",
                        re.compile(
                            r"^pwm_main: starting output with "
                            r"frequency: 1000 channel: -?\d+ duty: [0-9a-fA-F]{8}\r?$",
                            re.MULTILINE,
                        ),
                    ),
                    (
                        "PWM clean stop",
                        _line_literal("pwm_main: stopping output"),
                    ),
                ),
            ),
        )

        self._stage(
            "p2smartpins spi",
            lambda: self._command_stage(
                console,
                "p2smartpins spi",
                b"p2smartpins spi\r",
                smartpin_patterns("SPI"),
                parser=lambda text: parse_smartpin_command(text, "SPI"),
            ),
        )

        def i2c() -> Dict[str, object]:
            details = self._command_stage(
                console,
                "p2i2c BMP180",
                b"p2i2c\r",
                (
                    (
                        i2c_protocol.START_MARKER,
                        _line_literal(i2c_protocol.START_MARKER),
                    ),
                    (i2c_protocol.ID_MARKER, _line_literal(i2c_protocol.ID_MARKER)),
                    (
                        "P2I2C:READINGS=32",
                        re.compile(
                            r"^P2I2C:READINGS=32:MIN=\d+:MAX=\d+:"
                            r"FNV1A=[0-9A-F]{8}\r?$",
                            re.MULTILINE,
                        ),
                    ),
                    (i2c_protocol.PASS_MARKER, _line_literal(i2c_protocol.PASS_MARKER)),
                ),
            )
            protocol = i2c_protocol.parse_i2c(console.text)
            if protocol.get("complete") is not True:
                raise ShowcaseError("p2i2c strict parser failed: {}".format(protocol))
            details["protocol"] = protocol
            return details

        self._stage("p2i2c BMP180 on P24/P25", i2c)

        self._stage(
            "p2storage probe (read-only)",
            lambda: self._command_stage(
                console,
                "p2storage probe (read-only)",
                storage_protocol.command_bytes("probe"),
                storage_protocol.response_marker_patterns("probe"),
                parser=lambda text: storage_protocol.parse_storage_response(
                    text, "probe"
                ),
            ),
        )

        if self.config.include_psram:
            self._stage(
                "optional p2psram volatile write/read proof",
                lambda: self._command_stage(
                    console,
                    "optional p2psram volatile write/read proof",
                    psram_protocol.command_bytes(self.config.psram_sequence),
                    psram_protocol.marker_patterns(self.config.psram_sequence),
                    parser=lambda text: psram_protocol.parse_psram(
                        text, self.config.psram_sequence
                    ),
                    timeout=self.config.psram_timeout,
                ),
            )

        for marker in (
            "P2BOOT:ENTRY",
            "P2BOOT:DATA=OK",
            "P2BOOT:BSS=OK",
            "P2BOOT:NX_START",
            "P2SHOWCASE:READY:BOARD=p2-ec32mb:RUN=p2help",
        ):
            count = console.text.count(marker)
            if count != 1:
                raise ShowcaseError(
                    "final transcript expected one {}, found {}".format(marker, count)
                )
        if console.text.count("P2SMART:BEGIN") != 5:
            raise ShowcaseError("final transcript does not contain five Smart Pin runs")

    def run(self) -> bool:
        directory = self.config.artifact_dir
        session = None
        console = None
        raw_path = directory / "console.raw"
        normalized_path = directory / "console.normalized.log"
        transcript_path = directory / "commands.jsonl"
        try:
            loader, image = self._prepare_artifact()
            command = build_load_command(self.config, loader, image)
            self.status["load_command"] = list(command)
            self.status["loaded_elf"] = "inputs/build/nuttx"
            self._write_status()
            with self.lock_factory(
                self.config.board_lock,
                timeout=self.config.lock_timeout,
                monotonic=self.monotonic,
            ):
                source_build = build_artifact.load(
                    self.config.build.path, require_clean=True
                )
                if (
                    source_build.elf_sha256 != self.config.build.elf_sha256
                    or source_build.binary_sha256 != self.config.build.binary_sha256
                    or hil.sha256_file(self.config.source_loadp2)
                    != self.config.loadp2_sha256
                ):
                    raise ShowcaseError("source image or loader changed before reset")
                owners = self.owner_probe(self.config.port)
                self.status["serial_owners_before_load"] = list(owners)
                if owners:
                    raise ShowcaseError(
                        "serial port already has owner PID(s): {}".format(
                            ", ".join(str(pid) for pid in owners)
                        )
                    )
                with raw_path.open("wb") as raw_output, normalized_path.open(
                    "w", encoding="utf-8"
                ) as normalized_output, transcript_path.open(
                    "w", encoding="utf-8"
                ) as transcript_output:
                    session = self.process_factory(command)
                    try:
                        self.status["serial_processes_started"] = 1
                        self._write_status()
                        console = Console(
                            session,
                            raw_output,
                            normalized_output,
                            transcript_output,
                            self.monotonic,
                            self.utc_now,
                            self.started_monotonic,
                        )
                        try:
                            self._run_session(console)
                        finally:
                            console.finish()
                    finally:
                        self._terminate_session(session)
            self.status.update(
                {
                    "status": "PASS",
                    "exit_code": EXIT_OK,
                    "reason": "all required EC32MB showcase HIL stages passed",
                }
            )
            return True
        except Exception as exc:
            self.status.update(
                {"status": "FAIL", "exit_code": EXIT_HIL_FAILURE, "reason": str(exc)}
            )
            return False
        finally:
            if session is not None:
                self._terminate_session(session)
                session.close()
            if console is not None:
                self.status["raw_serial_bytes"] = console.raw_bytes
                self.status["input_events"] = console.inputs
            for key, path in (
                ("raw_serial_sha256", raw_path),
                ("normalized_serial_sha256", normalized_path),
                ("command_transcript_sha256", transcript_path),
            ):
                if path.is_file():
                    self.status[key] = hil.sha256_file(path)
            self.status["ended_utc"] = hil.utc_timestamp(self.utc_now())
            self.status["duration_seconds"] = round(
                self.monotonic() - self.started_monotonic, 6
            )
            if directory.is_dir():
                self._write_status()


def parse_config(
    argv: Optional[Sequence[str]],
    env: Mapping[str, str],
    utc_now: Callable[[], datetime.datetime],
    port_validator: Callable[[str], bool],
) -> ShowcaseConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--build-artifact", type=pathlib.Path, required=True)
    parser.add_argument("--port")
    parser.add_argument("--loadp2", type=pathlib.Path)
    parser.add_argument("--artifact-dir", type=pathlib.Path)
    parser.add_argument("--loader-baud", type=int)
    parser.add_argument("--console-baud", type=int)
    parser.add_argument("--reset-method", choices=("loadp2", "dtr", "rts"))
    parser.add_argument("--stage-timeout", type=float, default=60.0)
    parser.add_argument("--boot-timeout", type=float, default=90.0)
    parser.add_argument("--interrupt-timeout", type=float, default=5.0)
    parser.add_argument("--psram-timeout", type=float, default=1800.0)
    parser.add_argument("--lock-timeout", type=float, default=0.0)
    parser.add_argument("--include-psram", action="store_true")
    parser.add_argument("--psram-sequence", default="A55A0713")
    args = parser.parse_args(argv)

    if not args.execute:
        raise ShowcaseError("refusing dry run: pass --execute to enable reset and HIL")
    for name in ("P2_HIL", "P2_ALLOW_RESET", "P2_ALLOW_LOOPBACK_TESTS"):
        if env.get(name, "0") != "1":
            raise ShowcaseError("{}=1 is required".format(name))
    if args.include_psram and env.get("P2_ALLOW_PSRAM_WRITE", "0") != "1":
        raise ShowcaseError("--include-psram requires P2_ALLOW_PSRAM_WRITE=1")
    try:
        psram_sequence = psram_protocol.normalize_sequence(args.psram_sequence)
    except ValueError as exc:
        raise ShowcaseError(str(exc)) from exc

    build = build_artifact.load(args.build_artifact, require_clean=True)
    if build.board != BOARD or build.profile != PROFILE:
        raise ShowcaseError(
            "build artifact must be {}:{}, got {}:{}".format(
                BOARD, PROFILE, build.board, build.profile
            )
        )
    config_sha = validate_showcase_config(build.path / "config")
    elf = build.path / "nuttx"
    raw = build.path / "nuttx.bin"
    if elf.read_bytes()[:4] != b"\x7fELF":
        raise ShowcaseError("build artifact nuttx is not an ELF image")
    if raw.stat().st_size == 0:
        raise ShowcaseError("build artifact nuttx.bin is empty")

    env_port = env.get("P2_PORT", "")
    port = args.port or env_port
    if not port:
        raise ShowcaseError("--port or P2_PORT must name the exact serial device")
    if args.port and env_port and args.port != env_port:
        raise ShowcaseError("--port must exactly match P2_PORT when both are set")
    if not pathlib.Path(port).is_absolute():
        raise ShowcaseError("serial device path must be absolute")
    if not port_validator(port):
        raise ShowcaseError(
            "serial device is absent or not a character device: {}".format(port)
        )

    loadp2_value = args.loadp2 or (
        pathlib.Path(env["LOADP2"]) if env.get("LOADP2") else None
    )
    if loadp2_value is None:
        raise ShowcaseError("--loadp2 or LOADP2 must name the pinned loader")
    try:
        loadp2 = loadp2_value.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ShowcaseError("pinned loadp2 is unavailable: {}".format(exc)) from exc
    if not loadp2.is_file() or not os.access(loadp2, os.X_OK):
        raise ShowcaseError("pinned loadp2 is not executable: {}".format(loadp2))
    loadp2_sha = hil.pinned_sha256(loadp2, build.path / "toolchain.lock")

    reset_method = args.reset_method or env.get("P2_RESET_METHOD", "loadp2").lower()
    if (
        args.reset_method
        and env.get("P2_RESET_METHOD")
        and args.reset_method != env["P2_RESET_METHOD"].lower()
    ):
        raise ShowcaseError("--reset-method must exactly match P2_RESET_METHOD")
    if reset_method in ("loadp2", "dtr"):
        reset_flag = "-DTR"
    elif reset_method == "rts":
        reset_flag = "-RTS"
    else:
        raise ShowcaseError("P2_RESET_METHOD must be loadp2, dtr, or rts")

    loader_baud = args.loader_baud or int(env.get("P2_LOADER_BAUD", "2000000"))
    console_baud = args.console_baud or int(env.get("P2_CONSOLE_BAUD", "230400"))
    if loader_baud <= 0 or console_baud <= 0:
        raise ShowcaseError("loader and console baud must be positive")
    if not 0 < args.stage_timeout <= 600:
        raise ShowcaseError("--stage-timeout must be in (0, 600]")
    if not 0 < args.boot_timeout <= 600:
        raise ShowcaseError("--boot-timeout must be in (0, 600]")
    if not 0 < args.interrupt_timeout <= 10:
        raise ShowcaseError("--interrupt-timeout must be in (0, 10]")
    if not 0 < args.psram_timeout <= 3600:
        raise ShowcaseError("--psram-timeout must be in (0, 3600]")
    if args.lock_timeout < 0:
        raise ShowcaseError("--lock-timeout cannot be negative")

    if args.artifact_dir:
        artifact_dir = args.artifact_dir.expanduser().resolve()
    else:
        artifact_dir = (
            REPO_ROOT / "artifacts/hil" / "{}-showcase".format(hil.run_stamp(utc_now()))
        )
    if artifact_dir.exists():
        raise ShowcaseError(
            "artifact directory already exists: {}".format(artifact_dir)
        )
    if _is_relative_to(artifact_dir, build.path) or _is_relative_to(
        build.path, artifact_dir
    ):
        raise ShowcaseError(
            "artifact directory and build artifact must not contain each other"
        )

    board_lock = (
        pathlib.Path(env.get("P2_LOCK_FILE", str(hil.DEFAULT_LOCK_FILE)))
        .expanduser()
        .resolve()
    )
    return ShowcaseConfig(
        build=build,
        source_loadp2=loadp2,
        loadp2_sha256=loadp2_sha,
        port=port,
        artifact_dir=artifact_dir,
        board_lock=board_lock,
        loader_baud=loader_baud,
        console_baud=console_baud,
        reset_flag=reset_flag,
        stage_timeout=args.stage_timeout,
        boot_timeout=args.boot_timeout,
        interrupt_timeout=args.interrupt_timeout,
        psram_timeout=args.psram_timeout,
        lock_timeout=args.lock_timeout,
        include_psram=args.include_psram,
        psram_sequence=psram_sequence,
        config_sha256=config_sha,
    )


def main(
    argv: Optional[Sequence[str]] = None,
    env: Optional[Mapping[str, str]] = None,
    process_factory: Callable[[Sequence[str]], object] = hil.default_process_factory,
    monotonic: Callable[[], float] = time.monotonic,
    utc_now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(
        datetime.timezone.utc
    ),
    sleep: Callable[[float], None] = time.sleep,
    lock_factory=monitor.BoardLock,
    owner_probe: Callable[[str], Tuple[int, ...]] = hil.default_owner_probe,
    port_validator: Callable[[str], bool] = hil.is_character_device,
) -> int:
    effective_env = dict(os.environ if env is None else env)
    try:
        config = parse_config(argv, effective_env, utc_now, port_validator)
    except (
        ShowcaseError,
        build_artifact.BuildArtifactError,
        hil.SafetyError,
        OSError,
        ValueError,
    ) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return EXIT_CONFIGURATION
    runner = ShowcaseRunner(
        config,
        process_factory=process_factory,
        monotonic=monotonic,
        utc_now=utc_now,
        sleep=sleep,
        lock_factory=lock_factory,
        owner_probe=owner_probe,
    )
    passed = runner.run()
    if passed:
        print("PASS: showcase HIL evidence: {}".format(config.artifact_dir))
        return EXIT_OK
    print(
        "FAIL: {} (evidence: {})".format(runner.status["reason"], config.artifact_dir),
        file=sys.stderr,
    )
    return EXIT_HIL_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
