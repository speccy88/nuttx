#!/usr/bin/env python3
"""Strict console protocol for P2 flash and microSD HIL.

Destructive target commands are intentionally authenticated only by an exact,
long data-loss acknowledgement token.  The host safety gates are independent
and must also pass before :mod:`hil` opens the serial port.  A per-run 32-bit
sequence is used as a nonce and is embedded in the deterministic on-media
record, its checksum markers, and the reset-persistence verification.
"""

import functools
import re
from typing import Dict, List, Optional, Tuple

ACKNOWLEDGEMENT = "P2STORAGE-I-ACCEPT-DATA-LOSS-V1"
FLASH_DEVICE = "/dev/smart0"
FLASH_MOUNT = "/mnt/flash"
SD_DEVICE = "/dev/mmcsd0"
SD_MOUNT = "/mnt/sd"
SUPPORTED_FLASH_JEDECS = ("EF4018", "EF5018", "EF6018", "EF7018")
ALTERNATE_TRANSACTIONS = 1000
FLASH_CYCLE_COUNT = 16
SD_STRESS_COUNT = 64
STREAM_SIZE = 1048576
BOOT_CRC_PATTERN = re.compile(
    r"^P2STORAGE:W25_BOOT_CRC32=(?P<boot_crc32>[0-9A-F]{8})\r?$",
    re.MULTILINE,
)

ACTIONS = (
    "probe",
    "sd-rom-verify",
    "sd-mbr-repair",
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
)
SEQUENCE_ACTIONS = frozenset(
    (
        "flash-write",
        "flash-verify",
        "flash-cycle",
        "flash-full",
        "flash-interrupt-arm",
        "flash-interrupt-verify",
        "sd-write",
        "sd-verify",
        "sd-rename-delete",
        "sd-stress",
        "alternate",
    )
)
TARGET_DESTRUCTIVE_ACTIONS = frozenset(
    (
        "flash-format",
        "flash-write",
        "flash-cycle",
        "flash-full",
        "flash-interrupt-arm",
        "sd-format",
        "sd-mbr-repair",
        "sd-write",
        "sd-rename-delete",
        "sd-stress",
        "alternate",
    )
)
FLASH_WRITABLE_ACTIONS = frozenset(
    (
        "flash-format",
        "flash-write",
        "flash-cycle",
        "flash-full",
        "flash-interrupt-arm",
        "alternate",
    )
)
SD_DESTRUCTIVE_ACTIONS = frozenset(
    (
        "sd-format",
        "sd-mbr-repair",
        "sd-write",
        "sd-rename-delete",
        "sd-stress",
        "alternate",
    )
)

BOARD_MARKER_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    (
        "P2STORAGE:W25=PRIVATE JEDEC=SUPPORTED",
        re.compile(
            r"^P2STORAGE:W25=PRIVATE JEDEC=" r"(?P<w25_jedec>EF(?:40|50|60|70)18)\r?$",
            re.MULTILINE,
        ),
    ),
    (
        "P2STORAGE:W25_FREQUENCY PROBE=400000 ACTIVE=2000000",
        re.compile(
            r"^P2STORAGE:W25_FREQUENCY PROBE=400000 ACTIVE=2000000\r?$",
            re.MULTILINE,
        ),
    ),
    (
        "P2STORAGE:W25_GEOMETRY",
        re.compile(
            r"^P2STORAGE:W25_GEOMETRY "
            r"BLOCK=256 ERASE=4096 ERASEBLOCKS=4096 BYTES=16777216\r?$",
            re.MULTILINE,
        ),
    ),
    (
        "P2STORAGE:W25_LAYOUT",
        re.compile(
            r"^P2STORAGE:W25_LAYOUT "
            r"BOOT=0x00000000\+0x00080000 "
            r"DATA=0x00080000\+0x00F80000 "
            r"FIRSTBLOCK=2048 NBLOCKS=63488\r?$",
            re.MULTILINE,
        ),
    ),
    (
        "P2STORAGE:W25_BOOT_CRC32",
        BOOT_CRC_PATTERN,
    ),
    (
        "P2STORAGE:SMARTFS=/dev/smart0 AUTOFORMAT=NO",
        re.compile(
            r"^P2STORAGE:SMARTFS=/dev/smart0 AUTOFORMAT=NO\r?$",
            re.MULTILINE,
        ),
    ),
    (
        "P2STORAGE:MMCSD_FREQUENCY ID=400000 TRANSFER=2000000",
        re.compile(
            r"^P2STORAGE:MMCSD_FREQUENCY ID=400000 TRANSFER=2000000\r?$",
            re.MULTILINE,
        ),
    ),
    (
        "P2STORAGE:MMCSD=/dev/mmcsd0",
        re.compile(r"^P2STORAGE:MMCSD=/dev/mmcsd0\r?$", re.MULTILINE),
    ),
)

FAILURE_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    (
        "P2 SD ROM layout failure",
        re.compile(
            r"^P2STORAGE:SD:ROM-FAIL:STAGE=[A-Z0-9_-]+:" r"REASON=[A-Z0-9_-]+\r?$",
            re.MULTILINE,
        ),
    ),
    (
        "P2 storage action failure",
        re.compile(r"^P2STORAGE:FAIL:[A-Z0-9_-]+:[1-9][0-9]*\r?$", re.MULTILINE),
    ),
    (
        "P2 storage binding failure",
        re.compile(r"^P2STORAGE:[A-Z0-9_]+=FAIL:-?[0-9]+\r?$", re.MULTILINE),
    ),
)


def normalize_sequence(value: object) -> str:
    """Return an exact eight-digit uppercase hexadecimal HIL nonce."""

    if isinstance(value, int):
        if 0 <= value <= 0xFFFFFFFF:
            return "{:08X}".format(value)
        raise ValueError("storage sequence integer must fit in 32 bits")
    if not isinstance(value, str) or re.fullmatch(r"[0-9A-F]{8}", value) is None:
        raise ValueError("storage sequence must be exactly 8 uppercase hex digits")
    return value


def sequence_required(action: str) -> bool:
    _validate_action(action)
    return action in SEQUENCE_ACTIONS


def _validate_action(action: str) -> None:
    if action not in ACTIONS:
        raise ValueError("unknown P2 storage action: {}".format(action))


def fnv1a(data: bytes) -> int:
    value = 2166136261
    for byte in data:
        value ^= byte
        value = (value * 16777619) & 0xFFFFFFFF
    return value


def record_bytes(medium: str, sequence: object) -> bytes:
    """Build the exact 256-byte record written by the target HIL app."""

    sequence_text = normalize_sequence(sequence)
    sequence_value = int(sequence_text, 16)
    if medium == "flash":
        record = bytearray(b"P2STRG1F")
        seed = 0x46
    elif medium == "sd":
        record = bytearray(b"P2STRG1S")
        seed = 0x53
    else:
        raise ValueError("storage medium must be flash or sd")

    record.extend(sequence_value.to_bytes(4, "little"))
    for index in range(12, 252):
        sequence_byte = (sequence_value >> ((index & 3) * 8)) & 0xFF
        record.append((seed + index * 37 + sequence_byte) & 0xFF)
    record.extend(fnv1a(bytes(record)).to_bytes(4, "little"))
    if len(record) != 256:
        raise AssertionError("P2 storage record has wrong size")
    return bytes(record)


def record_checksum(medium: str, sequence: object) -> str:
    record = record_bytes(medium, sequence)
    return "{:08X}".format(int.from_bytes(record[-4:], "little"))


@functools.lru_cache(maxsize=16)
def stream_checksum(medium: str, sequence: object) -> str:
    """Return the target's FNV-1a for its one-MiB streaming file."""

    sequence_text = normalize_sequence(sequence)
    sequence_value = int(sequence_text, 16)
    if medium == "flash":
        seed = 0x46
    elif medium == "sd":
        seed = 0x53
    else:
        raise ValueError("storage medium must be flash or sd")

    value = 2166136261
    for offset in range(STREAM_SIZE):
        sequence_byte = (sequence_value >> ((offset & 3) * 8)) & 0xFF
        byte = (seed + (offset & 0xFF) * 37 + sequence_byte) & 0xFF
        value ^= byte
        value = (value * 16777619) & 0xFFFFFFFF
    return "{:08X}".format(value)


def command_line(action: str, sequence: Optional[object] = None) -> str:
    """Return the exact one-line NSH command, without a trailing newline."""

    _validate_action(action)
    if action in SEQUENCE_ACTIONS:
        if sequence is None:
            raise ValueError("{} requires a storage sequence".format(action))
        sequence_text = normalize_sequence(sequence)
    else:
        if sequence is not None:
            raise ValueError("{} does not accept a storage sequence".format(action))
        sequence_text = ""

    fields = ["p2storage", action]
    if action in TARGET_DESTRUCTIVE_ACTIONS:
        fields.append(ACKNOWLEDGEMENT)
    if sequence_text:
        fields.append(sequence_text)
    return " ".join(fields)


def command_bytes(action: str, sequence: Optional[object] = None) -> bytes:
    return (command_line(action, sequence) + "\r").encode("ascii")


def _line_pattern(literal: str) -> re.Pattern:
    return re.compile(r"^" + re.escape(literal) + r"\r?$", re.MULTILINE)


def response_marker_patterns(
    action: str,
    sequence: Optional[object] = None,
    alternate_count: int = ALTERNATE_TRANSACTIONS,
) -> Tuple[Tuple[str, re.Pattern], ...]:
    """Return ordered streaming markers for one target command response."""

    _validate_action(action)
    if action in SEQUENCE_ACTIONS:
        if sequence is None:
            raise ValueError("{} requires a storage sequence".format(action))
        sequence_text = normalize_sequence(sequence)
    else:
        if sequence is not None:
            raise ValueError("{} does not accept a storage sequence".format(action))
        sequence_text = ""
    if alternate_count <= 0 or alternate_count > 100000:
        raise ValueError("alternate transaction count must be in 1..100000")

    markers: List[Tuple[str, re.Pattern]] = []

    def literal(value: str) -> None:
        markers.append((value, _line_pattern(value)))

    literal("P2STORAGE:BEGIN:COMMAND={}".format(action))
    if action == "probe":
        markers.extend(
            (
                (
                    "P2STORAGE:PROBE:FLASH",
                    re.compile(
                        r"^P2STORAGE:PROBE:FLASH:"
                        r"DEV=/dev/smart0:AVAILABLE=1:WRITE=1:"
                        r"SECTORS=(?P<flash_sectors>[1-9][0-9]*):"
                        r"SECTORSIZE=(?P<flash_sectorsize>[1-9][0-9]*):PASS\r?$",
                        re.MULTILINE,
                    ),
                ),
                (
                    "P2STORAGE:PROBE:SD",
                    re.compile(
                        r"^P2STORAGE:PROBE:SD:"
                        r"DEV=/dev/mmcsd0:AVAILABLE=1:WRITE=1:"
                        r"SECTORS=(?P<sd_sectors>[1-9][0-9]*):"
                        r"SECTORSIZE=(?P<sd_sectorsize>[1-9][0-9]*):PASS\r?$",
                        re.MULTILINE,
                    ),
                ),
            )
        )
    elif action == "sd-rom-verify":
        markers.extend(
            (
                (
                    "P2STORAGE:SD:ROM-MBR",
                    re.compile(
                        r"^P2STORAGE:SD:ROM-MBR:"
                        r"TYPE=(?P<sd_rom_partition_type>0[BC]):"
                        r"START=(?P<sd_rom_partition_start>[1-9][0-9]*):"
                        r"SECTORS=(?P<sd_rom_partition_sectors>[1-9][0-9]*):"
                        r"PASS\r?$",
                        re.MULTILINE,
                    ),
                ),
                (
                    "P2STORAGE:SD:ROM-VBR",
                    re.compile(
                        r"^P2STORAGE:SD:ROM-VBR:"
                        r"BPS=(?P<sd_rom_bytes_per_sector>512):"
                        r"SPC=(?P<sd_rom_sectors_per_cluster>"
                        r"1|2|4|8|16|32|64|128):"
                        r"RESERVED=(?P<sd_rom_reserved_sectors>"
                        r"[1-9][0-9]*):FATS=2:"
                        r"FATSZ=(?P<sd_rom_fat_sectors>[1-9][0-9]*):"
                        r"ROOT=(?P<sd_rom_root_cluster>2):"
                        r"FSINFO=(?P<sd_rom_fsinfo_sector>[1-9][0-9]*):"
                        r"PASS\r?$",
                        re.MULTILINE,
                    ),
                ),
                (
                    "P2STORAGE:SD:ROM-FSINFO",
                    re.compile(
                        r"^P2STORAGE:SD:ROM-FSINFO:"
                        r"LBA=(?P<sd_rom_fsinfo_lba>[1-9][0-9]*):PASS\r?$",
                        re.MULTILINE,
                    ),
                ),
                (
                    "P2STORAGE:SD:ROM-ROOT",
                    re.compile(
                        r"^P2STORAGE:SD:ROM-ROOT:"
                        r"LBA=(?P<sd_rom_root_lba>[1-9][0-9]*):"
                        r"ENTRY=(?P<sd_rom_directory_entry>[0-9]+):"
                        r"NAME=_BOOT_P2\.BIX:"
                        r"CLUSTER=(?P<sd_rom_file_cluster>"
                        r"[2-9]|[1-9][0-9]+):"
                        r"BYTES=(?P<sd_rom_file_bytes>[1-9][0-9]*):"
                        r"PASS\r?$",
                        re.MULTILINE,
                    ),
                ),
                (
                    "P2STORAGE:SD:ROM-CHAIN",
                    re.compile(
                        r"^P2STORAGE:SD:ROM-CHAIN:"
                        r"FIRST=(?P<sd_rom_chain_first>"
                        r"[2-9]|[1-9][0-9]+):"
                        r"CLUSTERS=(?P<sd_rom_chain_clusters>"
                        r"[1-9][0-9]*):CONTIGUOUS=1:"
                        r"EOC=(?P<sd_rom_chain_eoc>0FFFFFF[89A-F]):"
                        r"PASS\r?$",
                        re.MULTILINE,
                    ),
                ),
                (
                    "P2STORAGE:SD:ROM-IMAGE",
                    re.compile(
                        r"^P2STORAGE:SD:ROM-IMAGE:"
                        r"LBA=(?P<sd_rom_image_lba>[1-9][0-9]*):"
                        r"SECTORS=(?P<sd_rom_image_sectors>[1-9][0-9]*):"
                        r"BYTES=(?P<sd_rom_image_bytes>[1-9][0-9]*):"
                        r"FNV1A=(?P<sd_rom_image_fnv1a>[0-9A-F]{8}):"
                        r"PASS\r?$",
                        re.MULTILINE,
                    ),
                ),
            )
        )
    elif action == "sd-mbr-repair":
        markers.extend(
            (
                (
                    "P2STORAGE:SD:ROM-MBR",
                    re.compile(
                        r"^P2STORAGE:SD:ROM-MBR:TYPE=0C:START=2048:"
                        r"SECTORS=(?P<sd_repair_partition_sectors>"
                        r"[1-9][0-9]*):PASS\r?$",
                        re.MULTILINE,
                    ),
                ),
                (
                    "P2STORAGE:SD:MBR-REPAIR",
                    re.compile(
                        r"^P2STORAGE:SD:MBR-REPAIR:START=2048:"
                        r"SECTORS=(?P<sd_repair_confirm_sectors>"
                        r"[1-9][0-9]*):PASS\r?$",
                        re.MULTILINE,
                    ),
                ),
            )
        )
    elif action == "flash-format":
        literal("P2STORAGE:FLASH:FORMAT:PASS")
    elif action in ("flash-write", "flash-verify"):
        checksum = stream_checksum("flash", sequence_text)
        stage = "WRITE" if action == "flash-write" else "PERSISTENCE"
        literal(
            "P2STORAGE:FLASH:{}:SEQUENCE={}:BYTES={}:FNV1A={}:PASS".format(
                stage, sequence_text, STREAM_SIZE, checksum
            )
        )
        if action == "flash-write":
            literal("P2STORAGE:READY:RESET=FLASH:SEQUENCE={}".format(sequence_text))
    elif action == "flash-cycle":
        base = int(sequence_text, 16)
        for iteration in range(1, FLASH_CYCLE_COUNT + 1):
            iteration_sequence = normalize_sequence((base + iteration - 1) & 0xFFFFFFFF)
            literal(
                "P2STORAGE:FLASH:CYCLE:ITERATION={}:SEQUENCE={}:"
                "FNV1A={}:PASS".format(
                    iteration,
                    iteration_sequence,
                    record_checksum("flash", iteration_sequence),
                )
            )
        literal("P2STORAGE:FLASH:CYCLE:COUNT={}:PASS".format(FLASH_CYCLE_COUNT))
    elif action == "flash-full":
        markers.append(
            (
                "P2STORAGE:FLASH:FULL",
                re.compile(
                    r"^P2STORAGE:FLASH:FULL:SEQUENCE="
                    + re.escape(sequence_text)
                    + r":BYTES=(?P<flash_full_bytes>[1-9][0-9]*):"
                    r"ENOSPC=1:PASS\r?$",
                    re.MULTILINE,
                ),
            )
        )
    elif action == "flash-interrupt-arm":
        base = int(sequence_text, 16)
        pending = normalize_sequence((base + 1) & 0xFFFFFFFF)
        literal(
            "P2STORAGE:FLASH:INTERRUPT:ARMED:BASE_SEQUENCE={}:"
            "PENDING_SEQUENCE={}:WRITTEN=128".format(sequence_text, pending)
        )
        literal("P2STORAGE:READY:POWER-CUT=FLASH:SEQUENCE={}".format(sequence_text))
    elif action == "flash-interrupt-verify":
        markers.append(
            (
                "P2STORAGE:FLASH:INTERRUPT:PENDING",
                re.compile(
                    r"^P2STORAGE:FLASH:INTERRUPT:PENDING="
                    r"(?P<interrupt_pending>ABSENT|PREFIX):"
                    r"BYTES=(?P<interrupt_pending_bytes>[0-9]{1,3}):PASS\r?$",
                    re.MULTILINE,
                ),
            )
        )
        literal(
            "P2STORAGE:FLASH:INTERRUPT:RECOVERY:SEQUENCE={}:PASS".format(sequence_text)
        )
    elif action == "sd-format":
        literal("P2STORAGE:SD:FORMAT:PASS")
    elif action in ("sd-write", "sd-verify"):
        checksum = stream_checksum("sd", sequence_text)
        stage = "WRITE" if action == "sd-write" else "PERSISTENCE"
        literal(
            "P2STORAGE:SD:{}:SEQUENCE={}:BYTES={}:FNV1A={}:PASS".format(
                stage, sequence_text, STREAM_SIZE, checksum
            )
        )
        if action == "sd-write":
            literal("P2STORAGE:READY:RESET=SD:SEQUENCE={}".format(sequence_text))
    elif action == "sd-rename-delete":
        literal("P2STORAGE:SD:MKDIR:SEQUENCE={}:PASS".format(sequence_text))
        literal("P2STORAGE:SD:RENAME:SEQUENCE={}:PASS".format(sequence_text))
        literal("P2STORAGE:SD:DELETE:SEQUENCE={}:PASS".format(sequence_text))
    elif action == "sd-stress":
        base = int(sequence_text, 16)
        for iteration in range(1, SD_STRESS_COUNT + 1):
            iteration_sequence = normalize_sequence((base + iteration - 1) & 0xFFFFFFFF)
            literal(
                "P2STORAGE:SD:STRESS:ITERATION={}:SEQUENCE={}:"
                "FNV1A={}:PASS".format(
                    iteration,
                    iteration_sequence,
                    record_checksum("sd", iteration_sequence),
                )
            )
        literal("P2STORAGE:SD:STRESS:COUNT={}:PASS".format(SD_STRESS_COUNT))
    elif action == "alternate":
        markers.extend(
            (
                "P2STORAGE:BUS:ITERATION={}".format(iteration),
                _line_pattern(
                    "P2STORAGE:BUS:ITERATION={}:FLASH=PASS:SD=PASS".format(iteration)
                ),
            )
            for iteration in range(1, alternate_count + 1)
        )
        literal("P2STORAGE:BUS:ALTERNATE:COUNT={}:PASS".format(alternate_count))

    if action != "flash-interrupt-arm":
        literal("P2STORAGE:PASS:{}".format(action.upper()))
    return tuple(markers)


def parse_storage_response(  # noqa: C901
    text: str,
    action: str,
    sequence: Optional[object] = None,
    alternate_count: int = ALTERNATE_TRANSACTIONS,
) -> Dict[str, object]:
    """Strictly validate one command response captured from one reset."""

    markers = response_marker_patterns(action, sequence, alternate_count)
    failures: List[Dict[str, str]] = []
    for line in text.replace("\r", "\n").split("\n"):
        stripped = line.strip()
        for label, pattern in FAILURE_PATTERNS:
            if pattern.search("\n" + stripped + "\n"):
                failures.append({"kind": label, "line": stripped})
                break

    found: Dict[str, int] = {}
    captures: Dict[str, str] = {}
    duplicates: List[str] = []
    for label, pattern in markers:
        matches = list(pattern.finditer(text))
        if len(matches) > 1:
            duplicates.append(label)
        elif matches:
            found[label] = matches[0].start()
            for name, value in matches[0].groupdict().items():
                if value is not None:
                    captures[name] = value

    missing = [label for label, pattern in markers if label not in found]
    positions = [found[label] for label, pattern in markers if label in found]
    order_valid = positions == sorted(positions)
    errors: List[str] = []
    if not order_valid:
        errors.append("storage response markers are out of order")

    if action == "probe":
        for name in (
            "flash_sectors",
            "flash_sectorsize",
            "sd_sectors",
            "sd_sectorsize",
        ):
            if name in captures and int(captures[name]) <= 0:
                errors.append("{} must be positive".format(name))
    if action == "flash-full" and "flash_full_bytes" in captures:
        if int(captures["flash_full_bytes"]) <= 0:
            errors.append("flash_full_bytes must be positive")
    if action == "flash-interrupt-verify" and "interrupt_pending" in captures:
        pending = captures["interrupt_pending"]
        pending_bytes = int(captures["interrupt_pending_bytes"])
        if pending == "ABSENT" and pending_bytes != 0:
            errors.append("ABSENT interrupted file must report BYTES=0")
        if pending == "PREFIX" and not 0 <= pending_bytes <= 128:
            errors.append("interrupted prefix length must be in 0..128")
    if action == "sd-rom-verify":
        if (
            "sd_rom_file_cluster" in captures
            and "sd_rom_chain_first" in captures
            and captures["sd_rom_file_cluster"] != captures["sd_rom_chain_first"]
        ):
            errors.append("SD ROM chain must begin at the directory cluster")
        if "sd_rom_file_bytes" in captures and "sd_rom_image_bytes" in captures:
            file_bytes = int(captures["sd_rom_file_bytes"])
            if file_bytes != int(captures["sd_rom_image_bytes"]):
                errors.append("SD ROM raw image byte count must match directory")
            if file_bytes > 507904:
                errors.append("SD ROM image exceeds the P2 ROM size limit")
            if "sd_rom_image_sectors" in captures:
                expected_sectors = (file_bytes + 511) // 512
                if int(captures["sd_rom_image_sectors"]) != expected_sectors:
                    errors.append("SD ROM image sector count does not match bytes")
        if all(
            name in captures
            for name in (
                "sd_rom_file_bytes",
                "sd_rom_chain_clusters",
                "sd_rom_sectors_per_cluster",
            )
        ):
            cluster_bytes = int(captures["sd_rom_sectors_per_cluster"]) * 512
            expected_clusters = (
                int(captures["sd_rom_file_bytes"]) + cluster_bytes - 1
            ) // cluster_bytes
            if int(captures["sd_rom_chain_clusters"]) != expected_clusters:
                errors.append("SD ROM FAT chain length does not match bytes")
    if action == "sd-mbr-repair" and all(
        name in captures
        for name in (
            "sd_repair_partition_sectors",
            "sd_repair_confirm_sectors",
        )
    ):
        if (
            captures["sd_repair_partition_sectors"]
            != captures["sd_repair_confirm_sectors"]
        ):
            errors.append("SD MBR repair sector counts must match")

    sequence_text = normalize_sequence(sequence) if action in SEQUENCE_ACTIONS else None
    complete = not missing and not duplicates and not failures and not errors
    return {
        "complete": complete,
        "action": action,
        "sequence": sequence_text,
        "command": command_line(action, sequence),
        "found": [label for label, pattern in markers if label in found],
        "missing": missing,
        "duplicates": duplicates,
        "failures": failures,
        "errors": errors,
        "captures": dict(sorted(captures.items())),
        "order_valid": order_valid,
        "expected_checksum": (
            stream_checksum("flash", sequence_text)
            if action
            in (
                "flash-write",
                "flash-verify",
                "flash-interrupt-verify",
            )
            else (
                stream_checksum("sd", sequence_text)
                if action in ("sd-write", "sd-verify")
                else None
            )
        ),
    }


def response_labels(
    action: str,
    sequence: Optional[object] = None,
    alternate_count: int = ALTERNATE_TRANSACTIONS,
) -> Tuple[str, ...]:
    return tuple(
        label
        for label, pattern in response_marker_patterns(
            action, sequence, alternate_count
        )
    )


def first_error(result: Dict[str, object]) -> str:
    """Return a stable one-line reason for an incomplete parsed response."""

    details: List[str] = []
    details.extend(str(item) for item in result.get("errors", ()))
    details.extend("missing {}".format(item) for item in result.get("missing", ()))
    details.extend("duplicate {}".format(item) for item in result.get("duplicates", ()))
    details.extend(str(item.get("line", item)) for item in result.get("failures", ()))
    return "; ".join(details) or "incomplete storage response"
