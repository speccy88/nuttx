#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run twenty reset-only P2 boots from a previously proven flash image.

Execution retains one serial connection, performs no loader subprocess, and
transmits nothing until each reset reaches the exact NSH prompt.  The only
target command is the read-only one-MiB ``flash-verify`` for the nonce proven
by a prior PASS ``flash-write`` artifact.

The flashed target must be the ``p2-ec32mb:flashboot`` profile: every reset must
emit the exact P2BOOT and P2STORAGE board markers declared by
``flashboot_protocol.BOOT_MARKER_PATTERNS``, prove that its startup script
mounted the existing SmartFS without formatting, reach ``nsh> ``, and provide
the non-destructive ``p2storage`` NSH command.  The prerequisite is an
``hil.py --protocol storage --storage-action flash-write`` PASS artifact
captured before the intended flashboot image was programmed.  Its persistence
nonce and FNV are the data baseline.  Its boot CRC is pre-program evidence:
cycle one must establish a different flashboot CRC, and cycles two through
twenty must reproduce that new CRC exactly.

Dry-run contract check::

  python3 tools/p2/test-flashboot.py --port DEVICE \
    --flash-artifact artifacts/hil/PRIOR-storage \
    --program-artifact artifacts/hil/FLASH-program

Execute the fixed reset campaign::

  P2_HIL=1 P2_ALLOW_RESET=1 \
    python3 tools/p2/test-flashboot.py --execute --port DEVICE \
    --flash-artifact artifacts/hil/PRIOR-storage \
    --program-artifact artifacts/hil/FLASH-program
"""

import argparse
import codecs
import datetime
import json
import os
import pathlib
import shutil
import stat
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, Mapping, Optional, TextIO

import flashboot_protocol
import monitor
import reset
import storage_protocol


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "hil"

EXIT_OK = 0
EXIT_HIL_FAILED = 1
EXIT_SAFETY = 2
EXIT_INTERRUPTED = 130


class SafetyError(ValueError):
    """An explicit HIL gate or immutable run constraint is not satisfied."""


@dataclass(frozen=True)
class FlashBootConfig:
    port: str
    artifact_dir: pathlib.Path
    flash_artifact: flashboot_protocol.FlashArtifact
    program_artifact: flashboot_protocol.ProgramArtifact
    console_baud: int = 230400
    boot_timeout: float = 30.0
    verify_timeout: float = 120.0
    read_timeout: float = 0.1
    write_timeout: float = 1.0
    read_size: int = 4096
    board_lock: pathlib.Path = monitor.DEFAULT_LOCK_FILE
    lock_timeout: float = 0.0

    def validate(self) -> None:
        if not self.port:
            raise SafetyError("an explicit serial port is required")
        if self.program_artifact.port != self.port:
            raise SafetyError(
                "program artifact port {} does not match {}".format(
                    self.program_artifact.port, self.port
                )
            )
        if self.flash_artifact.port != self.port:
            raise SafetyError(
                "flash-write artifact port {} does not match {}".format(
                    self.flash_artifact.port, self.port
                )
            )
        source_started = flashboot_protocol.parse_utc_timestamp(
            self.flash_artifact.started_utc
        )
        source_ended = flashboot_protocol.parse_utc_timestamp(
            self.flash_artifact.ended_utc
        )
        program_started = flashboot_protocol.parse_utc_timestamp(
            self.program_artifact.started_utc
        )
        program_ended = flashboot_protocol.parse_utc_timestamp(
            self.program_artifact.ended_utc
        )
        build_started = flashboot_protocol.parse_utc_timestamp(
            self.program_artifact.build.started_utc
        )
        build_ended = flashboot_protocol.parse_utc_timestamp(
            self.program_artifact.build.ended_utc
        )
        if source_ended < source_started:
            raise SafetyError("flash-write artifact timestamps are reversed")
        if program_ended < program_started:
            raise SafetyError("flash-program artifact timestamps are reversed")
        if build_ended < build_started:
            raise SafetyError("flashboot build artifact timestamps are reversed")
        if build_ended > program_started:
            raise SafetyError("flash program predates completion of its build")
        if program_started < source_ended:
            raise SafetyError(
                "flash program did not start after the prerequisite flash write"
            )
        if self.program_artifact.build.board_clock_hz != 180000000:
            raise SafetyError("flashboot build board clock is not 180 MHz")
        if self.console_baud <= 0:
            raise SafetyError("console baud must be greater than zero")
        if self.boot_timeout <= 0 or self.verify_timeout <= 0:
            raise SafetyError("boot and verify timeouts must be greater than zero")
        if self.read_timeout <= 0 or self.write_timeout <= 0:
            raise SafetyError("serial timeouts must be greater than zero")
        if self.read_size <= 0:
            raise SafetyError("read size must be greater than zero")
        if self.lock_timeout < 0:
            raise SafetyError("board lock timeout cannot be negative")
        if self.artifact_dir.exists():
            raise SafetyError(
                "flash-boot artifact directory already exists: {}".format(
                    self.artifact_dir
                )
            )
        for label, prerequisite in (
            ("flash-write", self.flash_artifact.path),
            ("flash-program", self.program_artifact.path),
            ("flashboot build", self.program_artifact.build.path),
        ):
            try:
                self.artifact_dir.resolve().relative_to(prerequisite.resolve())
            except ValueError:
                continue
            raise SafetyError(
                "output artifact cannot be inside the {} artifact".format(label)
            )


@dataclass(frozen=True)
class CycleResult:
    passed: bool
    reason: str
    elapsed_seconds: float
    raw_bytes: int
    boot_crc32: Optional[str]


class NormalizedLog:
    """Incrementally decode UTF-8 and canonicalize CR/LF in an artifact log."""

    def __init__(self, output: TextIO) -> None:
        self.output = output
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.pending_cr = False

    def feed(self, data: bytes) -> str:
        decoded = self.decoder.decode(data, final=False)
        self._write(decoded, final=False)
        return decoded

    def finish(self) -> str:
        decoded = self.decoder.decode(b"", final=True)
        self._write(decoded, final=True)
        return decoded

    def _write(self, text: str, final: bool) -> None:
        if self.pending_cr:
            if text.startswith("\n"):
                text = text[1:]
            self.output.write("\n")
            self.pending_cr = False
        if text.endswith("\r") and not final:
            text = text[:-1]
            self.pending_cr = True
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        self.output.write(text)
        if final and self.pending_cr:
            self.output.write("\n")
            self.pending_cr = False
        self.output.flush()


def utc_timestamp(now: Optional[datetime.datetime] = None) -> str:
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return (
        now.astimezone(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def artifact_stamp(now: Optional[datetime.datetime] = None) -> str:
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return now.astimezone(datetime.timezone.utc).strftime(
        "%Y%m%dT%H%M%S.%fZ-flashboot"
    )


def write_json(path: pathlib.Path, value: object) -> None:
    """Atomically checkpoint one JSON artifact."""

    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def default_serial_factory(**arguments):
    try:
        import serial
    except ImportError as exc:
        raise SafetyError("pyserial is required for --execute") from exc
    return serial.Serial(**arguments)


class FlashBootRunner:
    """One-connection, fixed-twenty-cycle reset and persistence runner."""

    def __init__(
        self,
        config: FlashBootConfig,
        serial_factory: Callable[..., object] = default_serial_factory,
        lock_factory: Callable[..., object] = monitor.BoardLock,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(
            datetime.timezone.utc
        ),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.serial_factory = serial_factory
        self.lock_factory = lock_factory
        self.monotonic = monotonic
        self.utc_now = utc_now
        self.sleep = sleep
        self.last_reason = "run did not start"

    def run(self) -> bool:
        config = self.config
        config.validate()
        config.artifact_dir.mkdir(parents=True, exist_ok=False)
        started = self.utc_now()
        overall: Dict[str, object] = {
            "status": "RUNNING",
            "started_utc": utc_timestamp(started),
            "cycles_requested": flashboot_protocol.RESET_CYCLES,
            "cycles_passed": 0,
            "protocol": "flashboot-reset-only",
            "port": config.port,
            "console_baud": config.console_baud,
            "boot_timeout_seconds": config.boot_timeout,
            "verify_timeout_seconds": config.verify_timeout,
            "board_lock": str(config.board_lock),
            "serial_connections_requested": 1,
            "reset_method": "DTR",
            "pre_prompt_tx_policy": "zero bytes",
            "target_command": storage_protocol.command_line(
                "flash-verify", config.flash_artifact.sequence
            ),
            "target_command_is_destructive": False,
            "flash_verify_bytes": storage_protocol.STREAM_SIZE,
            "flash_verify_fnv1a": storage_protocol.stream_checksum(
                "flash", config.flash_artifact.sequence
            ),
            "pre_program_boot_crc32": config.flash_artifact.boot_crc32,
            "source_flash_artifact": config.flash_artifact.as_dict(),
            "flash_program_artifact": config.program_artifact.as_dict(),
        }
        self._checkpoint_root(overall)

        passed = 0
        connection = None
        reason = "serial connection did not open"
        interrupted = False
        interrupt_error = None
        close_error = None
        try:
            overall["prerequisite_evidence"] = self._preserve_prerequisites()
            self._checkpoint_root(overall)
            with self.lock_factory(
                config.board_lock,
                timeout=config.lock_timeout,
                monotonic=self.monotonic,
            ):
                connection = self._open_serial()
                expected_crc32 = None
                for cycle in range(1, flashboot_protocol.RESET_CYCLES + 1):
                    result = self._run_cycle(
                        connection, cycle, expected_crc32
                    )
                    reason = result.reason
                    if not result.passed:
                        break
                    if expected_crc32 is None:
                        expected_crc32 = result.boot_crc32
                        overall["flashboot_crc32"] = expected_crc32
                    passed += 1
                    overall["cycles_passed"] = passed
                    overall["last_completed_cycle"] = cycle
                    overall["last_cycle_ended_utc"] = utc_timestamp(self.utc_now())
                    self._checkpoint_root(overall)
        except KeyboardInterrupt as exc:
            interrupted = True
            interrupt_error = exc
            reason = "interrupted"
        except Exception as exc:  # Preserve root evidence for all HIL I/O failures.
            reason = "flash-boot run failed: {}".format(_safe_error(exc))
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception as exc:
                    close_error = "serial close failed: {}".format(
                        _safe_error(exc)
                    )
                    reason = close_error

            overall["cycles_passed"] = passed
            overall["serial_connections_opened"] = 1 if connection is not None else 0
            overall["serial_close_error"] = close_error
            overall["ended_utc"] = utc_timestamp(self.utc_now())
            complete = (
                passed == flashboot_protocol.RESET_CYCLES
                and isinstance(overall.get("flashboot_crc32"), str)
                and close_error is None
                and not interrupted
            )
            overall["status"] = "PASS" if complete else "FAIL"
            overall["failure_reason"] = None if complete else reason
            overall["interrupted"] = interrupted
            self.last_reason = (
                "all 20 reset-only flash boots passed" if complete else reason
            )
            self._checkpoint_root(overall)
        if interrupt_error is not None:
            raise interrupt_error
        return complete

    def _preserve_prerequisites(self) -> Dict[str, object]:
        """Copy every consumed prerequisite file and hash the sealed copies."""

        config = self.config
        evidence_root = config.artifact_dir / "prerequisites"
        files = []

        def preserve(source: pathlib.Path, relative: pathlib.Path) -> None:
            destination = evidence_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            files.append(
                {
                    "source": str(source),
                    "copy": str(pathlib.Path("prerequisites") / relative),
                    "size": destination.stat().st_size,
                    "sha256": _sha256_file(destination),
                }
            )

        flash = config.flash_artifact
        preserve(flash.status_path, pathlib.Path("flash-write/status.json"))
        for cycle in range(1, flash.cycles + 1):
            cycle_name = "cycle-{:03d}".format(cycle)
            cycle_root = flash.path / cycle_name
            preserve(
                cycle_root / "status.json",
                pathlib.Path("flash-write") / cycle_name / "status.json",
            )
            preserve(
                cycle_root / "console.raw",
                pathlib.Path("flash-write") / cycle_name / "console.raw",
            )

        program = config.program_artifact
        for relative in (
            pathlib.Path("status.json"),
            pathlib.Path("command.txt"),
            pathlib.Path("command.json"),
            pathlib.Path("layout.txt"),
            pathlib.Path("loader.stdout"),
            pathlib.Path("loader.stderr"),
            pathlib.Path("inputs/flash-input.bin"),
            pathlib.Path("inputs/flash-input.bin.json"),
            pathlib.Path("inputs/loadp2"),
            pathlib.Path("inputs/toolchain.lock"),
        ):
            preserve(
                program.path / relative,
                pathlib.Path("flash-program") / relative,
            )
        for source in sorted(program.build.path.rglob("*")):
            if source.is_file():
                relative = source.relative_to(program.build.path)
                preserve(
                    source,
                    pathlib.Path("flash-program/inputs/build") / relative,
                )

        manifest = {
            "format": "p2-flashboot-prerequisites-v1",
            "files": files,
        }
        write_json(evidence_root / "manifest.json", manifest)
        manifest["manifest_sha256"] = _sha256_file(
            evidence_root / "manifest.json"
        )
        return manifest

    def _checkpoint_root(self, overall: Dict[str, object]) -> None:
        write_json(self.config.artifact_dir / "metadata.json", overall)
        write_json(self.config.artifact_dir / "status.json", overall)

    def _open_serial(self):
        arguments = dict(
            port=self.config.port,
            baudrate=self.config.console_baud,
            timeout=self.config.read_timeout,
            write_timeout=self.config.write_timeout,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
            exclusive=True,
        )
        try:
            connection = self.serial_factory(**arguments)
        except TypeError as exc:
            if "exclusive" not in str(exc):
                raise
            arguments.pop("exclusive")
            connection = self.serial_factory(**arguments)
        if hasattr(connection, "is_open") and not connection.is_open:
            raise RuntimeError("serial factory returned a closed connection")
        return connection

    def _run_cycle(
        self,
        connection: object,
        cycle: int,
        expected_crc32: Optional[str],
    ) -> CycleResult:
        config = self.config
        cycle_dir = config.artifact_dir / "cycle-{:03d}".format(cycle)
        cycle_dir.mkdir(parents=False, exist_ok=False)
        started_utc = self.utc_now()
        started = self.monotonic()
        command = storage_protocol.command_bytes(
            "flash-verify", config.flash_artifact.sequence
        )
        metadata: Dict[str, object] = {
            "status": "RUNNING",
            "cycle": cycle,
            "started_utc": utc_timestamp(started_utc),
            "connection_index": 1,
            "reset_method": "DTR",
            "reset_dwell_seconds": reset.DTR_DWELL_SECONDS,
            "pre_prompt_tx_bytes": 0,
            "verify_command_ascii": command.decode("ascii").rstrip("\r"),
            "verify_command_bytes": len(command),
            "expected_boot_crc32": expected_crc32,
            "pre_program_boot_crc32": config.flash_artifact.boot_crc32,
            "expected_flash_bytes": storage_protocol.STREAM_SIZE,
            "expected_flash_fnv1a": storage_protocol.stream_checksum(
                "flash", config.flash_artifact.sequence
            ),
        }
        write_json(cycle_dir / "metadata.json", metadata)
        write_json(cycle_dir / "status.json", metadata)

        marker_status: Dict[str, object] = {
            "boot": None,
            "flash_verify": None,
            "pre_prompt_tx_bytes": 0,
            "verify_command_sent": False,
        }
        write_json(cycle_dir / "markers.json", marker_status)

        boot_buffer = bytearray()
        verify_buffer = bytearray()
        raw_bytes = 0
        passed = False
        reason = "DTR reset did not complete"
        prompt_elapsed = None
        send_elapsed = None
        verify_elapsed = None
        pending_base_exception = None

        with (cycle_dir / "console.raw").open("wb") as raw_log, (
            cycle_dir / "console.log"
        ).open("w", encoding="utf-8", newline="\n") as normalized_file:
            normalized = NormalizedLog(normalized_file)

            def capture(chunk: bytes) -> None:
                nonlocal raw_bytes
                raw_log.write(chunk)
                raw_log.flush()
                normalized.feed(chunk)
                raw_bytes += len(chunk)

            try:
                reset.dtr_reset(connection, sleep=self.sleep)
                metadata["reset_completed_utc"] = utc_timestamp(self.utc_now())
                write_json(cycle_dir / "metadata.json", metadata)

                boot_deadline = self.monotonic() + config.boot_timeout
                boot_result: Dict[str, object] = {}
                while True:
                    if self.monotonic() >= boot_deadline:
                        reason = flashboot_protocol.first_incomplete_reason(
                            boot_result, "boot"
                        )
                        break
                    chunk = self._read(connection)
                    if not chunk:
                        continue
                    capture(chunk)
                    boot_buffer.extend(chunk)
                    boot_text = bytes(boot_buffer).decode("utf-8", errors="replace")
                    boot_result = flashboot_protocol.parse_boot(
                        boot_text, expected_crc32
                    )
                    marker_status["boot"] = boot_result
                    write_json(cycle_dir / "markers.json", marker_status)
                    if boot_result["errors"] or boot_result["duplicates"]:
                        reason = flashboot_protocol.first_incomplete_reason(
                            boot_result, "boot"
                        )
                        break
                    if boot_result["complete"]:
                        prompt_elapsed = max(0.0, self.monotonic() - started)
                        break
                if not boot_result.get("complete"):
                    raise RuntimeError(reason)
                if (expected_crc32 is None and
                        boot_result.get("boot_crc32") ==
                        config.flash_artifact.boot_crc32):
                    raise RuntimeError(
                        "cycle 1 boot CRC did not change after flash programming"
                    )

                # This is the sole transmit point.  It is unreachable until the
                # exact ordered boot protocol and first NSH prompt are complete.
                written = connection.write(command)
                if written is not None and written != len(command):
                    raise RuntimeError(
                        "short verify command write: {} of {} bytes".format(
                            written, len(command)
                        )
                    )
                connection.flush()
                send_elapsed = max(0.0, self.monotonic() - started)
                marker_status["verify_command_sent"] = True
                marker_status["verify_command_bytes"] = len(command)
                marker_status["send_after_prompt"] = send_elapsed >= prompt_elapsed
                write_json(cycle_dir / "markers.json", marker_status)

                verify_deadline = self.monotonic() + config.verify_timeout
                verify_result: Dict[str, object] = {}
                while True:
                    if self.monotonic() >= verify_deadline:
                        reason = flashboot_protocol.first_incomplete_reason(
                            verify_result, "flash verify"
                        )
                        break
                    chunk = self._read(connection)
                    if not chunk:
                        continue
                    capture(chunk)
                    verify_buffer.extend(chunk)
                    verify_text = bytes(verify_buffer).decode(
                        "utf-8", errors="replace"
                    )
                    verify_result = flashboot_protocol.parse_verify_response(
                        verify_text, config.flash_artifact.sequence
                    )
                    marker_status["flash_verify"] = verify_result
                    write_json(cycle_dir / "markers.json", marker_status)
                    if verify_result["errors"]:
                        reason = flashboot_protocol.first_incomplete_reason(
                            verify_result, "flash verify"
                        )
                        break
                    if verify_result["complete"]:
                        verify_elapsed = max(0.0, self.monotonic() - started)
                        passed = True
                        reason = (
                            "flash-boot CRC was stable and the exact one-MiB "
                            "flash FNV matched the prerequisite artifact"
                        )
                        break
            except Exception as exc:
                reason = _safe_error(exc)
            except BaseException as exc:
                pending_base_exception = exc
                reason = _safe_error(exc)
            finally:
                normalized.finish()

        elapsed = max(0.0, self.monotonic() - started)
        metadata.update(
            {
                "status": "PASS" if passed else "FAIL",
                "ended_utc": utc_timestamp(self.utc_now()),
                "elapsed_seconds": round(elapsed, 6),
                "prompt_elapsed_seconds": (
                    round(prompt_elapsed, 6) if prompt_elapsed is not None else None
                ),
                "send_elapsed_seconds": (
                    round(send_elapsed, 6) if send_elapsed is not None else None
                ),
                "verify_elapsed_seconds": (
                    round(verify_elapsed, 6) if verify_elapsed is not None else None
                ),
                "pre_prompt_tx_bytes": 0,
                "raw_bytes": raw_bytes,
                "reason": reason,
                "interrupted": isinstance(
                    pending_base_exception, KeyboardInterrupt
                ),
            }
        )
        marker_status["pre_prompt_tx_bytes"] = 0
        marker_status["send_after_prompt"] = bool(
            prompt_elapsed is not None
            and send_elapsed is not None
            and send_elapsed >= prompt_elapsed
        )
        write_json(cycle_dir / "markers.json", marker_status)
        write_json(cycle_dir / "metadata.json", metadata)
        write_json(cycle_dir / "status.json", metadata)
        if pending_base_exception is not None:
            raise pending_base_exception
        boot_crc32 = None
        if "boot_result" in locals():
            observed = boot_result.get("boot_crc32")
            if isinstance(observed, str):
                boot_crc32 = observed
        return CycleResult(passed, reason, elapsed, raw_bytes, boot_crc32)

    def _read(self, connection: object) -> bytes:
        chunk = connection.read(self.config.read_size)
        if chunk is None:
            raise RuntimeError("serial connection returned EOF")
        if not isinstance(chunk, (bytes, bytearray)):
            raise RuntimeError("serial read returned non-bytes")
        if not chunk and hasattr(connection, "is_open") and not connection.is_open:
            raise RuntimeError("serial connection disconnected")
        return bytes(chunk)


def _safe_error(exc: BaseException) -> str:
    return " ".join(str(exc).split()) or exc.__class__.__name__


def _sha256_file(path: pathlib.Path) -> str:
    import hashlib

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "verify twenty reset-only P2 flash boots using one persistent "
            "serial connection"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--port", required=True, help="explicit P2 serial device")
    parser.add_argument(
        "--flash-artifact",
        required=True,
        type=pathlib.Path,
        help="prior PASS storage/flash-write HIL artifact",
    )
    parser.add_argument(
        "--program-artifact",
        required=True,
        type=pathlib.Path,
        help="PASS flash.sh artifact for the currently programmed image",
    )
    parser.add_argument(
        "--artifact-dir",
        type=pathlib.Path,
        help="new incremental output directory (execute only)",
    )
    parser.add_argument("--console-baud", type=int, default=230400)
    parser.add_argument("--boot-timeout", type=float, default=30.0)
    parser.add_argument("--verify-timeout", type=float, default=120.0)
    parser.add_argument("--read-timeout", type=float, default=0.1)
    parser.add_argument("--lock-timeout", type=float, default=0.0)
    parser.add_argument(
        "--board-lock", type=pathlib.Path
    )
    return parser


def main(
    argv=None,
    environment: Optional[Mapping[str, str]] = None,
    serial_factory: Optional[Callable[..., object]] = None,
    lock_factory: Callable[..., object] = monitor.BoardLock,
) -> int:
    args = build_parser().parse_args(argv)
    env = os.environ if environment is None else environment
    try:
        source = flashboot_protocol.load_flash_artifact(args.flash_artifact)
        programmed = flashboot_protocol.load_program_artifact(
            args.program_artifact
        )
        expected_fnv = storage_protocol.stream_checksum("flash", source.sequence)
        print("flash_artifact={}".format(source.path))
        print("flash_artifact_status_sha256={}".format(source.status_sha256))
        print("flash_sequence={}".format(source.sequence))
        print("pre_program_boot_crc32={}".format(source.boot_crc32))
        print("program_image_sha256={}".format(programmed.image_sha256))
        print("program_erase_end=0x{:08X}".format(programmed.erase_end))
        print("program_settle_seconds={}".format(
            programmed.program_settle_seconds
        ))
        print("build_status_sha256={}".format(programmed.build.status_sha256))
        print("build_nuttx_commit={}".format(programmed.build.nuttx_commit))
        print("build_apps_commit={}".format(programmed.build.apps_commit))
        print("build_clock_hz={}".format(programmed.build.board_clock_hz))
        print("flash_verify_bytes={}".format(storage_protocol.STREAM_SIZE))
        print("flash_verify_fnv1a={}".format(expected_fnv))
        print("reset_cycles={}".format(flashboot_protocol.RESET_CYCLES))
        print("serial_connections=1")
        print("pre_prompt_tx_bytes=0")

        artifact_dir = (
            args.artifact_dir.expanduser().resolve()
            if args.artifact_dir is not None
            else (DEFAULT_ARTIFACT_ROOT / artifact_stamp()).resolve()
        )
        config = FlashBootConfig(
            port=args.port,
            artifact_dir=artifact_dir,
            flash_artifact=source,
            program_artifact=programmed,
            console_baud=args.console_baud,
            boot_timeout=args.boot_timeout,
            verify_timeout=args.verify_timeout,
            read_timeout=args.read_timeout,
            board_lock=pathlib.Path(
                args.board_lock
                if args.board_lock is not None
                else env.get("P2_LOCK_FILE", monitor.DEFAULT_LOCK_FILE)
            ).expanduser().resolve(),
            lock_timeout=args.lock_timeout,
        )
        config.validate()

        if not args.execute:
            print(
                "DRY-RUN: prior PASS artifact validated; no serial open, DTR "
                "reset, target command, flash write, or SD access was performed"
            )
            return EXIT_OK
        if env.get("P2_HIL", "0") != "1":
            raise SafetyError("P2_HIL=1 is required with --execute")
        if env.get("P2_ALLOW_RESET", "0") != "1":
            raise SafetyError("P2_ALLOW_RESET=1 is required with --execute")
        if not is_character_device(args.port):
            raise SafetyError("serial character device is absent: {}".format(args.port))

        runner_arguments = {"lock_factory": lock_factory}
        if serial_factory is not None:
            runner_arguments["serial_factory"] = serial_factory
        runner = FlashBootRunner(config, **runner_arguments)
        passed = runner.run()
        print("artifact_dir={}".format(artifact_dir))
        if not passed:
            print("HIL FAILED: {}".format(runner.last_reason), file=sys.stderr)
            return EXIT_HIL_FAILED
        print("PASS: {}".format(runner.last_reason))
        return EXIT_OK
    except (
        SafetyError,
        flashboot_protocol.FlashArtifactError,
        flashboot_protocol.ProgramArtifactError,
    ) as exc:
        print("HIL REQUIRED: {}".format(_safe_error(exc)), file=sys.stderr)
        return EXIT_SAFETY
    except (OSError, RuntimeError) as exc:
        print("HIL FAILED: {}".format(_safe_error(exc)), file=sys.stderr)
        return EXIT_HIL_FAILED
    except KeyboardInterrupt:
        print("INTERRUPTED", file=sys.stderr)
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())
