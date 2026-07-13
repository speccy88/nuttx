#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Strict host protocol for reset-only P2 flash-boot persistence HIL."""

import hashlib
import datetime
import json
import pathlib
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import storage_protocol
import build_artifact as build_evidence

sys.path.insert(0, str(pathlib.Path(__file__).with_name("lib")))
import flash_layout


RESET_CYCLES = 20
PROMPT_LABEL = "nsh> prompt"
PROMPT_PATTERN = re.compile(r"(?:^|[\r\n])nsh> (?:\x1b\[K)?", re.MULTILINE)
STARTUP_MOUNT_MARKER = (
    "P2FLASHBOOT:SMARTFS=/dev/smart0@/mnt/flash:MOUNTED:"
    "AUTOFORMAT=NO:DESTRUCTIVE_HANDLERS=ABSENT"
)
STARTUP_MOUNT_PATTERN = re.compile(
    r"^" + re.escape(STARTUP_MOUNT_MARKER) + r"\r?$", re.MULTILINE
)

PREREQUISITE_BOOT_MARKER_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    (
        "P2BOOT:ENTRY",
        re.compile(r"^P2BOOT:ENTRY\r?$", re.MULTILINE),
    ),
    (
        "P2BOOT:DATA=OK",
        re.compile(r"^P2BOOT:DATA=OK\r?$", re.MULTILINE),
    ),
    (
        "P2BOOT:BSS=OK",
        re.compile(r"^P2BOOT:BSS=OK\r?$", re.MULTILINE),
    ),
    (
        "P2BOOT:NX_START",
        re.compile(r"^P2BOOT:NX_START\r?$", re.MULTILINE),
    ),
) + storage_protocol.BOARD_MARKER_PATTERNS

BOOT_MARKER_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    PREREQUISITE_BOOT_MARKER_PATTERNS
    + ((STARTUP_MOUNT_MARKER, STARTUP_MOUNT_PATTERN),)
    + ((PROMPT_LABEL, PROMPT_PATTERN),)
)

REJECTION_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("panic", re.compile(r"\bPANIC\b", re.IGNORECASE)),
    ("assertion", re.compile(r"\bASSERT(?:ION)?\b", re.IGNORECASE)),
    ("error", re.compile(r"\bERROR\b", re.IGNORECASE)),
    ("failure", re.compile(r"\bFAIL(?:ED|URE)?\b", re.IGNORECASE)),
    ("stack overflow", re.compile(r"STACK\s+OVERFLOW", re.IGNORECASE)),
    ("unexpected IRQ", re.compile(r"UNEXPECTED\s+IRQ", re.IGNORECASE)),
    ("register dump", re.compile(r"REGISTER\s+DUMP", re.IGNORECASE)),
    ("watchdog reset", re.compile(r"WATCHDOG(?:\s+RESET)?", re.IGNORECASE)),
)

LOADER_SIGNATURE_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("loader Prop_Chk handshake", re.compile(r"\bProp_Chk\b")),
    ("loader Prop_Ver response", re.compile(r"\bProp_Ver\b")),
    ("loader name", re.compile(r"\bloadp2\b", re.IGNORECASE)),
    ("loader fast stage", re.compile(r"\bLoading\s+fast\s+loader\b", re.I)),
    (
        "loader byte transfer",
        re.compile(
            r"\b(?:Loading|Downloading)\s+[^\r\n]*?[0-9]+\s+bytes\b", re.I
        ),
    ),
    ("loader P2 probe", re.compile(r"\bFound\s+(?:a\s+)?P2\b", re.I)),
    (
        "loader P2 version",
        re.compile(r"\bP2\s+version\s+.\s+found\s+on\s+serial\s+port\b", re.I),
    ),
    (
        "loader checksum",
        re.compile(
            r"\bChecksum(?:ped)?(?:\s+\(0x[0-9A-Fa-f]+\))?\s+"
            r"(?:OK|ERROR|FAILED|VALIDATED)\b",
            re.I,
        ),
    ),
    (
        "ROM loader prompt",
        re.compile(r"(?:^|[\r\n])> ?(?:[\r\n]|$)", re.MULTILINE),
    ),
)

VERIFY_RECORD_PATTERN = re.compile(
    r"^P2STORAGE:FLASH:PERSISTENCE:"
    r"SEQUENCE=(?P<sequence>[0-9A-F]{8}):"
    r"BYTES=(?P<bytes>[0-9]+):"
    r"FNV1A=(?P<fnv1a>[0-9A-F]{8}):PASS\r?$",
    re.MULTILINE,
)


class FlashArtifactError(ValueError):
    """A prerequisite flash-write artifact is incomplete or untrustworthy."""


class ProgramArtifactError(ValueError):
    """The flash programming artifact is incomplete or crosses the boundary."""


def parse_utc_timestamp(value: str) -> datetime.datetime:
    """Parse the exact UTC timestamps used by incremental HIL artifacts."""

    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp must be an ISO-8601 UTC value ending in Z")
    try:
        parsed = datetime.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("timestamp is not valid ISO-8601 UTC") from exc
    if parsed.utcoffset() != datetime.timedelta(0):
        raise ValueError("timestamp is not UTC")
    return parsed


@dataclass(frozen=True)
class FlashArtifact:
    """Validated evidence needed by the reset-only persistence run."""

    path: pathlib.Path
    status_path: pathlib.Path
    status_sha256: str
    sequence: str
    boot_crc32: str
    image_sha256: str
    cycles: int
    port: str
    started_utc: str
    ended_utc: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "path": str(self.path),
            "status_path": str(self.status_path),
            "status_sha256": self.status_sha256,
            "sequence": self.sequence,
            "boot_crc32": self.boot_crc32,
            "image_sha256": self.image_sha256,
            "cycles": self.cycles,
            "port": self.port,
            "started_utc": self.started_utc,
            "ended_utc": self.ended_utc,
        }


@dataclass(frozen=True)
class ProgramArtifact:
    """Validated evidence that the intended raw image was programmed."""

    path: pathlib.Path
    status_path: pathlib.Path
    status_sha256: str
    image_sha256: str
    image_size: int
    program_end: int
    erase_end: int
    port: str
    manifest_sha256: str
    started_utc: str
    ended_utc: str
    build: build_evidence.BuildArtifact
    program_settle_seconds: int

    def as_dict(self) -> Dict[str, object]:
        return {
            "path": str(self.path),
            "status_path": str(self.status_path),
            "status_sha256": self.status_sha256,
            "image_sha256": self.image_sha256,
            "image_size": self.image_size,
            "program_end": self.program_end,
            "erase_end": self.erase_end,
            "port": self.port,
            "manifest_sha256": self.manifest_sha256,
            "started_utc": self.started_utc,
            "ended_utc": self.ended_utc,
            "build": self.build.as_dict(),
            "program_settle_seconds": self.program_settle_seconds,
        }


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: pathlib.Path) -> Dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FlashArtifactError("cannot read {}: {}".format(path, exc)) from exc
    if not isinstance(value, dict):
        raise FlashArtifactError("{} must contain a JSON object".format(path))
    return value


def _read_console(path: pathlib.Path) -> str:
    try:
        return path.read_bytes().decode("utf-8", errors="replace")
    except OSError as exc:
        raise FlashArtifactError("cannot read {}: {}".format(path, exc)) from exc


def load_program_artifact(path: pathlib.Path) -> ProgramArtifact:
    """Validate a PASS ``flash.sh`` artifact and its contained raw image."""

    requested = pathlib.Path(path).expanduser().resolve()
    if requested.name == "status.json":
        root = requested.parent
        status_path = requested
    else:
        root = requested
        status_path = root / "status.json"
    if not root.is_dir():
        raise ProgramArtifactError(
            "flash program artifact directory is absent: {}".format(root)
        )

    try:
        status = _read_json(status_path)
    except FlashArtifactError as exc:
        raise ProgramArtifactError(str(exc)) from exc
    for key, expected in (
        ("status", "PASS"),
        ("action", "flash-program"),
        ("exit_code", 0),
        ("boot_partition_range", "[0x00000000,0x00080000)"),
        ("flash_write_gate", True),
        ("flash_erase_gate", True),
        ("reset_gate", True),
        ("shared_sd_write_gate", True),
    ):
        if status.get(key) != expected:
            raise ProgramArtifactError(
                "flash program artifact {} must be {!r}, got {!r}".format(
                    key, expected, status.get(key)
                )
            )

    image_sha256 = status.get("image_sha256")
    image_size = status.get("image_size")
    port = status.get("port")
    started_utc = status.get("started_utc")
    ended_utc = status.get("ended_utc")
    if not isinstance(image_sha256, str) or re.fullmatch(
        r"[0-9a-f]{64}", image_sha256
    ) is None:
        raise ProgramArtifactError("programmed image SHA-256 is malformed")
    if not isinstance(image_size, int) or not 0 < image_size <= 0x7C000:
        raise ProgramArtifactError("programmed raw image size is out of range")
    if not isinstance(port, str) or not port:
        raise ProgramArtifactError("flash program artifact has no serial port")
    for label, value in (("started_utc", started_utc),
                         ("ended_utc", ended_utc)):
        try:
            parse_utc_timestamp(value)
        except ValueError as exc:
            raise ProgramArtifactError(
                "flash program artifact has no exact {}: {}".format(label, exc)
            ) from exc

    ranges: Dict[str, int] = {}
    for name in ("program", "erase"):
        value = status.get(name + "_range")
        match = re.fullmatch(
            r"\[0x00000000,0x(?P<end>[0-9a-f]{8})\)",
            value if isinstance(value, str) else "",
        )
        if match is None:
            raise ProgramArtifactError(
                "flash program artifact {} range is malformed".format(name)
            )
        ranges[name] = int(match.group("end"), 16)
    if not 0 < ranges["program"] <= ranges["erase"] <= 0x80000:
        raise ProgramArtifactError(
            "flash program or erase range crosses the data partition"
        )

    image = root / "inputs" / "flash-input.bin"
    if not image.is_file() or image.stat().st_size != image_size:
        raise ProgramArtifactError("preserved flash input size does not match")
    if _sha256(image) != image_sha256:
        raise ProgramArtifactError("preserved flash input SHA-256 does not match")
    manifest_path = root / "inputs" / "flash-input.bin.json"
    try:
        manifest = flash_layout.validate_image_manifest(image, manifest_path)
    except ValueError as exc:
        raise ProgramArtifactError(
            "preserved mkflash manifest is invalid: {}".format(exc)
        ) from exc
    manifest_sha256 = _sha256(manifest_path)
    for key, expected in (
        ("manifest_file", "inputs/flash-input.bin.json"),
        ("manifest_format", flash_layout.FLASH_INPUT_FORMAT),
        ("manifest_sha256", manifest_sha256),
    ):
        if status.get(key) != expected:
            raise ProgramArtifactError(
                "flash program artifact {} does not match preserved manifest".format(
                    key
                )
            )
    if (manifest["program_end"] != ranges["program"] or
            manifest["erase_end"] != ranges["erase"]):
        raise ProgramArtifactError(
            "flash program ranges do not match the mkflash manifest"
        )
    build_path = root / "inputs" / "build"
    try:
        build = build_evidence.load(build_path, image=image, require_clean=True)
    except build_evidence.BuildArtifactError as exc:
        raise ProgramArtifactError(
            "embedded flashboot build artifact is invalid: {}".format(exc)
        ) from exc
    for key, expected in (
        ("build_artifact_copy", "inputs/build"),
        ("build_status_sha256", build.status_sha256),
        ("build_profile", build.profile),
        ("build_nuttx_commit", build.nuttx_commit),
        ("build_apps_commit", build.apps_commit),
        ("board_clock_hz", build.board_clock_hz),
    ):
        if status.get(key) != expected:
            raise ProgramArtifactError(
                "flash program artifact {} does not match embedded build".format(
                    key
                )
            )
    if build.profile != "flashboot":
        raise ProgramArtifactError("embedded build profile is not flashboot")
    program_settle_seconds = status.get("program_settle_seconds")
    if (not isinstance(program_settle_seconds, int) or
            program_settle_seconds < 3):
        raise ProgramArtifactError(
            "flash program artifact has no safe programming settle interval"
        )
    loadp2 = status.get("loadp2")
    loadp2_sha256 = status.get("loadp2_sha256")
    loader_baud = status.get("loader_baud")
    if not isinstance(loadp2, str) or not pathlib.Path(loadp2).is_absolute():
        raise ProgramArtifactError("flash program loader path is malformed")
    if not isinstance(loadp2_sha256, str) or re.fullmatch(
        r"[0-9a-f]{64}", loadp2_sha256
    ) is None:
        raise ProgramArtifactError("flash program loader SHA-256 is malformed")
    if not isinstance(loader_baud, int) or loader_baud <= 0:
        raise ProgramArtifactError("flash program loader baud is malformed")
    if status.get("loadp2_copy") != "inputs/loadp2":
        raise ProgramArtifactError("flash program loader copy is not pinned")
    loader_copy = root / "inputs" / "loadp2"
    if not loader_copy.is_file() or _sha256(loader_copy) != loadp2_sha256:
        raise ProgramArtifactError("preserved loadp2 SHA-256 does not match")
    lock_path = root / "inputs" / "toolchain.lock"
    try:
        lock_text = lock_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ProgramArtifactError("preserved toolchain lock is absent") from exc
    lock_line = "sha256={}  {}".format(loadp2_sha256, loadp2)
    if lock_line not in lock_text.splitlines():
        raise ProgramArtifactError("preserved toolchain lock does not pin loadp2")

    if status.get("loader_command_file") != "command.json":
        raise ProgramArtifactError("flash loader command record is not JSON")
    command_path = root / "command.json"
    try:
        command = _read_json(command_path)
    except FlashArtifactError as exc:
        raise ProgramArtifactError("flash loader command is absent: {}".format(exc))
    expected_argv = [
        str(loader_copy.resolve()),
        "-p",
        port,
        "-l",
        str(loader_baud),
        "-DTR",
        "-SINGLE",
        "-FLASH",
        "-v",
        str(image.resolve()),
    ]
    if command.get("loader_baud") != loader_baud:
        raise ProgramArtifactError("flash loader command baud does not match status")
    if command.get("argv") != expected_argv:
        raise ProgramArtifactError("flash loader argv is not the exact sealed command")
    for log_name in ("loader.stdout", "loader.stderr", "layout.txt"):
        if not (root / log_name).is_file():
            raise ProgramArtifactError(
                "flash program artifact is missing {}".format(log_name)
            )

    return ProgramArtifact(
        path=root,
        status_path=status_path,
        status_sha256=_sha256(status_path),
        image_sha256=image_sha256,
        image_size=image_size,
        program_end=ranges["program"],
        erase_end=ranges["erase"],
        port=port,
        manifest_sha256=manifest_sha256,
        started_utc=started_utc,
        ended_utc=ended_utc,
        build=build,
        program_settle_seconds=program_settle_seconds,
    )


def load_flash_artifact(path: pathlib.Path) -> FlashArtifact:
    """Validate a completed destructive ``flash-write`` HIL artifact.

    The source console is allowed to contain loader chatter because the prior
    destructive run is RAM-loaded.  Its target-side write proof, one-MiB FNV,
    boot-partition CRC, cycle statuses, and top-level status must all agree.
    """

    requested = pathlib.Path(path).expanduser().resolve()
    if requested.name == "status.json":
        root = requested.parent
        status_path = requested
    else:
        root = requested
        status_path = root / "status.json"
    if not root.is_dir():
        raise FlashArtifactError("flash artifact directory is absent: {}".format(root))

    status = _read_json(status_path)
    required = {
        "status": "PASS",
        "protocol": "storage",
        "storage_action": "flash-write",
    }
    for key, expected in required.items():
        if status.get(key) != expected:
            raise FlashArtifactError(
                "flash artifact {} must be {!r}, got {!r}".format(
                    key, expected, status.get(key)
                )
            )

    try:
        sequence = storage_protocol.normalize_sequence(status["storage_sequence"])
    except (KeyError, ValueError) as exc:
        raise FlashArtifactError(
            "flash artifact has no exact storage sequence"
        ) from exc

    cycles = status.get("cycles_requested")
    passed = status.get("cycles_passed")
    if not isinstance(cycles, int) or cycles <= 0 or passed != cycles:
        raise FlashArtifactError(
            "flash artifact cycle completion is inconsistent: requested={!r} "
            "passed={!r}".format(cycles, passed)
        )
    image_sha256 = status.get("image_sha256")
    if not isinstance(image_sha256, str) or re.fullmatch(
        r"[0-9a-f]{64}", image_sha256
    ) is None:
        raise FlashArtifactError("flash artifact image_sha256 is missing or malformed")
    port = status.get("port")
    started_utc = status.get("started_utc")
    ended_utc = status.get("ended_utc")
    if not isinstance(port, str) or not port:
        raise FlashArtifactError("flash artifact has no serial port")
    for label, value in (("started_utc", started_utc),
                         ("ended_utc", ended_utc)):
        try:
            parse_utc_timestamp(value)
        except ValueError as exc:
            raise FlashArtifactError(
                "flash artifact has no exact {}: {}".format(label, exc)
            ) from exc

    expected_crc: Optional[str] = None
    for cycle in range(1, cycles + 1):
        cycle_dir = root / "cycle-{:03d}".format(cycle)
        cycle_status = _read_json(cycle_dir / "status.json")
        if cycle_status.get("status") != "PASS":
            raise FlashArtifactError(
                "flash artifact cycle {:03d} is not PASS".format(cycle)
            )
        console_path = cycle_dir / "console.raw"
        if not console_path.is_file():
            raise FlashArtifactError(
                "flash artifact cycle {:03d} has no console.raw".format(cycle)
            )
        text = _read_console(console_path)
        _found, missing, duplicates, positions = _marker_status(
            text, PREREQUISITE_BOOT_MARKER_PATTERNS
        )
        if missing or duplicates or positions != sorted(positions):
            raise FlashArtifactError(
                "flash artifact cycle {:03d} lacks the exact storage-profile "
                "boot contract: missing={!r} duplicates={!r} order_valid={}".format(
                    cycle,
                    missing,
                    duplicates,
                    positions == sorted(positions),
                )
            )
        if PROMPT_PATTERN.search(text) is None:
            raise FlashArtifactError(
                "flash artifact cycle {:03d} has no exact NSH prompt".format(cycle)
            )
        write_result = storage_protocol.parse_storage_response(
            text, "flash-write", sequence
        )
        if not write_result["complete"]:
            raise FlashArtifactError(
                "flash artifact cycle {:03d} lacks the exact one-MiB write proof: "
                "{}".format(cycle, storage_protocol.first_error(write_result))
            )
        crc_matches = list(storage_protocol.BOOT_CRC_PATTERN.finditer(text))
        if len(crc_matches) != 1:
            raise FlashArtifactError(
                "flash artifact cycle {:03d} must contain one boot CRC, got {}".format(
                    cycle, len(crc_matches)
                )
            )
        cycle_crc = crc_matches[0].group("boot_crc32")
        if expected_crc is None:
            expected_crc = cycle_crc
        elif cycle_crc != expected_crc:
            raise FlashArtifactError(
                "flash artifact boot CRC changed: {} then {}".format(
                    expected_crc, cycle_crc
                )
            )

    if expected_crc is None:
        raise FlashArtifactError("flash artifact did not establish a boot CRC")
    return FlashArtifact(
        path=root,
        status_path=status_path,
        status_sha256=_sha256(status_path),
        sequence=sequence,
        boot_crc32=expected_crc,
        image_sha256=image_sha256,
        cycles=cycles,
        port=port,
        started_utc=started_utc,
        ended_utc=ended_utc,
    )


def first_rejection(text: str) -> Optional[Dict[str, str]]:
    """Return the earliest fatal or loader signature found in console text."""

    candidates: List[Tuple[int, str, str]] = []
    for category, patterns in (
        ("fatal", REJECTION_PATTERNS),
        ("loader", LOADER_SIGNATURE_PATTERNS),
    ):
        for label, pattern in patterns:
            match = pattern.search(text)
            if match is not None:
                line = text[match.start() :].splitlines()[0].strip()
                candidates.append((match.start(), category + ": " + label, line))
    if "\x00" in text:
        candidates.append((text.index("\x00"), "serial: NUL byte", "NUL byte"))
    if "\ufffd" in text:
        candidates.append(
            (text.index("\ufffd"), "serial: invalid UTF-8", "invalid UTF-8")
        )
    if not candidates:
        return None
    offset, kind, line = min(candidates, key=lambda item: item[0])
    return {"kind": kind, "line": line, "offset": str(offset)}


def _marker_status(
    text: str, patterns: Sequence[Tuple[str, re.Pattern]]
) -> Tuple[List[str], List[str], List[str], List[int]]:
    found: List[str] = []
    missing: List[str] = []
    duplicates: List[str] = []
    positions: List[int] = []
    for label, pattern in patterns:
        matches = list(pattern.finditer(text))
        if not matches:
            missing.append(label)
            continue
        found.append(label)
        positions.append(matches[0].start())
        if len(matches) != 1:
            duplicates.append(label)
    return found, missing, duplicates, positions


def parse_boot(text: str, expected_crc32: Optional[str]) -> Dict[str, object]:
    """Validate one reset from ``P2BOOT:ENTRY`` through the first NSH prompt."""

    if expected_crc32 is not None and re.fullmatch(
        r"[0-9A-F]{8}", expected_crc32
    ) is None:
        raise ValueError("expected boot CRC must be eight uppercase hex digits")
    found, missing, duplicates, positions = _marker_status(
        text, BOOT_MARKER_PATTERNS
    )
    order_valid = positions == sorted(positions)
    errors: List[str] = []
    crc_matches = list(storage_protocol.BOOT_CRC_PATTERN.finditer(text))
    observed_crc = (
        crc_matches[0].group("boot_crc32") if len(crc_matches) == 1 else None
    )
    if (
        expected_crc32 is not None
        and observed_crc is not None
        and observed_crc != expected_crc32
    ):
        errors.append(
            "boot CRC mismatch: expected {}, got {}".format(
                expected_crc32, observed_crc
            )
        )
    malformed_crc = re.search(r"^P2STORAGE:W25_BOOT_CRC32=.*$", text, re.M)
    if malformed_crc is not None and observed_crc is None:
        errors.append("boot CRC marker is malformed or duplicated")
    if not order_valid:
        errors.append("boot markers are out of order")
    rejection = first_rejection(text)
    if rejection is not None:
        errors.append("{}: {}".format(rejection["kind"], rejection["line"]))
    return {
        "complete": not missing and not duplicates and not errors,
        "found": found,
        "missing": missing,
        "duplicates": duplicates,
        "errors": errors,
        "order_valid": order_valid,
        "boot_crc32": observed_crc,
        "expected_boot_crc32": expected_crc32,
        "rejection": rejection,
    }


def parse_verify_response(text: str, sequence: str) -> Dict[str, object]:
    """Validate the post-send read-only one-MiB flash verification response."""

    sequence = storage_protocol.normalize_sequence(sequence)
    storage = storage_protocol.parse_storage_response(
        text, "flash-verify", sequence
    )
    errors: List[str] = []
    rejection = first_rejection(text)
    if rejection is not None:
        errors.append("{}: {}".format(rejection["kind"], rejection["line"]))
    if re.search(r"^P2BOOT:", text, re.MULTILINE) is not None:
        errors.append("unexpected reset occurred after the verify command")

    record_matches = list(VERIFY_RECORD_PATTERN.finditer(text))
    expected_checksum = storage_protocol.stream_checksum("flash", sequence)
    if record_matches:
        record = record_matches[0]
        if len(record_matches) != 1:
            errors.append("flash persistence record is duplicated")
        if record.group("sequence") != sequence:
            errors.append("flash persistence sequence does not match the artifact")
        if int(record.group("bytes")) != storage_protocol.STREAM_SIZE:
            errors.append("flash persistence byte count is not exactly one MiB")
        if record.group("fnv1a") != expected_checksum:
            errors.append("flash persistence FNV does not match the host prediction")
    elif "P2STORAGE:FLASH:PERSISTENCE:" in text:
        errors.append("flash persistence record is malformed")

    prompts = list(PROMPT_PATTERN.finditer(text))
    if len(prompts) > 1:
        errors.append("post-command NSH prompt is duplicated")

    # NSH starts this builtin asynchronously and may redraw its prompt before
    # the first P2STORAGE marker.  There is no reliable prompt after the app's
    # final PASS line, so that exact target marker is the terminal condition.

    complete = bool(storage["complete"] and not errors)
    return {
        "complete": complete,
        "sequence": sequence,
        "expected_bytes": storage_protocol.STREAM_SIZE,
        "expected_fnv1a": expected_checksum,
        "storage_protocol": storage,
        "prompt_count": len(prompts),
        "errors": errors,
        "rejection": rejection,
    }


def first_incomplete_reason(result: Dict[str, object], phase: str) -> str:
    """Return a stable bounded-timeout or strict-protocol failure reason."""

    errors = result.get("errors") or []
    if errors:
        return "{} protocol rejected: {}".format(phase, "; ".join(errors))
    duplicates = result.get("duplicates") or []
    if duplicates:
        return "{} protocol duplicated: {}".format(phase, ", ".join(duplicates))
    if phase == "boot":
        missing = result.get("missing") or []
    else:
        storage = result.get("storage_protocol") or {}
        missing = list(storage.get("missing") or [])
    return "{} protocol incomplete; missing {}".format(
        phase, ", ".join(missing) if missing else "exact required markers"
    )
