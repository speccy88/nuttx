#!/usr/bin/env python3
"""Capture and classify the Propeller 2 serial console.

The command-line entry point is intentionally HIL-gated.  The reusable
``SerialMonitor`` core accepts an injected serial factory and clocks so host
tests never need to open a physical serial device.
"""

import argparse
import codecs
import contextlib
import datetime
import fcntl
import os
import pathlib
import re
import sys
import time
from dataclasses import dataclass
from typing import BinaryIO, Callable, Iterable, Optional, Sequence, TextIO, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_LOCK_FILE = pathlib.Path("/tmp/nuttx-p2-hil.lock")

EXIT_OK = 0
EXIT_SAFETY = 2
EXIT_EXPECT_TIMEOUT = 3
EXIT_PANIC = 4
EXIT_UNEXPECTED_RESET = 5
EXIT_CONNECT_FAILED = 6
EXIT_DISCONNECTED = 7
EXIT_SERIAL_ERROR = 8
EXIT_LOCK_BUSY = 9
EXIT_INTERRUPTED = 130

DEFAULT_PANIC_MARKERS = (
    "PANIC",
    "ASSERT",
    "STACK OVERFLOW",
    "UNEXPECTED IRQ",
    "REGISTER DUMP",
)

DEFAULT_RESET_MARKERS = (
    "P2BOOT:ENTRY",
    "P2HELLO:ENTRY",
)

LINE_ENDINGS = {
    "none": b"",
    "cr": b"\r",
    "lf": b"\n",
    "crlf": b"\r\n",
}


class ConfigurationError(ValueError):
    """The monitor configuration cannot be executed safely."""


class LockBusyError(RuntimeError):
    """Another process owns the P2 board lock."""


class SerialProtocolError(RuntimeError):
    """A serial implementation violated the small pyserial interface used."""


@dataclass(frozen=True)
class MonitorConfig:
    port: str
    raw_log: pathlib.Path
    normalized_log: pathlib.Path
    baud: int = 230400
    timeout: float = 30.0
    read_timeout: float = 0.1
    write_timeout: float = 1.0
    reconnect_interval: float = 0.25
    read_size: int = 4096
    expected_markers: Tuple[str, ...] = ()
    panic_markers: Tuple[str, ...] = DEFAULT_PANIC_MARKERS
    reset_markers: Tuple[str, ...] = DEFAULT_RESET_MARKERS
    max_resets: int = 1
    sends: Tuple[str, ...] = ()
    send_ending: bytes = b"\r\n"
    send_delay: float = 0.0
    send_interval: float = 0.0

    def validate(self) -> None:
        if not self.port:
            raise ConfigurationError("a serial port is required")
        if self.baud <= 0:
            raise ConfigurationError("baud must be greater than zero")
        if self.timeout < 0:
            raise ConfigurationError("timeout cannot be negative")
        if self.expected_markers and self.timeout == 0:
            raise ConfigurationError("expected markers require a nonzero --timeout")
        if self.read_timeout <= 0 or self.write_timeout <= 0:
            raise ConfigurationError("serial timeouts must be greater than zero")
        if self.reconnect_interval <= 0:
            raise ConfigurationError("reconnect interval must be greater than zero")
        if self.read_size <= 0:
            raise ConfigurationError("read size must be greater than zero")
        if self.max_resets < 0:
            raise ConfigurationError("max resets cannot be negative")
        if self.send_delay < 0 or self.send_interval < 0:
            raise ConfigurationError("send delays cannot be negative")
        if self.raw_log.resolve() == self.normalized_log.resolve():
            raise ConfigurationError("raw and normalized logs must be different files")
        if any(not marker for marker in self.expected_markers):
            raise ConfigurationError("expected markers cannot be empty")
        if len(set(self.expected_markers)) != len(self.expected_markers):
            raise ConfigurationError("expected markers must be unique")
        if any(not marker for marker in self.panic_markers):
            raise ConfigurationError("panic markers cannot be empty")
        if any(not marker for marker in self.reset_markers):
            raise ConfigurationError("reset markers cannot be empty")


@dataclass(frozen=True)
class MonitorResult:
    exit_code: int
    reason: str
    expected_found: Tuple[str, ...]
    connections: int
    disconnects: int
    resets: int
    raw_bytes: int


class BoardLock:
    """Nonblocking, process-wide lock shared by every P2 HIL operation."""

    def __init__(
        self,
        path: pathlib.Path,
        timeout: float = 0.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout < 0:
            raise ConfigurationError("lock timeout cannot be negative")
        self.path = pathlib.Path(path)
        self.timeout = timeout
        self.monotonic = monotonic
        self.sleep = sleep
        self._file: Optional[TextIO] = None

    @property
    def fileno(self) -> int:
        if self._file is None:
            raise RuntimeError("board lock is not held")
        return self._file.fileno()

    def __enter__(self) -> "BoardLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.path.open("a+", encoding="utf-8")
        deadline = self.monotonic() + self.timeout

        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if self.monotonic() >= deadline:
                    lock_file.close()
                    raise LockBusyError(
                        "P2 board lock is busy: {}".format(self.path)
                    ) from exc
                self.sleep(min(0.05, max(0.0, deadline - self.monotonic())))

        try:
            lock_file.seek(0)
            lock_file.truncate()
            lock_file.write("pid={} utc={}\n".format(os.getpid(), utc_timestamp()))
            lock_file.flush()
        except Exception:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            raise
        self._file = lock_file
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._file is not None:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None


class InheritedBoardLock:
    """Validate a caller-owned lock descriptor without releasing its lock.

    A future HIL orchestrator can retain the lock for build/load/monitor/parse
    and pass the descriptor to this process with ``pass_fds``.  A bare
    ``--no-lock`` escape hatch is deliberately not provided.
    """

    def __init__(self, fd: int, path: pathlib.Path) -> None:
        self.fd = fd
        self.path = pathlib.Path(path)

    def __enter__(self) -> "InheritedBoardLock":
        try:
            fd_stat = os.fstat(self.fd)
            path_stat = self.path.stat()
        except (OSError, ValueError) as exc:
            raise ConfigurationError(
                "inherited board-lock descriptor is not valid"
            ) from exc
        if (fd_stat.st_dev, fd_stat.st_ino) != (path_stat.st_dev, path_stat.st_ino):
            raise ConfigurationError(
                "inherited descriptor does not refer to {}".format(self.path)
            )
        try:
            # This is idempotent when the descriptor already carries the
            # orchestrator's flock.  If it does not, acquiring it here still
            # preserves exclusivity until that orchestrator closes the fd.
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockBusyError(
                "inherited P2 board lock is busy: {}".format(self.path)
            ) from exc
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        # The orchestrator owns both the descriptor and the lock lifetime.
        return None


class SerialTextStream:
    """Incrementally decode UTF-8 and normalize CR, LF, and CRLF lines."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._line = []
        self._last_was_cr = False

    def feed(self, data: bytes) -> Tuple[str, Tuple[str, ...]]:
        text = self._decoder.decode(data)
        return text, self._consume(text, final=False)

    def finish(self) -> Tuple[str, Tuple[str, ...]]:
        text = self._decoder.decode(b"", final=True)
        lines = list(self._consume(text, final=False))
        if self._line:
            lines.append("".join(self._line))
            self._line.clear()
        self._last_was_cr = False
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        return text, tuple(lines)

    def _consume(self, text: str, final: bool) -> Tuple[str, ...]:
        del final  # Kept explicit to make the streaming/final boundary clear.
        lines = []
        for character in text:
            if character == "\n":
                if self._last_was_cr:
                    self._last_was_cr = False
                    continue
                lines.append("".join(self._line))
                self._line.clear()
                continue
            if character == "\r":
                lines.append("".join(self._line))
                self._line.clear()
                self._last_was_cr = True
                continue
            self._last_was_cr = False
            self._line.append(character)
        return tuple(lines)


class StreamDetector:
    """Find literal markers even when serial reads divide a marker."""

    def __init__(
        self,
        expected: Sequence[str],
        panic: Sequence[str],
        reset: Sequence[str],
    ) -> None:
        self.expected = tuple(dict.fromkeys(expected))
        specs = []
        for index, marker in enumerate(self.expected):
            specs.append(("expected", index, marker, False))
        for index, marker in enumerate(dict.fromkeys(panic)):
            specs.append(("panic", index, marker, True))
        for index, marker in enumerate(dict.fromkeys(reset)):
            specs.append(("reset", index, marker, True))
        self._patterns = tuple(
            (
                kind,
                index,
                marker,
                re.compile(re.escape(marker), re.IGNORECASE if ignore_case else 0),
            )
            for kind, index, marker, ignore_case in specs
        )
        longest = max((len(spec[2]) for spec in specs), default=1)
        self._overlap = max(4096, longest)
        self._tail = ""
        self._total = 0

    def feed(self, text: str) -> Tuple[Tuple[str, int, str], ...]:
        if not text:
            return ()
        previous_total = self._total
        combined = self._tail + text
        base_offset = previous_total - len(self._tail)
        events = []

        for kind, index, marker, pattern in self._patterns:
            for match in pattern.finditer(combined):
                absolute_end = base_offset + match.end()
                if absolute_end > previous_total:
                    events.append((kind, index, marker))

        self._total += len(text)
        self._tail = combined[-self._overlap :]
        return tuple(events)

    def break_stream(self) -> None:
        """Prevent a literal marker from spanning a serial disconnect."""

        self._tail = ""


class Reporter:
    def __init__(
        self,
        normalized: TextIO,
        utc_now: Callable[[], datetime.datetime],
        output: Optional[TextIO],
        diagnostics: Optional[TextIO],
    ) -> None:
        self.normalized = normalized
        self.utc_now = utc_now
        self.output = output
        self.diagnostics = diagnostics

    def serial_line(self, line: str) -> None:
        rendered = "[{}] {}\n".format(utc_timestamp(self.utc_now()), line)
        self.normalized.write(rendered)
        self.normalized.flush()
        if self.output is not None:
            self.output.write(rendered)
            self.output.flush()

    def event(self, name: str, detail: str = "") -> None:
        suffix = " {}".format(detail) if detail else ""
        rendered = "[{}] MONITOR:{}{}\n".format(
            utc_timestamp(self.utc_now()), name, suffix
        )
        self.normalized.write(rendered)
        self.normalized.flush()
        if self.diagnostics is not None:
            self.diagnostics.write(rendered)
            self.diagnostics.flush()


class SerialMonitor:
    """Serial capture loop independent of CLI gating and board locking."""

    def __init__(
        self,
        config: MonitorConfig,
        serial_factory: Callable[..., object],
        serial_exceptions: Iterable[type] = (OSError,),
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        utc_now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(
            datetime.timezone.utc
        ),
        output: Optional[TextIO] = sys.stdout,
        diagnostics: Optional[TextIO] = sys.stderr,
    ) -> None:
        config.validate()
        self.config = config
        self.serial_factory = serial_factory
        exception_types = tuple(serial_exceptions)
        self.serial_exceptions = tuple(dict.fromkeys(exception_types + (OSError,)))
        self.monotonic = monotonic
        self.sleep = sleep
        self.utc_now = utc_now
        self.output = output
        self.diagnostics = diagnostics

    def run(self) -> MonitorResult:
        config = self.config
        config.raw_log.parent.mkdir(parents=True, exist_ok=True)
        config.normalized_log.parent.mkdir(parents=True, exist_ok=True)

        with config.raw_log.open("wb") as raw_log, config.normalized_log.open(
            "w", encoding="utf-8", newline="\n"
        ) as normalized_log:
            reporter = Reporter(
                normalized_log, self.utc_now, self.output, self.diagnostics
            )
            reporter.event(
                "START",
                "port={} baud={} raw={} normalized={}".format(
                    config.port,
                    config.baud,
                    config.raw_log,
                    config.normalized_log,
                ),
            )
            text_stream = SerialTextStream()
            detector = StreamDetector(
                config.expected_markers,
                config.panic_markers,
                config.reset_markers,
            )

            try:
                result = self._capture(raw_log, reporter, text_stream, detector)
            except KeyboardInterrupt:
                result = MonitorResult(
                    EXIT_INTERRUPTED,
                    "interrupted",
                    (),
                    0,
                    0,
                    0,
                    raw_log.tell(),
                )
            except (SerialProtocolError,) + self.serial_exceptions as exc:
                reporter.event("SERIAL_ERROR", safe_error(exc))
                result = MonitorResult(
                    EXIT_SERIAL_ERROR,
                    "serial error: {}".format(safe_error(exc)),
                    (),
                    0,
                    0,
                    0,
                    raw_log.tell(),
                )

            trailing_text, trailing_lines = text_stream.finish()
            # ASCII markers are already decoded by feed().  This handles only a
            # final buffered UTF-8 sequence and keeps the normalized log intact.
            if trailing_text:
                detector.feed(trailing_text)
            for line in trailing_lines:
                reporter.serial_line(line)
            reporter.event(
                "RESULT",
                "code={} reason={} connections={} disconnects={} resets={} bytes={}".format(
                    result.exit_code,
                    result.reason,
                    result.connections,
                    result.disconnects,
                    result.resets,
                    result.raw_bytes,
                ),
            )
            return result

    def _capture(
        self,
        raw_log: BinaryIO,
        reporter: Reporter,
        text_stream: SerialTextStream,
        detector: StreamDetector,
    ) -> MonitorResult:
        config = self.config
        start = self.monotonic()
        deadline = start + config.timeout if config.timeout else None
        connection = None
        connections = 0
        disconnects = 0
        resets = 0
        raw_bytes = 0
        expected_found = set()
        sends_completed = False
        last_connect_error = None

        try:
            while True:
                if deadline is not None and self.monotonic() >= deadline:
                    return self._deadline_result(
                        expected_found,
                        connections,
                        disconnects,
                        resets,
                        raw_bytes,
                        connection is not None,
                    )

                if connection is None:
                    try:
                        connection = self._open_serial()
                    except self.serial_exceptions as exc:
                        error = safe_error(exc)
                        if error != last_connect_error:
                            reporter.event("CONNECT_FAILED", error)
                            last_connect_error = error
                        self._sleep_until_retry(deadline)
                        continue

                    connections += 1
                    event = "CONNECTED" if connections == 1 else "RECONNECTED"
                    reporter.event(event, "attempt={}".format(connections))
                    last_connect_error = None

                    if config.sends and not sends_completed:
                        try:
                            self._send_commands(connection, reporter, deadline)
                            sends_completed = True
                        except self.serial_exceptions as exc:
                            disconnects += 1
                            self._break_stream(reporter, text_stream, detector)
                            reporter.event(
                                "DISCONNECTED",
                                "during-send {}".format(safe_error(exc)),
                            )
                            self._close(connection)
                            connection = None
                            self._sleep_until_retry(deadline)
                            continue

                try:
                    data = connection.read(config.read_size)
                except self.serial_exceptions as exc:
                    disconnects += 1
                    self._break_stream(reporter, text_stream, detector)
                    reporter.event("DISCONNECTED", safe_error(exc))
                    self._close(connection)
                    connection = None
                    self._sleep_until_retry(deadline)
                    continue

                if not isinstance(data, (bytes, bytearray)):
                    raise SerialProtocolError("serial read() did not return bytes")
                if not data:
                    continue

                data = bytes(data)
                raw_log.write(data)
                raw_log.flush()
                raw_bytes += len(data)
                decoded, lines = text_stream.feed(data)
                for line in lines:
                    reporter.serial_line(line)

                panic_marker = None
                unexpected_reset = None
                for kind, index, marker in detector.feed(decoded):
                    if kind == "expected":
                        if index not in expected_found:
                            expected_found.add(index)
                            reporter.event("EXPECTED", repr(marker))
                    elif kind == "panic":
                        if panic_marker is None:
                            panic_marker = marker
                            reporter.event("PANIC", repr(marker))
                    elif kind == "reset":
                        resets += 1
                        reporter.event(
                            "RESET", "count={} marker={!r}".format(resets, marker)
                        )
                        if resets > config.max_resets and unexpected_reset is None:
                            unexpected_reset = marker

                if panic_marker is not None:
                    return self._result(
                        EXIT_PANIC,
                        "panic marker: {}".format(panic_marker),
                        expected_found,
                        connections,
                        disconnects,
                        resets,
                        raw_bytes,
                    )
                if unexpected_reset is not None:
                    return self._result(
                        EXIT_UNEXPECTED_RESET,
                        "unexpected reset marker: {}".format(unexpected_reset),
                        expected_found,
                        connections,
                        disconnects,
                        resets,
                        raw_bytes,
                    )
                if (
                    len(expected_found) == len(config.expected_markers)
                    and config.expected_markers
                ):
                    return self._result(
                        EXIT_OK,
                        "all expected markers observed",
                        expected_found,
                        connections,
                        disconnects,
                        resets,
                        raw_bytes,
                    )
        finally:
            if connection is not None:
                self._close(connection)

    def _open_serial(self):
        arguments = dict(
            port=self.config.port,
            baudrate=self.config.baud,
            timeout=self.config.read_timeout,
            write_timeout=self.config.write_timeout,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
            exclusive=True,
        )
        try:
            return self.serial_factory(**arguments)
        except TypeError as exc:
            # pyserial before support for exclusive=, and simple test doubles,
            # can still rely on the process-wide board lock.
            if "exclusive" not in str(exc):
                raise
            arguments.pop("exclusive")
            return self.serial_factory(**arguments)

    def _send_commands(self, connection, reporter: Reporter, deadline) -> None:
        if self.config.send_delay:
            self._bounded_sleep(self.config.send_delay, deadline)
        for index, command in enumerate(self.config.sends):
            payload = command.encode("utf-8") + self.config.send_ending
            written = connection.write(payload)
            if written is not None and written != len(payload):
                raise SerialProtocolError(
                    "short serial write: {} of {} bytes".format(written, len(payload))
                )
            connection.flush()
            reporter.event(
                "TX",
                "index={} bytes={} text={!r}".format(index, len(payload), command),
            )
            if index + 1 < len(self.config.sends) and self.config.send_interval:
                self._bounded_sleep(self.config.send_interval, deadline)

    def _bounded_sleep(self, duration: float, deadline) -> None:
        if deadline is None:
            self.sleep(duration)
            return
        remaining = deadline - self.monotonic()
        if remaining > 0:
            self.sleep(min(duration, remaining))

    def _sleep_until_retry(self, deadline) -> None:
        self._bounded_sleep(self.config.reconnect_interval, deadline)

    @staticmethod
    def _break_stream(
        reporter: Reporter,
        text_stream: SerialTextStream,
        detector: StreamDetector,
    ) -> None:
        trailing_text, trailing_lines = text_stream.finish()
        if trailing_text:
            detector.feed(trailing_text)
        for line in trailing_lines:
            reporter.serial_line(line)
        detector.break_stream()

    def _deadline_result(
        self,
        expected_found,
        connections,
        disconnects,
        resets,
        raw_bytes,
        connected,
    ) -> MonitorResult:
        if connections == 0:
            code = EXIT_CONNECT_FAILED
            reason = "serial port was never connected"
        elif not connected and disconnects:
            code = EXIT_DISCONNECTED
            reason = "serial port disconnected and did not reconnect"
        elif self.config.expected_markers:
            missing = [
                marker
                for index, marker in enumerate(self.config.expected_markers)
                if index not in expected_found
            ]
            code = EXIT_EXPECT_TIMEOUT
            reason = "expected-marker timeout; missing {}".format(
                ", ".join(repr(marker) for marker in missing)
            )
        else:
            code = EXIT_OK
            reason = "capture duration completed"
        return self._result(
            code,
            reason,
            expected_found,
            connections,
            disconnects,
            resets,
            raw_bytes,
        )

    def _result(
        self,
        code,
        reason,
        expected_found,
        connections,
        disconnects,
        resets,
        raw_bytes,
    ) -> MonitorResult:
        ordered = tuple(
            marker
            for index, marker in enumerate(self.config.expected_markers)
            if index in expected_found
        )
        return MonitorResult(
            code,
            reason,
            ordered,
            connections,
            disconnects,
            resets,
            raw_bytes,
        )

    @staticmethod
    def _close(connection) -> None:
        with contextlib.suppress(Exception):
            connection.close()


def safe_error(error: BaseException) -> str:
    text = str(error).replace("\r", " ").replace("\n", " ").strip()
    return text or error.__class__.__name__


def utc_timestamp(now: Optional[datetime.datetime] = None) -> str:
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return (
        now.astimezone(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def artifact_log_paths(args) -> Tuple[pathlib.Path, pathlib.Path]:
    raw = pathlib.Path(args.raw_log).expanduser() if args.raw_log else None
    normalized = (
        pathlib.Path(args.normalized_log).expanduser() if args.normalized_log else None
    )
    if raw is None and normalized is None:
        if args.artifact_dir:
            directory = pathlib.Path(args.artifact_dir).expanduser()
        else:
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y%m%dT%H%M%S.%fZ"
            )
            directory = REPO_ROOT / "artifacts" / "hil" / "{}-monitor".format(stamp)
        raw = directory / "console.raw"
        normalized = directory / "console.log"
    elif raw is None:
        raw = normalized.with_name(normalized.name + ".raw")
    elif normalized is None:
        normalized = raw.with_name(raw.name + ".log")
    return raw, normalized


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture the P2 serial console with raw and timestamped logs",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "exit codes: 0 success; 2 safety/configuration refusal; "
            "3 expected-marker timeout; 4 panic; 5 unexpected reset; "
            "6 never connected; 7 disconnect not recovered; "
            "8 serial error; 9 lock busy; 130 interrupted"
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="open the serial port (also requires P2_HIL=1)",
    )
    parser.add_argument("--port", default=os.getenv("P2_PORT", ""))
    parser.add_argument(
        "--baud", type=int, default=os.getenv("P2_CONSOLE_BAUD", "230400")
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="expected-marker timeout, or capture duration without --expect; 0 runs forever",
    )
    parser.add_argument("--read-timeout", type=float, default=0.1)
    parser.add_argument("--write-timeout", type=float, default=1.0)
    parser.add_argument("--reconnect-interval", type=float, default=0.25)
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="TEXT",
        help="literal marker to require; repeat to require all markers",
    )
    parser.add_argument(
        "--send",
        action="append",
        default=[],
        metavar="TEXT",
        help="UTF-8 command to send after the first connection; repeat as needed",
    )
    parser.add_argument("--send-ending", choices=tuple(LINE_ENDINGS), default="crlf")
    parser.add_argument("--send-delay", type=float, default=0.0)
    parser.add_argument("--send-interval", type=float, default=0.0)
    parser.add_argument(
        "--panic-marker",
        action="append",
        default=[],
        metavar="TEXT",
        help="additional case-insensitive panic marker",
    )
    parser.add_argument(
        "--reset-marker",
        action="append",
        default=[],
        metavar="TEXT",
        help="additional case-insensitive boot/reset marker",
    )
    parser.add_argument("--no-default-panic-markers", action="store_true")
    parser.add_argument("--no-default-reset-markers", action="store_true")
    parser.add_argument(
        "--max-resets",
        type=int,
        default=1,
        help="number of reset markers allowed before reporting an unexpected reset",
    )
    parser.add_argument("--artifact-dir")
    parser.add_argument("--raw-log")
    parser.add_argument("--normalized-log", "--log", dest="normalized_log")
    parser.add_argument(
        "--lock-file",
        default=os.getenv("P2_LOCK_FILE") or str(DEFAULT_LOCK_FILE),
    )
    parser.add_argument("--lock-timeout", type=float, default=0.0)
    parser.add_argument(
        "--inherited-lock-fd",
        type=int,
        help="descriptor for the already-held --lock-file, passed by a HIL orchestrator",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="write serial lines only to the log"
    )
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    serial_factory: Optional[Callable[..., object]] = None,
    serial_exceptions: Optional[Iterable[type]] = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.execute:
        print(
            "DRY-RUN: serial port was not opened; pass --execute with P2_HIL=1",
            file=sys.stderr,
        )
        return EXIT_SAFETY
    if os.getenv("P2_HIL", "0") != "1":
        print("HIL REQUIRED: set P2_HIL=1 before --execute", file=sys.stderr)
        return EXIT_SAFETY

    raw_log, normalized_log = artifact_log_paths(args)
    panic_markers = () if args.no_default_panic_markers else DEFAULT_PANIC_MARKERS
    reset_markers = () if args.no_default_reset_markers else DEFAULT_RESET_MARKERS
    config = MonitorConfig(
        port=args.port,
        baud=args.baud,
        raw_log=raw_log,
        normalized_log=normalized_log,
        timeout=args.timeout,
        read_timeout=args.read_timeout,
        write_timeout=args.write_timeout,
        reconnect_interval=args.reconnect_interval,
        expected_markers=tuple(args.expect),
        panic_markers=tuple(panic_markers) + tuple(args.panic_marker),
        reset_markers=tuple(reset_markers) + tuple(args.reset_marker),
        max_resets=args.max_resets,
        sends=tuple(args.send),
        send_ending=LINE_ENDINGS[args.send_ending],
        send_delay=args.send_delay,
        send_interval=args.send_interval,
    )
    try:
        config.validate()
        lock_path = pathlib.Path(args.lock_file).expanduser().resolve()
        if args.inherited_lock_fd is None:
            lock_context = BoardLock(lock_path, timeout=args.lock_timeout)
        else:
            lock_context = InheritedBoardLock(args.inherited_lock_fd, lock_path)
    except ConfigurationError as exc:
        print("CONFIGURATION ERROR: {}".format(exc), file=sys.stderr)
        return EXIT_SAFETY
    except OSError as exc:
        print("I/O ERROR: {}".format(safe_error(exc)), file=sys.stderr)
        return EXIT_SERIAL_ERROR

    if serial_factory is None:
        try:
            import serial
        except ImportError:
            print(
                "CONFIGURATION ERROR: pyserial is required; install tools/p2/requirements-hil.txt",
                file=sys.stderr,
            )
            return EXIT_SAFETY
        serial_factory = serial.Serial
        serial_exceptions = (serial.SerialException, OSError)
    elif serial_exceptions is None:
        serial_exceptions = (OSError,)

    try:
        with lock_context:
            monitor = SerialMonitor(
                config,
                serial_factory,
                serial_exceptions=serial_exceptions,
                output=None if args.quiet else sys.stdout,
                diagnostics=sys.stderr,
            )
            return monitor.run().exit_code
    except LockBusyError as exc:
        print("LOCK BUSY: {}".format(exc), file=sys.stderr)
        return EXIT_LOCK_BUSY
    except ConfigurationError as exc:
        print("CONFIGURATION ERROR: {}".format(exc), file=sys.stderr)
        return EXIT_SAFETY
    except OSError as exc:
        print("I/O ERROR: {}".format(safe_error(exc)), file=sys.stderr)
        return EXIT_SERIAL_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
