#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prove a previously written P2 image boots in user-confirmed SD-only mode.

This verifier never invokes a loader and never transmits a serial byte.  It
opens the console, applies the same DTR-only reset pulse used by the P2 HIL
tools, and requires the exact universal P2 early-boot markers followed by the
first exact NSH prompt.  The SD-only switch confirmation is deliberately a
required execution-time assertion because the switch positions cannot be
observed through the serial port.
"""

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import flashboot_protocol
import monitor
import reset


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "hil"
EXIT_OK = 0
EXIT_SAFETY = 2
EXIT_HIL_FAILED = 3
EXIT_INTERRUPTED = 130

UNIVERSAL_BOOT_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    flashboot_protocol.PREREQUISITE_BOOT_MARKER_PATTERNS[:4]
    + ((flashboot_protocol.PROMPT_LABEL, flashboot_protocol.PROMPT_PATTERN),)
)


class SafetyError(ValueError):
    """The reset-only proof is not explicitly authorized or well formed."""


@dataclass(frozen=True)
class WriteEvidence:
    path: pathlib.Path
    status_sha256: str
    port: str
    image_size: int
    image_sha256: str
    writer_sha256: str
    loadp2_sha256: str


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_error(exc: BaseException) -> str:
    return " ".join(str(exc).split()) or exc.__class__.__name__


def utc_timestamp(now: Optional[datetime.datetime] = None) -> str:
    value = now or datetime.datetime.now(datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def artifact_stamp(now: Optional[datetime.datetime] = None) -> str:
    value = now or datetime.datetime.now(datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ-sd-boot-verify"
    )


def write_json(path: pathlib.Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def load_write_evidence(path: pathlib.Path) -> WriteEvidence:
    root = path.expanduser().resolve()
    status_path = root / "status.json"
    if not status_path.is_file():
        raise SafetyError("SD write artifact has no status.json: {}".format(root))
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SafetyError("SD write status is unreadable: {}".format(exc)) from exc
    expected = {
        "action": "sd-boot-write",
        "status": "PASS",
        "boot_status": "UNVERIFIED",
        "output_filename": "_BOOT_P2.BIX",
        "fragmentation_verified": False,
    }
    for key, value in expected.items():
        if status.get(key) != value:
            raise SafetyError(
                "SD write status {} must be {!r}, got {!r}".format(
                    key, value, status.get(key)
                )
            )
    port = status.get("port")
    image_size = status.get("image_size")
    if not isinstance(port, str) or not port:
        raise SafetyError("SD write status has no serial port")
    if not isinstance(image_size, int) or image_size <= 0:
        raise SafetyError("SD write status has no positive image size")
    digests = {}
    for key in ("image_sha256", "writer_sha256", "loadp2_sha256"):
        value = status.get(key)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise SafetyError("SD write status has invalid {}".format(key))
        digests[key] = value
    return WriteEvidence(
        path=root,
        status_sha256=sha256_file(status_path),
        port=port,
        image_size=image_size,
        image_sha256=digests["image_sha256"],
        writer_sha256=digests["writer_sha256"],
        loadp2_sha256=digests["loadp2_sha256"],
    )


def marker_status(text: str) -> Dict[str, object]:
    found = []
    missing = []
    duplicates = []
    positions = []
    for label, pattern in UNIVERSAL_BOOT_PATTERNS:
        matches = list(pattern.finditer(text))
        if not matches:
            missing.append(label)
            continue
        found.append(label)
        positions.append(matches[0].start())
        if len(matches) != 1:
            duplicates.append(label)
    errors = []
    if positions != sorted(positions):
        errors.append("boot markers are out of order")
    rejection = flashboot_protocol.first_rejection(text)
    if rejection is not None:
        errors.append("{}: {}".format(rejection["kind"], rejection["line"]))
    loader_signatures = [
        label
        for label, pattern in flashboot_protocol.LOADER_SIGNATURE_PATTERNS
        if pattern.search(text) is not None
    ]
    if loader_signatures:
        errors.append(
            "loader signature appeared during reset-only proof: {}".format(
                ", ".join(loader_signatures)
            )
        )
    return {
        "complete": not missing and not duplicates and not errors,
        "found": found,
        "missing": missing,
        "duplicates": duplicates,
        "errors": errors,
        "order_valid": positions == sorted(positions),
        "loader_signatures": loader_signatures,
    }


def incomplete_reason(result: Mapping[str, object]) -> str:
    if result.get("errors"):
        return "boot protocol rejected: {}".format(
            "; ".join(result["errors"])
        )
    if result.get("duplicates"):
        return "boot protocol duplicated: {}".format(
            ", ".join(result["duplicates"])
        )
    return "boot protocol incomplete; missing {}".format(
        ", ".join(result.get("missing") or ["all markers"])
    )


def is_character_device(path: str) -> bool:
    try:
        return stat.S_ISCHR(os.stat(path).st_mode)
    except OSError:
        return False


def open_serial(port: str, baud: int, read_timeout: float):
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("pyserial is required for SD boot verification") from exc
    arguments = dict(
        port=port,
        baudrate=baud,
        timeout=read_timeout,
        write_timeout=1.0,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
        exclusive=True,
    )
    try:
        return serial.Serial(**arguments)
    except TypeError as exc:
        if "exclusive" not in str(exc):
            raise
        arguments.pop("exclusive")
        return serial.Serial(**arguments)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "reset-only proof of a prior _BOOT_P2.BIX write in SD-only mode"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-sd-only", action="store_true")
    parser.add_argument("--port", required=True)
    parser.add_argument("--image", required=True, type=pathlib.Path)
    parser.add_argument("--write-artifact", required=True, type=pathlib.Path)
    parser.add_argument("--artifact-dir", type=pathlib.Path)
    parser.add_argument("--console-baud", type=int, default=230400)
    parser.add_argument("--boot-timeout", type=float, default=30.0)
    parser.add_argument("--read-timeout", type=float, default=0.1)
    parser.add_argument("--lock-timeout", type=float, default=0.0)
    parser.add_argument("--board-lock", type=pathlib.Path)
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    environment: Optional[Mapping[str, str]] = None,
    serial_factory: Optional[Callable[..., object]] = None,
    lock_factory: Callable[..., object] = monitor.BoardLock,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    utc_now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(
        datetime.timezone.utc
    ),
) -> int:
    args = build_parser().parse_args(argv)
    env = os.environ if environment is None else environment
    try:
        evidence = load_write_evidence(args.write_artifact)
        image = args.image.expanduser().resolve()
        if not image.is_file() or image.stat().st_size <= 0:
            raise SafetyError("image is missing or empty: {}".format(image))
        if image.stat().st_size != evidence.image_size:
            raise SafetyError("image size does not match the SD write artifact")
        if sha256_file(image) != evidence.image_sha256:
            raise SafetyError("image SHA-256 does not match the SD write artifact")
        if args.port != evidence.port:
            raise SafetyError("serial port does not match the SD write artifact")
        if args.console_baud <= 0:
            raise SafetyError("console baud must be positive")
        if args.boot_timeout <= 0 or args.read_timeout <= 0:
            raise SafetyError("boot and read timeouts must be positive")
        if args.lock_timeout < 0:
            raise SafetyError("lock timeout cannot be negative")

        artifact_dir = (
            args.artifact_dir.expanduser().resolve()
            if args.artifact_dir is not None
            else (DEFAULT_ARTIFACT_ROOT / artifact_stamp()).resolve()
        )
        print("sd_write_artifact={}".format(evidence.path))
        print("sd_write_status_sha256={}".format(evidence.status_sha256))
        print("image={}".format(image))
        print("image_size={}".format(evidence.image_size))
        print("image_sha256={}".format(evidence.image_sha256))
        print("switches=FLASH:OFF,up:OFF,down:ON")
        print("loader_downloaded=false")
        print("serial_tx_bytes=0")

        if not args.execute:
            print(
                "DRY-RUN: write evidence and image validated; no serial open, "
                "DTR reset, loader download, or target write was performed"
            )
            print(
                "BOOT-UNVERIFIED: execution requires --confirm-sd-only after "
                "setting (FLASH,up,down)=(OFF,OFF,ON)"
            )
            return EXIT_OK
        if not args.confirm_sd_only:
            raise SafetyError(
                "--confirm-sd-only is required after physically setting "
                "(FLASH,up,down)=(OFF,OFF,ON)"
            )
        if env.get("P2_HIL", "0") != "1":
            raise SafetyError("P2_HIL=1 is required with --execute")
        if env.get("P2_ALLOW_RESET", "0") != "1":
            raise SafetyError("P2_ALLOW_RESET=1 is required with --execute")
        if serial_factory is None and not is_character_device(args.port):
            raise SafetyError(
                "serial character device is absent: {}".format(args.port)
            )
        if artifact_dir.exists():
            raise SafetyError("artifact directory already exists: {}".format(artifact_dir))

        board_lock = pathlib.Path(
            args.board_lock
            if args.board_lock is not None
            else env.get("P2_LOCK_FILE", monitor.DEFAULT_LOCK_FILE)
        ).expanduser().resolve()
        artifact_dir.parent.mkdir(parents=True, exist_ok=True)
        artifact_dir.mkdir()
        shutil.copy2(evidence.path / "status.json", artifact_dir / "write-status.json")

        started_utc = utc_timestamp(utc_now())
        status: Dict[str, object] = {
            "action": "sd-boot-verify",
            "status": "RUNNING",
            "boot_status": "UNVERIFIED",
            "boot_source": "SD_ONLY_USER_CONFIRMED",
            "switch_confirmation": {
                "FLASH": "OFF",
                "up": "OFF",
                "down": "ON",
            },
            "started_utc": started_utc,
            "ended_utc": None,
            "port": args.port,
            "console_baud": args.console_baud,
            "reset_method": "DTR",
            "reset_dwell_seconds": reset.DTR_DWELL_SECONDS,
            "loader_downloaded": False,
            "serial_tx_bytes": 0,
            "image": str(image),
            "image_size": evidence.image_size,
            "image_sha256": evidence.image_sha256,
            "sd_write_artifact": str(evidence.path),
            "sd_write_status_sha256": evidence.status_sha256,
            "writer_sha256": evidence.writer_sha256,
            "loadp2_sha256": evidence.loadp2_sha256,
            "fragmentation_verified": False,
            "reason": None,
        }
        write_json(artifact_dir / "status.json", status)
        write_json(artifact_dir / "markers.json", marker_status(""))

        connection = None
        boot_result: Dict[str, object] = marker_status("")
        raw = bytearray()
        reason = "reset-only SD boot did not complete"
        passed = False
        interrupted = False
        started = monotonic()
        try:
            with lock_factory(board_lock, timeout=args.lock_timeout):
                factory = serial_factory or open_serial
                if serial_factory is None:
                    connection = factory(args.port, args.console_baud, args.read_timeout)
                else:
                    connection = factory(
                        port=args.port,
                        baudrate=args.console_baud,
                        timeout=args.read_timeout,
                        write_timeout=1.0,
                        xonxoff=False,
                        rtscts=False,
                        dsrdtr=False,
                        exclusive=True,
                    )
                if hasattr(connection, "is_open") and not connection.is_open:
                    raise RuntimeError("serial factory returned a closed connection")
                reset.dtr_reset(connection, sleep=sleep)
                deadline = monotonic() + args.boot_timeout
                while monotonic() < deadline:
                    chunk = connection.read(4096)
                    if chunk is None:
                        raise RuntimeError("serial connection returned EOF")
                    if not isinstance(chunk, (bytes, bytearray)):
                        raise RuntimeError("serial read returned non-bytes")
                    if not chunk:
                        if hasattr(connection, "is_open") and not connection.is_open:
                            raise RuntimeError("serial connection disconnected")
                        continue
                    raw.extend(chunk)
                    boot_result = marker_status(
                        bytes(raw).decode("utf-8", errors="replace")
                    )
                    write_json(artifact_dir / "markers.json", boot_result)
                    if boot_result["errors"] or boot_result["duplicates"]:
                        reason = incomplete_reason(boot_result)
                        break
                    if boot_result["complete"]:
                        passed = True
                        reason = (
                            "user-confirmed SD-only reset reached the exact ordered "
                            "P2 boot markers and first NSH prompt with zero serial TX"
                        )
                        break
                if not passed and reason == "reset-only SD boot did not complete":
                    reason = incomplete_reason(boot_result)
        except Exception as exc:
            reason = safe_error(exc)
        except KeyboardInterrupt:
            interrupted = True
            reason = "interrupted"
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

        (artifact_dir / "console.raw").write_bytes(bytes(raw))
        text = bytes(raw).decode("utf-8", errors="replace")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        (artifact_dir / "console.log").write_text(normalized, encoding="utf-8")
        elapsed = max(0.0, monotonic() - started)
        status.update(
            {
                "status": "PASS" if passed else "FAIL",
                "boot_status": "PASS" if passed else "FAIL",
                "ended_utc": utc_timestamp(utc_now()),
                "elapsed_seconds": round(elapsed, 6),
                "raw_bytes": len(raw),
                "fragmentation_verified": passed,
                "interrupted": interrupted,
                "reason": reason,
            }
        )
        write_json(artifact_dir / "status.json", status)
        write_json(artifact_dir / "markers.json", boot_result)
        print("artifact_dir={}".format(artifact_dir))
        if interrupted:
            print("INTERRUPTED", file=sys.stderr)
            return EXIT_INTERRUPTED
        if not passed:
            print("HIL FAILED: {}".format(reason), file=sys.stderr)
            return EXIT_HIL_FAILED
        print("PASS: {}".format(reason))
        return EXIT_OK
    except SafetyError as exc:
        print("HIL REQUIRED: {}".format(safe_error(exc)), file=sys.stderr)
        return EXIT_SAFETY
    except KeyboardInterrupt:
        print("INTERRUPTED", file=sys.stderr)
        return EXIT_INTERRUPTED
    except (OSError, RuntimeError) as exc:
        print("HIL FAILED: {}".format(safe_error(exc)), file=sys.stderr)
        return EXIT_HIL_FAILED


if __name__ == "__main__":
    sys.exit(main())
