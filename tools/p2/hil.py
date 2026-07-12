#!/usr/bin/env python3
"""Run a native P2 standalone protocol under one board lock.

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

import monitor

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_IMAGE = (
    REPO_ROOT / "tools" / "p2" / "standalone" / "hello" / "build" / "p2hello.elf"
)
DEFAULT_CONTEXT_IMAGE = (
    REPO_ROOT / "tools" / "p2" / "standalone" / "context" / "build" / "p2context.elf"
)
DEFAULT_LOCK_FILE = pathlib.Path("/tmp/nuttx-p2-hil.lock")
DEFAULT_TOOLCHAIN_LOCK = REPO_ROOT / "tools" / "p2" / "toolchain.lock"
LOADP2_SCRIPT = "pausems(500)send(?)"

EXIT_OK = 0
EXIT_SAFETY = 2
EXIT_HIL_FAILURE = 3
EXIT_LOCK_BUSY = 9
EXIT_INTERRUPTED = 130

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
    loadp2_script: str
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


class MarkerParser:
    """Streaming marker parser which detects split markers and bad output."""

    def __init__(
        self,
        expected: Sequence[MarkerSpec],
        reset_pattern: re.Pattern = HELLO_MARKERS[0].pattern,
        protocol_failure_patterns: Sequence[
            Tuple[str, re.Pattern]
        ] = PROTOCOL_FAILURE_PATTERNS,
    ) -> None:
        self.expected = tuple(expected)
        self.reset_pattern = reset_pattern
        self.protocol_failure_patterns = tuple(protocol_failure_patterns)
        self.found: Dict[str, int] = {}
        self.captures: Dict[str, str] = {}
        self.panic_marker: Optional[str] = None
        self.protocol_failure: Optional[str] = None
        self.disconnect_marker: Optional[str] = None
        self.reset_count = 0
        self.order_valid = True
        all_patterns = [spec.pattern for spec in self.expected]
        all_patterns.extend(pattern for _, pattern in PANIC_PATTERNS)
        all_patterns.extend(pattern for _, pattern in self.protocol_failure_patterns)
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
            if spec.label in self.found:
                continue
            for match in spec.pattern.finditer(combined):
                absolute_end = base + match.end()
                if absolute_end > previous_total:
                    self.found[spec.label] = base + match.start()
                    for name, value in match.groupdict().items():
                        if value is not None:
                            self.captures[name] = value
                    break

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
    protocol: str, extra_literals: Sequence[str]
) -> Tuple[MarkerSpec, ...]:
    if protocol == "hello":
        return exact_hello_markers(extra_literals)

    markers = list(CONTEXT_MARKERS)
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
        config.artifact_dir.mkdir(parents=True, exist_ok=False)
        preserved_inputs = preserve_hil_inputs(config)
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
            "console_baud": config.console_baud,
            "reset_flag": config.reset_flag,
            "timeout_seconds_per_cycle": config.timeout,
            "preserved_inputs": preserved_inputs,
        }
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
                    result = self._run_cycle(cycle)
                    if not result.passed:
                        overall["failure_reason"] = result.reason
                        break
                    passed += 1
        finally:
            overall["cycles_passed"] = passed
            overall["ended_utc"] = utc_timestamp(self.utc_now())
            overall["status"] = "PASS" if passed == config.cycles else "FAIL"
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
        )
        raw_bytes = 0
        returncode = None
        intentionally_terminated = False
        passed = False
        reason = "loadp2 did not start"
        session = None

        command_record = {
            "argv": list(command),
            "shell_escaped": shlex.join(command),
            "loadp2_script": config.loadp2_script,
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
            "console_baud": config.console_baud,
            "reset_flag": config.reset_flag,
            "timeout_seconds": config.timeout,
        }
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
                                reason = "loadp2 terminal disconnected before protocol completed"
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
                            parser.feed(decoded)
                            failure = parser.failure_reason
                            if failure is not None:
                                reason = failure
                                break
                            if parser.complete:
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
                                    reason = "loadp2 terminal disconnected before protocol completed"
                                break
                except (OSError, RuntimeError, ValueError) as exc:
                    reason = "loadp2 process I/O failed: {}".format(
                        monitor.safe_error(exc)
                    )
                finally:
                    trailing = normalizer.finish()
                    if trailing:
                        parser.feed(trailing)
        finally:
            if session is not None:
                returncode = self._stop_session(session, returncode)

        elapsed = max(0.0, self.monotonic() - started)
        marker_status = parser.as_dict()
        write_json(cycle_dir / "markers.json", marker_status)
        status = {
            "status": "PASS" if passed else "FAIL",
            "reason": reason,
            "elapsed_seconds": round(elapsed, 6),
            "raw_bytes": raw_bytes,
            "loader_returncode": returncode,
            "intentionally_terminated": intentionally_terminated,
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
        description="RAM-load and verify a native P2 standalone protocol",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--protocol", choices=("hello", "context"), default="hello")
    parser.add_argument("--port")
    parser.add_argument("--image")
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--lock-timeout", type=float, default=0.0)
    parser.add_argument("--loader-baud", type=int)
    parser.add_argument("--console-baud", type=int)
    parser.add_argument("--reset-method", choices=("loadp2", "dtr", "rts"))
    parser.add_argument("--artifact-dir")
    parser.add_argument("--build-standalone", action="store_true")
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="LITERAL",
        help="additional literal marker required after the fixed protocol",
    )
    return parser


def config_from_args(
    args,
    env: Mapping[str, str],
    utc_now: Callable[[], datetime.datetime],
    port_validator: Callable[[str], bool],
) -> HilConfig:
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

    loader_baud = args.loader_baud or int(env.get("P2_LOADER_BAUD", "2000000"))
    console_baud = args.console_baud or int(env.get("P2_CONSOLE_BAUD", "230400"))
    if loader_baud <= 0 or console_baud <= 0:
        raise SafetyError("loader and console baud must be greater than zero")
    if args.cycles <= 0 or args.cycles > 100:
        raise SafetyError("--cycles must be in the range 1..100")
    if args.timeout <= 0 or args.timeout > 600:
        raise SafetyError("--timeout must be in the range (0, 600]")
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
        expected=exact_protocol_markers(args.protocol, args.expect),
        reset_pattern=(
            CONTEXT_MARKERS[0].pattern
            if args.protocol == "context"
            else HELLO_MARKERS[0].pattern
        ),
        protocol_failure_patterns=(
            CONTEXT_FAILURE_PATTERNS
            if args.protocol == "context"
            else PROTOCOL_FAILURE_PATTERNS
        ),
        loadp2_script="" if args.protocol == "context" else LOADP2_SCRIPT,
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
            "DRY-RUN: no build, serial open, reset, or load was performed; pass --execute",
            file=sys.stderr,
        )
        return EXIT_SAFETY
    if environment.get("P2_HIL", "0") != "1":
        print("HIL REQUIRED: set P2_HIL=1 before --execute", file=sys.stderr)
        return EXIT_SAFETY

    try:
        if args.build_standalone:
            build_rc = build_runner(args.protocol)
            if build_rc != 0:
                raise SafetyError(
                    "standalone {} build failed with exit code {}".format(
                        args.protocol, build_rc
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
