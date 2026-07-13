#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run a bracketed ten-second and ten-minute P2 GETCT qualification.

The target is RAM-loaded through the pinned ``loadp2`` executable.  That one
process retains the serial port for the full campaign; this host sends at most
one ``S`` request at a time through its terminal stdin and waits for the exact
matching ``P2CLOCK:SAMPLE`` before scheduling the next request.

Without ``--execute`` this command is a no-op.  A production run always uses
the fixed one-second cadence, five-second maximum gap, >=10-second immediate
result, >=600-second final result, and broad +/-1 percent structural gate.
"""

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import stat
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import clock_protocol
import hil
import monitor


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
APPS_ROOT = REPO_ROOT.parent / "apps"
DEFAULT_IMAGE = REPO_ROOT / "nuttx"
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "hil"
DEFAULT_TOOLCHAIN_LOCK = REPO_ROOT / "tools" / "p2" / "toolchain.lock"

SHORT_DURATION_SECONDS = 10.0
FINAL_DURATION_SECONDS = 600.0
SAMPLE_INTERVAL_SECONDS = 1.0
READY_TIMEOUT_SECONDS = 30.0
SAMPLE_RESPONSE_TIMEOUT_SECONDS = 3.0
DONE_TIMEOUT_SECONDS = 5.0
POST_DONE_QUIET_SECONDS = 0.5
READ_POLL_SECONDS = 0.10
LOADP2_FIFO_BYTES = 16384

EXIT_OK = 0
EXIT_HIL_FAILURE = 1
EXIT_SAFETY = 2
EXIT_LOCK_BUSY = 9
EXIT_INTERRUPTED = 130

CLOCK_REQUIRED_CONFIG = (
    ("CONFIG_ARCH", '"p2"'),
    ("CONFIG_ARCH_P2", "y"),
    ("CONFIG_ARCH_CHIP", '"p2x8c4m64p"'),
    ("CONFIG_ARCH_CHIP_P2X8C4M64P", "y"),
    ("CONFIG_ARCH_BOARD", '"p2-ec32mb"'),
    ("CONFIG_ARCH_BOARD_P2_EC32MB", "y"),
    ("CONFIG_ARCH_TOOLCHAIN_CLANG", "y"),
    ("CONFIG_BUILD_FLAT", "y"),
    ("CONFIG_DEFAULT_TASK_STACKSIZE", "2048"),
    ("CONFIG_DEV_CONSOLE", "y"),
    ("CONFIG_DISABLE_ENVIRON", "y"),
    ("CONFIG_DISABLE_MOUNTPOINT", "y"),
    ("CONFIG_DISABLE_MQUEUE", "y"),
    ("CONFIG_DISABLE_POSIX_TIMERS", "y"),
    ("CONFIG_INIT_ENTRYPOINT", '"p2clock_main"'),
    ("CONFIG_INIT_PRIORITY", "100"),
    ("CONFIG_INIT_STACKSIZE", "2048"),
    ("CONFIG_INTELHEX_BINARY", "n"),
    ("CONFIG_LIBC_ARCH_ATOMIC", "y"),
    ("CONFIG_LIBM_NONE", "y"),
    ("CONFIG_P2_BOOT_TRACE", "y"),
    ("CONFIG_P2_SYSCLK_HZ", "180000000"),
    ("CONFIG_P2_XTAL_HZ", "20000000"),
    ("CONFIG_RAM_SIZE", "524288"),
    ("CONFIG_RAW_BINARY", "y"),
    ("CONFIG_RR_INTERVAL", "200"),
    ("CONFIG_SCHED_HPWORK", "n"),
    ("CONFIG_START_DAY", "1"),
    ("CONFIG_START_MONTH", "1"),
    ("CONFIG_START_YEAR", "2026"),
    ("CONFIG_TESTING_P2CLOCK", "y"),
    ("CONFIG_UART0_BAUD", "230400"),
    ("CONFIG_UART0_SERIAL_CONSOLE", "y"),
)

PANIC_PATTERNS = (
    re.compile(r"\bPANIC\b", re.IGNORECASE),
    re.compile(r"\bASSERT(?:ION)?\b", re.IGNORECASE),
    re.compile(r"STACK\s+OVERFLOW", re.IGNORECASE),
    re.compile(r"UNEXPECTED\s+IRQ", re.IGNORECASE),
    re.compile(r"REGISTER\s+DUMP", re.IGNORECASE),
)

TARGET_RESET_MARKERS = (
    "P2BOOT:ENTRY",
    "P2HELLO:ENTRY",
)


class SafetyError(ValueError):
    """An execution gate or immutable clock-run input is invalid."""


class ClockRunError(RuntimeError):
    """The loaded target did not complete the clock protocol."""


@dataclass(frozen=True)
class ClockRunConfig:
    port: str
    image: pathlib.Path
    loadp2: pathlib.Path
    toolchain_lock: pathlib.Path
    generated_config: pathlib.Path
    artifact_dir: pathlib.Path
    board_lock: pathlib.Path
    loader_baud: int
    console_baud: int
    reset_flag: str
    lock_timeout: float
    image_sha256: str
    loadp2_sha256: str
    config_sha256: str

    def validate(self) -> None:
        if not self.port or not pathlib.Path(self.port).is_absolute():
            raise SafetyError("an absolute serial port is required")
        if not self.image.is_file() or self.image.stat().st_size == 0:
            raise SafetyError("clock image is missing or empty: {}".format(self.image))
        if not self.loadp2.is_file() or not os.access(self.loadp2, os.X_OK):
            raise SafetyError("pinned LOADP2 is unavailable: {}".format(self.loadp2))
        if not self.toolchain_lock.is_file():
            raise SafetyError(
                "toolchain lock is unavailable: {}".format(self.toolchain_lock)
            )
        if not self.generated_config.is_file():
            raise SafetyError("generated .config is unavailable")
        if self.artifact_dir.exists():
            raise SafetyError(
                "clock artifact directory already exists: {}".format(
                    self.artifact_dir
                )
            )
        if self.loader_baud <= 0 or self.console_baud <= 0:
            raise SafetyError("loader and console baud must be positive")
        if self.reset_flag not in ("-DTR", "-RTS"):
            raise SafetyError("clock runner requires exactly one DTR or RTS reset")
        if self.lock_timeout < 0:
            raise SafetyError("lock timeout cannot be negative")
        if sha256_file(self.image) != self.image_sha256:
            raise SafetyError("clock image changed after validation")
        if sha256_file(self.loadp2) != self.loadp2_sha256:
            raise SafetyError("LOADP2 changed after validation")
        if sha256_file(self.generated_config) != self.config_sha256:
            raise SafetyError("generated .config changed after validation")


@dataclass(frozen=True)
class TimedMarker:
    marker: clock_protocol.Marker
    receive_monotonic: float
    receive_utc: str


def utc_timestamp(now: datetime.datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return (
        now.astimezone(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def artifact_stamp(now: datetime.datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return now.astimezone(datetime.timezone.utc).strftime(
        "%Y%m%dT%H%M%S.%fZ-clock"
    )


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: pathlib.Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def validate_clock_config(values: Mapping[str, str]) -> None:
    mismatches = [
        "{}={} (required {})".format(
            name, values.get(name, "<unset>"), expected
        )
        for name, expected in CLOCK_REQUIRED_CONFIG
        if values.get(name) != expected
    ]
    if mismatches:
        raise SafetyError(
            "image does not match the locked clock profile: {}".format(
                ", ".join(mismatches)
            )
        )


def build_command(config: ClockRunConfig) -> Tuple[str, ...]:
    command = (
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
        "-t",
        str(config.image),
    )
    if command.count("-DTR") + command.count("-RTS") != 1:
        raise SafetyError("loadp2 command must contain exactly one reset flag")
    if {"-PATCH", "-FLASH"}.intersection(command):
        raise SafetyError("flash option entered the RAM-only clock command")
    if command[0] != str(config.loadp2) or command[-1] != str(config.image):
        raise SafetyError("loadp2 command path or image changed unexpectedly")
    return command


def preserve_inputs(config: ClockRunConfig) -> Dict[str, str]:
    input_dir = config.artifact_dir / "inputs"
    input_dir.mkdir()
    candidates = (
        ("nuttx", config.image),
        ("nuttx.bin", REPO_ROOT / "nuttx.bin"),
        ("nuttx.map", REPO_ROOT / "nuttx.map"),
        ("System.map", REPO_ROOT / "System.map"),
        (".config", config.generated_config),
        ("toolchain.lock", config.toolchain_lock),
        ("clock_protocol.py", REPO_ROOT / "tools" / "p2" / "clock_protocol.py"),
        ("test-clock.py", REPO_ROOT / "tools" / "p2" / "test-clock.py"),
        (
            "clock-defconfig",
            REPO_ROOT
            / "boards"
            / "p2"
            / "p2x8c4m64p"
            / "p2-ec32mb"
            / "configs"
            / "clock"
            / "defconfig",
        ),
        ("p2clock_main.c", APPS_ROOT / "testing" / "p2clock" / "p2clock_main.c"),
        ("p2clock-Kconfig", APPS_ROOT / "testing" / "p2clock" / "Kconfig"),
        (
            "p2clock-CMakeLists.txt",
            APPS_ROOT / "testing" / "p2clock" / "CMakeLists.txt",
        ),
        ("p2clock-Makefile", APPS_ROOT / "testing" / "p2clock" / "Makefile"),
        ("p2clock-Make.defs", APPS_ROOT / "testing" / "p2clock" / "Make.defs"),
    )
    manifest = {}
    for name, source in candidates:
        if not source.is_file():
            continue
        destination = input_dir / name
        shutil.copy2(source, destination)
        manifest["inputs/{}".format(name)] = sha256_file(destination)
    return dict(sorted(manifest.items()))


class ClockRunner:
    """One-loadp2-session raw-counter qualification runner."""

    def __init__(
        self,
        config: ClockRunConfig,
        process_factory: Callable[
            [Sequence[str]], object
        ] = hil.default_process_factory,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(
            datetime.timezone.utc
        ),
        lock_factory: Callable[..., object] = monitor.BoardLock,
        owner_probe: Callable[[str], Tuple[int, ...]] = hil.default_owner_probe,
        short_duration: float = SHORT_DURATION_SECONDS,
        final_duration: float = FINAL_DURATION_SECONDS,
        sample_interval: float = SAMPLE_INTERVAL_SECONDS,
        sample_response_timeout: float = SAMPLE_RESPONSE_TIMEOUT_SECONDS,
    ) -> None:
        if short_duration <= 0 or final_duration < short_duration:
            raise ValueError("clock qualification durations are invalid")
        if sample_interval <= 0 or sample_interval >= clock_protocol.MAX_GAP_SECONDS:
            raise ValueError("clock sample interval is invalid")
        if sample_response_timeout <= 0:
            raise ValueError("clock sample response timeout is invalid")
        self.config = config
        self.process_factory = process_factory
        self.monotonic = monotonic
        self.utc_now = utc_now
        self.lock_factory = lock_factory
        self.owner_probe = owner_probe
        self.short_duration = short_duration
        self.final_duration = final_duration
        self.sample_interval = sample_interval
        self.sample_response_timeout = sample_response_timeout
        self.parser = clock_protocol.ClockMarkerParser()
        self.stream = monitor.SerialTextStream()
        self.events: List[TimedMarker] = []
        self.session = None
        self.raw_log = None
        self.normalized_log = None
        self.raw_bytes = 0
        self.loader_returncode = None
        self.intentionally_terminated = False

    def run(self) -> bool:
        config = self.config
        config.validate()
        config.artifact_dir.mkdir(parents=True, exist_ok=False)
        preserved = preserve_inputs(config)
        command = build_command(config)
        started = self.utc_now()
        overall: Dict[str, object] = {
            "format": "p2-clock-hil-v1",
            "status": "RUNNING",
            "started_utc": utc_timestamp(started),
            "port": config.port,
            "image": str(config.image),
            "image_sha256": config.image_sha256,
            "loadp2": str(config.loadp2),
            "loadp2_sha256": config.loadp2_sha256,
            "toolchain_lock": str(config.toolchain_lock),
            "config_sha256": config.config_sha256,
            "board_lock": str(config.board_lock),
            "loader_baud": config.loader_baud,
            "console_baud": config.console_baud,
            "loadp2_fifo_bytes": LOADP2_FIFO_BYTES,
            "reset_flag": config.reset_flag,
            "sample_interval_seconds": self.sample_interval,
            "maximum_gap_seconds": clock_protocol.MAX_GAP_SECONDS,
            "short_duration_seconds": self.short_duration,
            "final_duration_seconds": self.final_duration,
            "nominal_frequency_hz": clock_protocol.EXPECTED_SYSCLK_HZ,
            "structural_sanity_fraction": (
                clock_protocol.STRUCTURAL_SANITY_FRACTION
            ),
            "tolerance_policy": (
                "broad +/-1 percent structural sanity only; "
                "no oscillator accuracy tolerance"
            ),
            "qualification_rule": "last.send-first.receive >= duration",
            "commands": {"sample": "S\\r", "quit": "Q\\r"},
            "one_outstanding_sample_command": True,
            "preserved_input_sha256": preserved,
            "samples_recorded": 0,
        }
        write_json(config.artifact_dir / "metadata.json", overall)
        write_json(
            config.artifact_dir / "command.json",
            {"argv": list(command), "shell_escaped": shlex.join(command)},
        )
        samples: List[clock_protocol.ClockSample] = []
        short_result = None
        final_result = None
        failure_reason = None
        passed = False

        try:
            with self.lock_factory(
                config.board_lock,
                timeout=config.lock_timeout,
                monotonic=self.monotonic,
            ):
                owners = self.owner_probe(config.port)
                if owners:
                    raise SafetyError(
                        "serial port is already owned by PID(s): {}".format(
                            ", ".join(str(owner) for owner in owners)
                        )
                    )
                self._verify_immutable_inputs()
                with (config.artifact_dir / "console.raw").open("wb") as raw_log, (
                    config.artifact_dir / "console.log"
                ).open("w", encoding="utf-8", newline="\n") as normalized_log, (
                    config.artifact_dir / "samples.jsonl"
                ).open("w", encoding="utf-8", newline="\n") as sample_log:
                    self.raw_log = raw_log
                    self.normalized_log = normalized_log
                    self.session = self.process_factory(command)
                    ready = self._wait_for("ready", READY_TIMEOUT_SECONDS)
                    overall["ready_marker"] = ready.marker.line

                    while final_result is None:
                        if samples:
                            due = samples[-1].send_monotonic + self.sample_interval
                            self._idle_until(due)
                        self._verify_immutable_inputs()
                        if self.events:
                            raise ClockRunError(
                                "target marker arrived without an outstanding command"
                            )

                        send_monotonic = self.monotonic()
                        send_utc = utc_timestamp(self.utc_now())
                        self.session.write(b"S\r")
                        response = self._wait_for(
                            "sample", self.sample_response_timeout
                        )
                        marker = response.marker
                        if marker.sequence != len(samples):
                            raise ClockRunError(
                                "sample response sequence does not match request"
                            )
                        sample = clock_protocol.ClockSample(
                            sequence=marker.sequence,
                            counter=marker.counter,
                            send_monotonic=send_monotonic,
                            receive_monotonic=response.receive_monotonic,
                        )
                        candidate = samples + [sample]
                        clock_protocol.validate_samples(candidate)
                        previous = samples[-1] if samples else None
                        record = clock_protocol.sample_record(sample, previous)
                        record.update(
                            {
                                "send_utc": send_utc,
                                "receive_utc": response.receive_utc,
                            }
                        )
                        sample_log.write(json.dumps(record, sort_keys=True) + "\n")
                        sample_log.flush()
                        samples.append(sample)
                        if short_result is None:
                            prefix = clock_protocol.first_qualified_prefix(
                                samples, self.short_duration
                            )
                            if prefix is not None:
                                short_result = clock_protocol.calibration_result(
                                    prefix, self.short_duration
                                )
                                write_json(
                                    config.artifact_dir / "calibration-10s.json",
                                    short_result,
                                )
                                print(
                                    "P2 clock >=10s: {:.3f} Hz, {:.3f} ppm "
                                    "[{}]".format(
                                        short_result["frequency_estimate_hz"],
                                        short_result["ppm_estimate"],
                                        short_result["status"],
                                    ),
                                    flush=True,
                                )
                                if short_result["status"] != "PASS":
                                    raise ClockRunError(
                                        "short clock result failed structural sanity"
                                    )

                        prefix = clock_protocol.first_qualified_prefix(
                            samples, self.final_duration
                        )
                        if prefix is not None:
                            final_result = clock_protocol.calibration_result(
                                prefix, self.final_duration
                            )
                            write_json(
                                config.artifact_dir / "calibration-600s.json",
                                final_result,
                            )
                            print(
                                "P2 clock >=600s: {:.3f} Hz, {:.3f} ppm "
                                "[{}]".format(
                                    final_result["frequency_estimate_hz"],
                                    final_result["ppm_estimate"],
                                    final_result["status"],
                                ),
                                flush=True,
                            )
                            if final_result["status"] != "PASS":
                                raise ClockRunError(
                                    "final clock result failed structural sanity"
                                )

                        if len(samples) % 60 == 0:
                            print(
                                "P2 clock progress: {} samples, {:.1f}s "
                                "conservative elapsed".format(
                                    len(samples),
                                    samples[-1].send_monotonic
                                    - samples[0].receive_monotonic,
                                ),
                                flush=True,
                            )

                    self.session.write(b"Q\r")
                    done = self._wait_for("done", DONE_TIMEOUT_SECONDS)
                    if done.marker.sample_count != len(samples):
                        raise ClockRunError("DONE count does not match sample log")
                    if self.events:
                        raise ClockRunError("unexpected marker followed DONE")
                    self._drain_after_done(POST_DONE_QUIET_SECONDS)
                    self.intentionally_terminated = True
                    self._verify_immutable_inputs()
                    passed = True
        except (SafetyError, hil.SafetyError, monitor.ConfigurationError) as exc:
            failure_reason = monitor.safe_error(exc)
            raise
        except (
            ClockRunError,
            clock_protocol.ClockProtocolError,
            OSError,
            RuntimeError,
            ValueError,
        ) as exc:
            failure_reason = monitor.safe_error(exc)
        finally:
            self._finish_stream()
            self.loader_returncode = self._stop_session(
                self.session, self.loader_returncode
            )
            combined = {
                "format": "p2-clock-calibrations-v1",
                "short": short_result,
                "final": final_result,
            }
            write_json(config.artifact_dir / "calibration.json", combined)
            overall.update(
                {
                    "status": "PASS" if passed else "FAIL",
                    "ended_utc": utc_timestamp(self.utc_now()),
                    "samples_recorded": len(samples),
                    "raw_bytes": self.raw_bytes,
                    "ready_seen": self.parser.ready is not None,
                    "done_seen": self.parser.done is not None,
                    "done_sample_count": (
                        self.parser.done.sample_count
                        if self.parser.done is not None
                        else None
                    ),
                    "short_result": short_result,
                    "final_result": final_result,
                    "failure_reason": failure_reason,
                    "loader_returncode": self.loader_returncode,
                    "intentionally_terminated": self.intentionally_terminated,
                }
            )
            write_json(config.artifact_dir / "metadata.json", overall)
            write_json(config.artifact_dir / "status.json", overall)
            self.raw_log = None
            self.normalized_log = None
        return passed

    def _verify_immutable_inputs(self) -> None:
        config = self.config
        if sha256_file(config.image) != config.image_sha256:
            raise SafetyError("clock image changed during the run")
        if sha256_file(config.generated_config) != config.config_sha256:
            raise SafetyError("generated .config changed during the run")
        if sha256_file(config.loadp2) != config.loadp2_sha256:
            raise SafetyError("LOADP2 changed during the run")

    def _write_normalized(self, line: str) -> None:
        if self.normalized_log is not None:
            self.normalized_log.write(
                "[{}] {}\n".format(utc_timestamp(self.utc_now()), line)
            )
            self.normalized_log.flush()

    def _ingest(self, data: bytes) -> None:
        if self.raw_log is None:
            raise ClockRunError("raw log is not open")
        self.raw_log.write(data)
        self.raw_log.flush()
        self.raw_bytes += len(data)
        _text, lines = self.stream.feed(data)
        observed = self.monotonic()
        observed_utc = utc_timestamp(self.utc_now())
        for line in lines:
            self._write_normalized(line)
            if any(pattern.search(line) for pattern in PANIC_PATTERNS):
                raise ClockRunError("fatal console marker: {}".format(line))
            if self.parser.ready is not None and any(
                marker in line for marker in TARGET_RESET_MARKERS
            ):
                raise ClockRunError(
                    "unexpected target reset after P2CLOCK:READY: {}".format(
                        line
                    )
                )
            marker = self.parser.feed_line(line)
            if marker is not None:
                self.events.append(TimedMarker(marker, observed, observed_utc))

    def _finish_stream(self) -> None:
        try:
            _text, lines = self.stream.finish()
            for line in lines:
                self._write_normalized(line)
                if line.startswith("P2CLOCK:"):
                    self.parser.feed_line(line)
        except (
            clock_protocol.ClockProtocolError,
            OSError,
            RuntimeError,
            ValueError,
        ):
            return

    def _wait_for(self, kind: str, timeout: float) -> TimedMarker:
        deadline = self.monotonic() + timeout
        while True:
            if self.events:
                event = self.events.pop(0)
                if event.marker.kind != kind:
                    raise ClockRunError(
                        "received {} while waiting for {}".format(
                            event.marker.kind, kind
                        )
                    )
                return event
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise ClockRunError("timeout waiting for P2CLOCK:{}".format(kind))
            self._read_once(min(READ_POLL_SECONDS, remaining))

    def _idle_until(self, deadline: float) -> None:
        while self.monotonic() < deadline:
            if self.events:
                raise ClockRunError(
                    "target marker arrived without an outstanding S command"
                )
            self._read_once(min(READ_POLL_SECONDS, deadline - self.monotonic()))
        if self.events:
            raise ClockRunError(
                "target marker arrived without an outstanding S command"
            )

    def _read_once(self, timeout: float) -> None:
        if self.session is None:
            raise ClockRunError("loadp2 session is not running")
        chunk = self.session.read(max(0.0, timeout))
        if chunk is None:
            self.loader_returncode = self.session.poll()
            raise ClockRunError(
                "loadp2 terminal disconnected with code {}".format(
                    self.loader_returncode
                )
            )
        if chunk:
            if not isinstance(chunk, (bytes, bytearray)):
                raise ClockRunError("loadp2 output reader returned non-bytes")
            self._ingest(bytes(chunk))
        returncode = self.session.poll()
        if returncode is not None:
            self.loader_returncode = returncode
            if not self.events:
                raise ClockRunError(
                    "loadp2 exited with code {}".format(returncode)
                )

    def _drain_after_done(self, quiet_seconds: float) -> None:
        """Reject any target marker immediately following the terminal DONE."""

        deadline = self.monotonic() + quiet_seconds
        while self.monotonic() < deadline:
            if self.events:
                raise ClockRunError("unexpected marker followed DONE")
            if self.session is None:
                raise ClockRunError("loadp2 session is not running")
            chunk = self.session.read(
                min(READ_POLL_SECONDS, deadline - self.monotonic())
            )
            if chunk is None:
                returncode = self.session.poll()
                self.loader_returncode = returncode
                if returncode not in (None, 0):
                    raise ClockRunError(
                        "loadp2 exited with code {} after DONE".format(
                            returncode
                        )
                    )
                return
            if chunk:
                if not isinstance(chunk, (bytes, bytearray)):
                    raise ClockRunError(
                        "loadp2 output reader returned non-bytes"
                    )
                self._ingest(bytes(chunk))
            returncode = self.session.poll()
            if returncode is not None:
                self.loader_returncode = returncode
                if returncode != 0:
                    raise ClockRunError(
                        "loadp2 exited with code {} after DONE".format(
                            returncode
                        )
                    )
                return
        if self.events:
            raise ClockRunError("unexpected marker followed DONE")

    @staticmethod
    def _stop_session(session, known_returncode: Optional[int]) -> Optional[int]:
        if session is None:
            return known_returncode
        try:
            current = session.poll()
            if current is None:
                session.terminate()
                try:
                    current = session.wait(timeout=1.0)
                except Exception:
                    session.kill()
                    current = session.wait(timeout=1.0)
            return current if current is not None else known_returncode
        finally:
            session.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the locked P2 raw GETCT qualification",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--port")
    parser.add_argument("--image", type=pathlib.Path)
    parser.add_argument("--artifact-dir", type=pathlib.Path)
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--lock-timeout", type=float, default=0.0)
    return parser


def config_from_args(
    args,
    env: Mapping[str, str],
    utc_now: Callable[[], datetime.datetime],
    port_validator: Callable[[str], bool],
) -> ClockRunConfig:
    env_port = env.get("P2_PORT", "")
    if not env_port:
        raise SafetyError("P2_PORT must name the exact serial device")
    port = args.port or env_port
    if port != env_port:
        raise SafetyError("--port must exactly match P2_PORT")
    if not pathlib.Path(port).is_absolute():
        raise SafetyError("P2_PORT must be absolute")
    if not port_validator(port):
        raise SafetyError(
            "serial device is absent or not a character device: {}".format(port)
        )

    loadp2_text = env.get("LOADP2", "")
    if not loadp2_text:
        raise SafetyError("LOADP2 is unset")
    loadp2 = pathlib.Path(loadp2_text).expanduser().resolve()
    toolchain_lock = pathlib.Path(
        env.get("P2_TOOLCHAIN_LOCK", str(DEFAULT_TOOLCHAIN_LOCK))
    ).expanduser().resolve()
    try:
        loadp2_sha = hil.pinned_sha256(loadp2, toolchain_lock)
    except hil.SafetyError as exc:
        raise SafetyError(str(exc)) from exc

    image = (args.image or DEFAULT_IMAGE).expanduser().resolve()
    generated_config = REPO_ROOT / ".config"
    try:
        values = hil.read_kconfig(generated_config)
    except hil.SafetyError as exc:
        raise SafetyError(str(exc)) from exc
    validate_clock_config(values)

    reset_method = env.get("P2_RESET_METHOD", "loadp2")
    if reset_method in ("loadp2", "dtr"):
        reset_flag = "-DTR"
    elif reset_method == "rts":
        reset_flag = "-RTS"
    else:
        raise SafetyError(
            "clock runner requires loadp2, dtr, or rts reset method"
        )

    if args.artifact_dir is not None:
        artifact_dir = args.artifact_dir.expanduser().resolve()
    else:
        artifact_dir = DEFAULT_ARTIFACT_ROOT / artifact_stamp(utc_now())
    board_lock = pathlib.Path(
        env.get("P2_LOCK_FILE", str(monitor.DEFAULT_LOCK_FILE))
    ).expanduser().resolve()
    try:
        loader_baud = int(env.get("P2_LOADER_BAUD", "2000000"), 0)
        console_baud = int(env.get("P2_CONSOLE_BAUD", "230400"), 0)
    except ValueError as exc:
        raise SafetyError("P2 loader and console baud must be integers") from exc

    try:
        image_sha = sha256_file(image)
        config_sha = sha256_file(generated_config)
    except OSError as exc:
        raise SafetyError("cannot hash clock run input: {}".format(exc)) from exc
    config = ClockRunConfig(
        port=port,
        image=image,
        loadp2=loadp2,
        toolchain_lock=toolchain_lock,
        generated_config=generated_config,
        artifact_dir=artifact_dir,
        board_lock=board_lock,
        loader_baud=loader_baud,
        console_baud=console_baud,
        reset_flag=reset_flag,
        lock_timeout=args.lock_timeout,
        image_sha256=image_sha,
        loadp2_sha256=loadp2_sha,
        config_sha256=config_sha,
    )
    config.validate()
    return config


def default_build_runner() -> int:
    return hil.default_build_runner("clock")


def is_character_device(path: str) -> bool:
    try:
        return stat.S_ISCHR(os.stat(path).st_mode)
    except OSError:
        return False


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
    process_factory: Callable[[Sequence[str]], object] = hil.default_process_factory,
    monotonic: Callable[[], float] = time.monotonic,
    utc_now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(
        datetime.timezone.utc
    ),
    lock_factory: Callable[..., object] = monitor.BoardLock,
    owner_probe: Callable[[str], Tuple[int, ...]] = hil.default_owner_probe,
    build_runner: Callable[[], int] = default_build_runner,
    port_validator: Callable[[str], bool] = is_character_device,
) -> int:
    args = build_parser().parse_args(argv)
    environment = hil.local_environment(os.environ) if env is None else dict(env)
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
        if not args.no_build:
            build_rc = build_runner()
            if build_rc != 0:
                raise SafetyError(
                    "clock build failed with exit code {}".format(build_rc)
                )
        config = config_from_args(args, environment, utc_now, port_validator)
        runner = ClockRunner(
            config,
            process_factory=process_factory,
            monotonic=monotonic,
            utc_now=utc_now,
            lock_factory=lock_factory,
            owner_probe=owner_probe,
        )
        passed = runner.run()
        print("P2 clock artifact: {}".format(config.artifact_dir))
        return EXIT_OK if passed else EXIT_HIL_FAILURE
    except monitor.LockBusyError as exc:
        print("LOCK BUSY: {}".format(exc), file=sys.stderr)
        return EXIT_LOCK_BUSY
    except (SafetyError, hil.SafetyError, monitor.ConfigurationError) as exc:
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
