#!/usr/bin/env python3
"""Pack and validate deterministic Propeller 2 Python runtime containers."""

# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import binascii
import dataclasses
import hashlib
import json
import os
import pathlib
import stat
import struct
import sys
import tempfile
import unicodedata
from typing import BinaryIO, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


FORMAT_NAME = "p2-python-container-input-v1"
MAGIC = b"P2PYCTN\x00"
VERSION_MAJOR = 1
VERSION_MINOR = 0
ENDIAN_TAG = 0x01020304

UINT32_MAX = (1 << 32) - 1
UINT64_MAX = (1 << 64) - 1
MAX_CONTAINER_SIZE = UINT32_MAX
MAX_MANIFEST_SIZE = 32 * 1024 * 1024
MAX_SECTIONS = 65535
MAX_STUBS = 1 << 20
MAX_NAME_BYTES = 1024
MAX_ALIGNMENT = 1 << 20
IO_CHUNK_SIZE = 64 * 1024
CONTAINER_ALIGNMENT = 16

P2_PSRAM_BASE = 0x10000000
P2_PSRAM_SIZE = 32 * 1024 * 1024
P2_PSRAM_END = P2_PSRAM_BASE + P2_PSRAM_SIZE
P2_PC_LIMIT = 1 << 20
P2_HUB_LOAD_LIMIT = 0x0007C000
P2_INSTRUCTION_SIZE = 4

TYPE_EXTERNAL_INIT = 1
TYPE_EXTERNAL_ZERO = 2
TYPE_OVERLAY_GROUP = 3
TYPE_STDLIB_ROMFS = 4

SECTION_TYPE_NAMES = {
    TYPE_EXTERNAL_INIT: "external-init",
    TYPE_EXTERNAL_ZERO: "external-zero",
    TYPE_OVERLAY_GROUP: "overlay-group",
    TYPE_STDLIB_ROMFS: "stdlib-romfs",
}

CODEC_NONE = 0
CODEC_NAMES = {CODEC_NONE: "none"}

SECTION_FLAG_REQUIRED = 1 << 0
SECTION_FLAG_READ_ONLY = 1 << 1
SECTION_FLAG_EXECUTABLE = 1 << 2
SECTION_FLAG_FIXED_ADDRESS = 1 << 3
SECTION_FLAG_MASK = (
    SECTION_FLAG_REQUIRED
    | SECTION_FLAG_READ_ONLY
    | SECTION_FLAG_EXECUTABLE
    | SECTION_FLAG_FIXED_ADDRESS
)
SECTION_FLAG_NAMES = {
    "required": SECTION_FLAG_REQUIRED,
    "read-only": SECTION_FLAG_READ_ONLY,
    "executable": SECTION_FLAG_EXECUTABLE,
    "fixed-address": SECTION_FLAG_FIXED_ADDRESS,
}

DEFAULT_SECTION_FLAGS = {
    TYPE_EXTERNAL_INIT: SECTION_FLAG_REQUIRED | SECTION_FLAG_FIXED_ADDRESS,
    TYPE_EXTERNAL_ZERO: SECTION_FLAG_REQUIRED | SECTION_FLAG_FIXED_ADDRESS,
    TYPE_OVERLAY_GROUP: (
        SECTION_FLAG_REQUIRED
        | SECTION_FLAG_READ_ONLY
        | SECTION_FLAG_EXECUTABLE
        | SECTION_FLAG_FIXED_ADDRESS
    ),
    TYPE_STDLIB_ROMFS: SECTION_FLAG_REQUIRED | SECTION_FLAG_READ_ONLY,
}

HEADER_FLAG_EXTERNAL_INIT = 1 << 0
HEADER_FLAG_EXTERNAL_ZERO = 1 << 1
HEADER_FLAG_OVERLAYS = 1 << 2
HEADER_FLAG_STUBS = 1 << 3
HEADER_FLAG_STDLIB_ROMFS = 1 << 4
HEADER_FLAG_MASK = (
    HEADER_FLAG_EXTERNAL_INIT
    | HEADER_FLAG_EXTERNAL_ZERO
    | HEADER_FLAG_OVERLAYS
    | HEADER_FLAG_STUBS
    | HEADER_FLAG_STDLIB_ROMFS
)

# Header layout is documented in P2_PYTHON_CONTAINER_FORMAT.md.  The fixed
# sizes are part of the target ABI; changing one requires a new major version.

HEADER_STRUCT = struct.Struct("<8s8H6I8Q32s32sII8s")
SECTION_STRUCT = struct.Struct("<HHIIIIIIQQQQQII20s")
GROUP_STRUCT = struct.Struct("<IIII")
STUB_STRUCT = struct.Struct("<II")
STUB_NAME_STRUCT = struct.Struct("<II")

HEADER_SIZE = HEADER_STRUCT.size
SECTION_ENTRY_SIZE = SECTION_STRUCT.size
GROUP_ENTRY_SIZE = GROUP_STRUCT.size
STUB_ENTRY_SIZE = STUB_STRUCT.size
STUB_NAME_ENTRY_SIZE = STUB_NAME_STRUCT.size
MANIFEST_SHA256_OFFSET = 144
MANIFEST_SHA256_SIZE = 32

assert HEADER_SIZE == 192
assert SECTION_ENTRY_SIZE == 96
assert GROUP_ENTRY_SIZE == 16
assert STUB_ENTRY_SIZE == 8
assert STUB_NAME_ENTRY_SIZE == 8


class ContainerError(ValueError):
    """Raised when an input manifest or container violates the ABI."""


@dataclasses.dataclass(frozen=True)
class Section:
    section_type: int
    codec: int
    flags: int
    section_id: int
    name: str
    alignment: int
    virtual_address: int
    file_offset: int
    stored_size: int
    memory_size: int
    uncompressed_size: int
    crc32: int
    source: Optional[pathlib.Path] = dataclasses.field(
        default=None, compare=False, repr=False
    )

    @property
    def has_payload(self) -> bool:
        return self.section_type != TYPE_EXTERNAL_ZERO


@dataclasses.dataclass(frozen=True)
class Stub:
    stub_id: int
    group_id: int
    entry_offset: int
    flags: int
    name: str


@dataclasses.dataclass(frozen=True)
class Container:
    path: pathlib.Path
    flags: int
    file_size: int
    manifest_size: int
    overlay_load_address: int
    overlay_slot_size: int
    build_fingerprint: bytes
    manifest_sha256: bytes
    sections: Tuple[Section, ...]
    stubs: Tuple[Stub, ...]


def _fail(message: str) -> None:
    raise ContainerError(message)


def _checked_add(left: int, right: int, context: str, limit: int = UINT64_MAX) -> int:
    if left < 0 or right < 0 or left > limit - right:
        _fail("{} overflows its {}-bit range".format(context, limit.bit_length()))
    return left + right


def _checked_mul(left: int, right: int, context: str, limit: int = UINT64_MAX) -> int:
    if left < 0 or right < 0 or (left != 0 and right > limit // left):
        _fail("{} overflows its {}-bit range".format(context, limit.bit_length()))
    return left * right


def _align_up(value: int, alignment: int, context: str = "alignment") -> int:
    if alignment <= 0 or alignment & (alignment - 1):
        _fail("{} is not a positive power of two".format(context))
    mask = alignment - 1
    if value > UINT64_MAX - mask:
        _fail("{} overflows while aligning".format(context))
    return (value + mask) & ~mask


def _parse_object_pairs(pairs: Iterable[Tuple[str, object]]) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate JSON key {!r}".format(key))
        result[key] = value
    return result


def _load_input_manifest(path: pathlib.Path) -> Mapping[str, object]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream, object_pairs_hook=_parse_object_pairs)
    except ContainerError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("cannot read input manifest {}: {}".format(path, exc))
    if not isinstance(value, dict):
        _fail("input manifest root must be a JSON object")
    return value


def _check_keys(
    value: Mapping[str, object],
    required: Sequence[str],
    optional: Sequence[str],
    context: str,
) -> None:
    allowed = set(required) | set(optional)
    missing = sorted(set(required) - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        _fail("{} is missing required key(s): {}".format(context, ", ".join(missing)))
    if unknown:
        _fail("{} has unknown key(s): {}".format(context, ", ".join(unknown)))


def _parse_integer(value: object, context: str, maximum: int = UINT64_MAX) -> int:
    if isinstance(value, bool):
        _fail("{} must be an integer, not a boolean".format(context))
    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        if value != value.strip() or not value:
            _fail("{} has an invalid integer spelling".format(context))
        try:
            result = int(value, 0)
        except ValueError:
            _fail("{} has an invalid integer spelling".format(context))
    else:
        _fail("{} must be an integer or base-prefixed integer string".format(context))
    if result < 0 or result > maximum:
        _fail("{} is outside 0..{}".format(context, maximum))
    return result


def _parse_alignment(value: object, context: str, default: int) -> int:
    if value is None:
        result = default
    else:
        result = _parse_integer(value, context, MAX_ALIGNMENT)
    if result == 0 or result & (result - 1):
        _fail("{} must be a power of two".format(context))
    return result


def _parse_name(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        _fail("{} must be a non-empty string".format(context))
    if unicodedata.normalize("NFC", value) != value:
        _fail("{} must use Unicode NFC normalization".format(context))
    if "\x00" in value or any(ord(character) < 0x20 for character in value):
        _fail("{} contains a NUL or control character".format(context))
    encoded = value.encode("utf-8")
    if len(encoded) > MAX_NAME_BYTES:
        _fail("{} exceeds {} UTF-8 bytes".format(context, MAX_NAME_BYTES))
    return value


def _parse_codec(value: object, context: str) -> int:
    if value is None or value == "none":
        return CODEC_NONE
    _fail("{} codec {!r} is unsupported; only 'none' is valid in version 1".format(context, value))
    raise AssertionError("unreachable")


def _parse_flags(value: object, section_type: int, context: str) -> int:
    if value is None:
        return DEFAULT_SECTION_FLAGS[section_type]
    if not isinstance(value, list):
        _fail("{}.flags must be a JSON array".format(context))
    result = 0
    seen = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or item not in SECTION_FLAG_NAMES:
            _fail("{}.flags[{}] is unknown".format(context, index))
        if item in seen:
            _fail("{}.flags repeats {!r}".format(context, item))
        seen.add(item)
        result |= SECTION_FLAG_NAMES[item]
    _validate_section_flags(section_type, result, context)
    return result


def _validate_section_flags(section_type: int, flags: int, context: str) -> None:
    if flags & ~SECTION_FLAG_MASK:
        _fail("{} contains unknown section flag bits 0x{:x}".format(context, flags & ~SECTION_FLAG_MASK))
    if not flags & SECTION_FLAG_REQUIRED:
        _fail("{} must carry the required flag".format(context))
    if section_type in (TYPE_EXTERNAL_INIT, TYPE_EXTERNAL_ZERO):
        if not flags & SECTION_FLAG_FIXED_ADDRESS:
            _fail("{} must carry the fixed-address flag".format(context))
        if flags & SECTION_FLAG_EXECUTABLE:
            _fail("{} external data must not be executable".format(context))
        if section_type == TYPE_EXTERNAL_ZERO and flags & SECTION_FLAG_READ_ONLY:
            _fail("{} zero-fill data must be writable".format(context))
    elif section_type == TYPE_OVERLAY_GROUP:
        required = SECTION_FLAG_READ_ONLY | SECTION_FLAG_EXECUTABLE | SECTION_FLAG_FIXED_ADDRESS
        if flags & required != required:
            _fail("{} overlay group must be read-only, executable, and fixed-address".format(context))
    elif section_type == TYPE_STDLIB_ROMFS:
        if not flags & SECTION_FLAG_READ_ONLY:
            _fail("{} ROMFS must carry the read-only flag".format(context))
        if flags & (SECTION_FLAG_EXECUTABLE | SECTION_FLAG_FIXED_ADDRESS):
            _fail("{} ROMFS must not be executable or fixed-address".format(context))
    else:
        _fail("{} uses unknown section type {}".format(context, section_type))


def _parse_fingerprint(value: object) -> bytes:
    if not isinstance(value, str) or len(value) != 64:
        _fail("build_fingerprint must be exactly 64 hexadecimal characters")
    try:
        result = bytes.fromhex(value)
    except ValueError:
        _fail("build_fingerprint must be exactly 64 hexadecimal characters")
    if result == bytes(32):
        _fail("build_fingerprint must not be all zero")
    return result


def _parse_source_path(
    value: object, manifest_path: pathlib.Path, context: str
) -> pathlib.Path:
    if not isinstance(value, str) or not value:
        _fail("{}.path must be a non-empty string".format(context))
    source = pathlib.Path(value)
    if not source.is_absolute():
        source = manifest_path.parent / source
    try:
        source = source.resolve(strict=True)
        mode = source.stat().st_mode
    except OSError as exc:
        _fail("cannot access {} payload {}: {}".format(context, source, exc))
    if not stat.S_ISREG(mode):
        _fail("{} payload {} is not a regular file".format(context, source))
    return source


def _crc32_stream(stream: BinaryIO, count: int, context: str) -> int:
    remaining = count
    checksum = 0
    while remaining:
        chunk = stream.read(min(IO_CHUNK_SIZE, remaining))
        if not chunk:
            _fail("{} is truncated".format(context))
        checksum = binascii.crc32(chunk, checksum)
        remaining -= len(chunk)
    return checksum & UINT32_MAX


def _source_size_crc(path: pathlib.Path, context: str) -> Tuple[int, int]:
    try:
        before = path.stat()
        size = before.st_size
        if size <= 0:
            _fail("{} payload must not be empty".format(context))
        if size > UINT32_MAX:
            _fail("{} payload exceeds the 32-bit target size limit".format(context))
        with path.open("rb") as stream:
            checksum = _crc32_stream(stream, size, context)
            if stream.read(1):
                _fail("{} payload grew while it was checksummed".format(context))
        after = path.stat()
    except ContainerError:
        raise
    except OSError as exc:
        _fail("cannot checksum {} payload {}: {}".format(context, path, exc))
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        _fail("{} payload changed while it was checksummed".format(context))
    return size, checksum


def _parse_section_payload(
    value: object,
    section_type: int,
    manifest_path: pathlib.Path,
    context: str,
) -> Section:
    if not isinstance(value, dict):
        _fail("{} must be a JSON object".format(context))
    address_key = "load_address" if section_type == TYPE_OVERLAY_GROUP else "address"
    _check_keys(
        value,
        ("id", "name", "path", address_key),
        ("alignment", "codec", "flags"),
        context,
    )
    section_id = _parse_integer(value["id"], "{}.id".format(context), UINT32_MAX)
    name = _parse_name(value["name"], "{}.name".format(context))
    alignment = _parse_alignment(value.get("alignment"), "{}.alignment".format(context), 4)
    codec = _parse_codec(value.get("codec"), context)
    flags = _parse_flags(value.get("flags"), section_type, context)
    source = _parse_source_path(value["path"], manifest_path, context)
    size, checksum = _source_size_crc(source, context)
    address = _parse_integer(value[address_key], "{}.{}".format(context, address_key), UINT32_MAX)
    return Section(
        section_type=section_type,
        codec=codec,
        flags=flags,
        section_id=section_id,
        name=name,
        alignment=alignment,
        virtual_address=address,
        file_offset=0,
        stored_size=size,
        memory_size=size,
        uncompressed_size=size,
        crc32=checksum,
        source=source,
    )


def _parse_zero_section(value: object, context: str) -> Section:
    if not isinstance(value, dict):
        _fail("{} must be a JSON object".format(context))
    _check_keys(
        value,
        ("id", "name", "address", "size"),
        ("alignment", "flags"),
        context,
    )
    section_id = _parse_integer(value["id"], "{}.id".format(context), UINT32_MAX)
    name = _parse_name(value["name"], "{}.name".format(context))
    alignment = _parse_alignment(value.get("alignment"), "{}.alignment".format(context), 4)
    flags = _parse_flags(value.get("flags"), TYPE_EXTERNAL_ZERO, context)
    address = _parse_integer(value["address"], "{}.address".format(context), UINT32_MAX)
    size = _parse_integer(value["size"], "{}.size".format(context), UINT32_MAX)
    if size == 0:
        _fail("{}.size must be nonzero".format(context))
    return Section(
        section_type=TYPE_EXTERNAL_ZERO,
        codec=CODEC_NONE,
        flags=flags,
        section_id=section_id,
        name=name,
        alignment=alignment,
        virtual_address=address,
        file_offset=0,
        stored_size=0,
        memory_size=size,
        uncompressed_size=0,
        crc32=0,
    )


def _parse_romfs(
    value: object, manifest_path: pathlib.Path, context: str
) -> Section:
    if not isinstance(value, dict):
        _fail("{} must be a JSON object".format(context))
    _check_keys(
        value,
        ("name", "path"),
        ("id", "alignment", "codec", "flags"),
        context,
    )
    normalized = dict(value)
    normalized.setdefault("id", 0)
    normalized["address"] = 0
    return _parse_section_payload(
        normalized, TYPE_STDLIB_ROMFS, manifest_path, context
    )


def _parse_array(value: object, context: str) -> Sequence[object]:
    if not isinstance(value, list):
        _fail("{} must be a JSON array".format(context))
    return value


def _validate_contiguous_ids(sections: Sequence[Section], section_type: int) -> None:
    ids = sorted(section.section_id for section in sections if section.section_type == section_type)
    first = 1 if section_type == TYPE_OVERLAY_GROUP else 0
    if ids != list(range(first, first + len(ids))):
        if section_type == TYPE_OVERLAY_GROUP:
            _fail(
                "overlay-group section IDs must be contiguous from one; "
                "group zero is reserved for resident code"
            )
        _fail(
            "{} section IDs must be contiguous from zero".format(
                SECTION_TYPE_NAMES[section_type]
            )
        )


def _validate_external_ranges(sections: Sequence[Section]) -> None:
    ranges = []
    for section in sections:
        if section.section_type not in (TYPE_EXTERNAL_INIT, TYPE_EXTERNAL_ZERO):
            continue
        start = section.virtual_address
        end = _checked_add(start, section.memory_size, "{} virtual range".format(section.name), UINT32_MAX + 1)
        if start < P2_PSRAM_BASE or end > P2_PSRAM_END:
            _fail("{} lies outside tagged 32-MiB PSRAM range 0x{:08x}..0x{:08x}".format(section.name, P2_PSRAM_BASE, P2_PSRAM_END))
        if start & (section.alignment - 1):
            _fail("{} virtual address is not aligned to {}".format(section.name, section.alignment))
        ranges.append((start, end, section.name))
    ranges.sort()
    for previous, current in zip(ranges, ranges[1:]):
        if current[0] < previous[1]:
            _fail("external sections {!r} and {!r} overlap".format(previous[2], current[2]))


def _validate_overlay_ranges(
    sections: Sequence[Section],
    configured_load_address: int,
    configured_slot_size: int,
) -> None:
    if (
        configured_slot_size == 0
        or configured_slot_size & (P2_INSTRUCTION_SIZE - 1)
    ):
        _fail("configured overlay slot size must be a nonzero multiple of four")
    slot_end = _checked_add(
        configured_load_address,
        configured_slot_size,
        "configured overlay slot range",
        P2_PC_LIMIT,
    )
    if configured_load_address == 0 or slot_end > P2_HUB_LOAD_LIMIT:
        _fail(
            "configured overlay slot lies outside the pinned P2 Hub load "
            "window ending at 0x{:08x}".format(P2_HUB_LOAD_LIMIT)
        )
    load_addresses = set()
    for section in sections:
        if section.section_type != TYPE_OVERLAY_GROUP:
            continue
        start = section.virtual_address
        end = _checked_add(start, section.memory_size, "{} overlay range".format(section.name), P2_PC_LIMIT)
        if start == 0 or end > P2_HUB_LOAD_LIMIT:
            _fail(
                "{} lies outside the pinned P2 Hub load window ending at "
                "0x{:08x}".format(section.name, P2_HUB_LOAD_LIMIT)
            )
        if start & (section.alignment - 1) or start & (P2_INSTRUCTION_SIZE - 1):
            _fail("{} load address is not correctly aligned".format(section.name))
        if section.memory_size & (P2_INSTRUCTION_SIZE - 1):
            _fail("{} size is not a whole number of P2 instructions".format(section.name))
        if section.memory_size > configured_slot_size:
            _fail(
                "{} decoded size {} exceeds configured Hub overlay slot {}".format(
                    section.name, section.memory_size, configured_slot_size
                )
            )
        load_addresses.add(start)
    if len(load_addresses) != 1:
        _fail("all overlay groups must use one fixed Hub load address")
    if configured_load_address not in load_addresses:
        _fail("overlay group load address does not match the configured slot")


def _validate_sections(
    sections: Sequence[Section],
    overlay_load_address: int,
    overlay_slot_size: int,
) -> None:
    if len(sections) > MAX_SECTIONS:
        _fail("section count exceeds {}".format(MAX_SECTIONS))
    if len({section.name for section in sections}) != len(sections):
        _fail("section names must be globally unique")
    keys = [(section.section_type, section.section_id) for section in sections]
    if len(set(keys)) != len(keys):
        _fail("section IDs must be unique within each section type")
    for section in sections:
        if section.section_type not in SECTION_TYPE_NAMES:
            _fail("unknown section type {}".format(section.section_type))
        if section.codec != CODEC_NONE:
            _fail("{} uses unsupported codec {}".format(section.name, section.codec))
        if section.alignment == 0 or section.alignment > MAX_ALIGNMENT or section.alignment & (section.alignment - 1):
            _fail("{} has invalid alignment {}".format(section.name, section.alignment))
        _validate_section_flags(section.section_type, section.flags, section.name)
        if section.section_type == TYPE_EXTERNAL_ZERO:
            if section.file_offset or section.stored_size or section.uncompressed_size or section.crc32:
                _fail("{} zero-fill section contains file payload metadata".format(section.name))
            if section.memory_size == 0:
                _fail("{} zero-fill section has zero memory size".format(section.name))
            if section.memory_size > UINT32_MAX:
                _fail("{} zero-fill size exceeds the 32-bit target limit".format(section.name))
        else:
            if section.stored_size == 0:
                _fail("{} file-backed section is empty".format(section.name))
            if (
                section.stored_size > UINT32_MAX
                or section.memory_size > UINT32_MAX
                or section.uncompressed_size > UINT32_MAX
            ):
                _fail("{} exceeds the 32-bit target size limit".format(section.name))
            if section.stored_size != section.uncompressed_size or section.memory_size != section.uncompressed_size:
                _fail("{} codec-none sizes disagree".format(section.name))
        if (
            section.section_type == TYPE_STDLIB_ROMFS
            and section.virtual_address != 0
        ):
            _fail("{} ROMFS virtual address must be zero".format(section.name))
    for section_type in SECTION_TYPE_NAMES:
        _validate_contiguous_ids(sections, section_type)
    if not any(section.section_type == TYPE_OVERLAY_GROUP for section in sections):
        _fail("container must contain at least one overlay group")
    romfs_count = sum(section.section_type == TYPE_STDLIB_ROMFS for section in sections)
    if romfs_count != 1:
        _fail("container must contain exactly one stdlib ROMFS")
    _validate_external_ranges(sections)
    _validate_overlay_ranges(
        sections, overlay_load_address, overlay_slot_size
    )


def _parse_stubs(value: object, sections: Sequence[Section]) -> Tuple[Stub, ...]:
    entries = _parse_array(value, "stubs")
    if not entries:
        _fail("container must contain at least one overlay stub mapping")
    if len(entries) > MAX_STUBS:
        _fail("stub count exceeds {}".format(MAX_STUBS))
    groups = {
        section.section_id: section
        for section in sections
        if section.section_type == TYPE_OVERLAY_GROUP
    }
    stubs: List[Stub] = []
    for index, value_entry in enumerate(entries):
        context = "stubs[{}]".format(index)
        if not isinstance(value_entry, dict):
            _fail("{} must be a JSON object".format(context))
        _check_keys(value_entry, ("id", "name", "group_id", "entry_offset"), (), context)
        stub_id = _parse_integer(value_entry["id"], "{}.id".format(context), UINT32_MAX)
        name = _parse_name(value_entry["name"], "{}.name".format(context))
        group_id = _parse_integer(value_entry["group_id"], "{}.group_id".format(context), UINT32_MAX)
        entry_offset = _parse_integer(value_entry["entry_offset"], "{}.entry_offset".format(context), UINT32_MAX)
        if group_id == 0:
            _fail(
                "{} maps to reserved resident group zero".format(context)
            )
        group = groups.get(group_id)
        if group is None:
            _fail("{} references unknown overlay group {}".format(context, group_id))
        if entry_offset & (P2_INSTRUCTION_SIZE - 1):
            _fail("{}.entry_offset must be four-byte aligned".format(context))
        if entry_offset > group.memory_size - P2_INSTRUCTION_SIZE:
            _fail("{}.entry_offset is outside overlay group {}".format(context, group_id))
        stubs.append(Stub(stub_id, group_id, entry_offset, 0, name))
    stubs.sort(key=lambda entry: entry.stub_id)
    if [entry.stub_id for entry in stubs] != list(range(len(stubs))):
        _fail("stub IDs must be contiguous from zero")
    if len({entry.name for entry in stubs}) != len(stubs):
        _fail("stub names must be unique")
    return tuple(stubs)


def _parse_pack_inputs(
    manifest_path: pathlib.Path,
) -> Tuple[bytes, int, int, Tuple[Section, ...], Tuple[Stub, ...]]:
    root = _load_input_manifest(manifest_path)
    _check_keys(
        root,
        (
            "format",
            "build_fingerprint",
            "overlay_slot_size",
            "initialized_globals",
            "zero_fill",
            "overlay_groups",
            "stubs",
            "stdlib_romfs",
        ),
        (),
        "input manifest",
    )
    if root["format"] != FORMAT_NAME:
        _fail("input manifest format must be {!r}".format(FORMAT_NAME))
    fingerprint = _parse_fingerprint(root["build_fingerprint"])
    overlay_slot_size = _parse_integer(
        root["overlay_slot_size"],
        "overlay_slot_size",
        P2_HUB_LOAD_LIMIT,
    )
    sections: List[Section] = []
    for index, entry in enumerate(_parse_array(root["initialized_globals"], "initialized_globals")):
        sections.append(
            _parse_section_payload(
                entry,
                TYPE_EXTERNAL_INIT,
                manifest_path,
                "initialized_globals[{}]".format(index),
            )
        )
    for index, entry in enumerate(_parse_array(root["zero_fill"], "zero_fill")):
        sections.append(_parse_zero_section(entry, "zero_fill[{}]".format(index)))
    for index, entry in enumerate(_parse_array(root["overlay_groups"], "overlay_groups")):
        sections.append(
            _parse_section_payload(
                entry,
                TYPE_OVERLAY_GROUP,
                manifest_path,
                "overlay_groups[{}]".format(index),
            )
        )
    sections.append(_parse_romfs(root["stdlib_romfs"], manifest_path, "stdlib_romfs"))
    sections.sort(key=lambda section: (section.section_type, section.section_id, section.name.encode("utf-8")))
    overlay_sections = [
        section
        for section in sections
        if section.section_type == TYPE_OVERLAY_GROUP
    ]
    if not overlay_sections:
        _fail("container must contain at least one overlay group")
    overlay_load_address = overlay_sections[0].virtual_address
    _validate_sections(sections, overlay_load_address, overlay_slot_size)
    stubs = _parse_stubs(root["stubs"], sections)
    all_names = [section.name for section in sections] + [stub.name for stub in stubs]
    if len(set(all_names)) != len(all_names):
        _fail("section and stub names must be globally unique")
    return (
        fingerprint,
        overlay_load_address,
        overlay_slot_size,
        tuple(sections),
        stubs,
    )


def _header_flags(sections: Sequence[Section], stubs: Sequence[Stub]) -> int:
    flags = 0
    if any(section.section_type == TYPE_EXTERNAL_INIT for section in sections):
        flags |= HEADER_FLAG_EXTERNAL_INIT
    if any(section.section_type == TYPE_EXTERNAL_ZERO for section in sections):
        flags |= HEADER_FLAG_EXTERNAL_ZERO
    if any(section.section_type == TYPE_OVERLAY_GROUP for section in sections):
        flags |= HEADER_FLAG_OVERLAYS
    if stubs:
        flags |= HEADER_FLAG_STUBS
    if any(section.section_type == TYPE_STDLIB_ROMFS for section in sections):
        flags |= HEADER_FLAG_STDLIB_ROMFS
    return flags


def _build_string_table(
    sections: Sequence[Section], stubs: Sequence[Stub]
) -> Tuple[bytes, Mapping[str, Tuple[int, int]]]:
    encoded_names = sorted(
        {name.encode("utf-8") for name in [section.name for section in sections] + [stub.name for stub in stubs]}
    )
    table = bytearray()
    positions: Dict[str, Tuple[int, int]] = {}
    for encoded in encoded_names:
        offset = len(table)
        table.extend(encoded)
        positions[encoded.decode("utf-8")] = (offset, len(encoded))
    return bytes(table), positions


def _assign_layout(
    sections: Sequence[Section], stubs: Sequence[Stub]
) -> Tuple[
    Tuple[Section, ...],
    int,
    int,
    int,
    int,
    int,
    int,
    bytes,
    Mapping[str, Tuple[int, int]],
]:
    section_table_offset = HEADER_SIZE
    section_table_size = _checked_mul(len(sections), SECTION_ENTRY_SIZE, "section table size")
    group_table_offset = _checked_add(section_table_offset, section_table_size, "group table offset")
    group_count = 1 + sum(
        section.section_type == TYPE_OVERLAY_GROUP for section in sections
    )
    group_table_size = _checked_mul(
        group_count, GROUP_ENTRY_SIZE, "group table size"
    )
    stub_table_offset = _checked_add(
        group_table_offset, group_table_size, "stub table offset"
    )
    stub_table_size = _checked_mul(len(stubs), STUB_ENTRY_SIZE, "stub table size")
    stub_name_table_offset = _checked_add(
        stub_table_offset, stub_table_size, "stub-name table offset"
    )
    stub_name_table_size = _checked_mul(
        len(stubs), STUB_NAME_ENTRY_SIZE, "stub-name table size"
    )
    string_table_offset = _checked_add(
        stub_name_table_offset, stub_name_table_size, "string table offset"
    )
    string_table, names = _build_string_table(sections, stubs)
    string_table_end = _checked_add(string_table_offset, len(string_table), "string table end")
    manifest_size = _align_up(string_table_end, CONTAINER_ALIGNMENT, "manifest alignment")
    if manifest_size > MAX_MANIFEST_SIZE:
        _fail("manifest exceeds {} bytes".format(MAX_MANIFEST_SIZE))
    cursor = manifest_size
    laid_out = []
    for section in sections:
        if not section.has_payload:
            laid_out.append(section)
            continue
        effective_alignment = max(CONTAINER_ALIGNMENT, section.alignment)
        offset = _align_up(cursor, effective_alignment, "{} file offset".format(section.name))
        cursor = _checked_add(offset, section.stored_size, "{} payload end".format(section.name), MAX_CONTAINER_SIZE)
        laid_out.append(dataclasses.replace(section, file_offset=offset))
    return (
        tuple(laid_out),
        section_table_offset,
        group_table_offset,
        stub_table_offset,
        stub_name_table_offset,
        string_table_offset,
        manifest_size,
        string_table,
        names,
    )


def _encode_section(section: Section, names: Mapping[str, Tuple[int, int]]) -> bytes:
    name_offset, name_length = names[section.name]
    return SECTION_STRUCT.pack(
        section.section_type,
        section.codec,
        section.flags,
        section.section_id,
        name_offset,
        name_length,
        section.alignment,
        0,
        section.virtual_address,
        section.file_offset,
        section.stored_size,
        section.memory_size,
        section.uncompressed_size,
        section.crc32,
        0,
        bytes(20),
    )


def _encode_group(section: Section) -> bytes:
    if section.file_offset > UINT32_MAX or section.uncompressed_size > UINT32_MAX:
        _fail("{} cannot be represented in the 32-bit runtime group table".format(section.name))
    return GROUP_STRUCT.pack(
        section.file_offset,
        section.uncompressed_size,
        section.crc32,
        section.flags,
    )


def _encode_stub(stub: Stub) -> bytes:
    if stub.entry_offset > UINT32_MAX:
        _fail("stub {} entry cannot be represented in the runtime table".format(stub.stub_id))
    return STUB_STRUCT.pack(stub.group_id, stub.entry_offset)


def _encode_stub_name(stub: Stub, names: Mapping[str, Tuple[int, int]]) -> bytes:
    name_offset, name_length = names[stub.name]
    return STUB_NAME_STRUCT.pack(name_offset, name_length)


def _encode_manifest(
    fingerprint: bytes,
    overlay_load_address: int,
    overlay_slot_size: int,
    sections: Sequence[Section],
    stubs: Sequence[Stub],
    section_table_offset: int,
    group_table_offset: int,
    stub_table_offset: int,
    stub_name_table_offset: int,
    string_table_offset: int,
    manifest_size: int,
    string_table: bytes,
    names: Mapping[str, Tuple[int, int]],
) -> Tuple[bytes, bytes]:
    file_size = manifest_size
    for section in sections:
        if section.has_payload:
            file_size = max(file_size, section.file_offset + section.stored_size)
    flags = _header_flags(sections, stubs)
    header = HEADER_STRUCT.pack(
        MAGIC,
        VERSION_MAJOR,
        VERSION_MINOR,
        HEADER_SIZE,
        SECTION_ENTRY_SIZE,
        GROUP_ENTRY_SIZE,
        STUB_ENTRY_SIZE,
        STUB_NAME_ENTRY_SIZE,
        0,
        flags,
        ENDIAN_TAG,
        len(sections),
        1 + sum(
            section.section_type == TYPE_OVERLAY_GROUP for section in sections
        ),
        len(stubs),
        0,
        section_table_offset,
        group_table_offset,
        stub_table_offset,
        stub_name_table_offset,
        string_table_offset,
        len(string_table),
        manifest_size,
        file_size,
        fingerprint,
        bytes(32),
        overlay_load_address,
        overlay_slot_size,
        bytes(8),
    )
    output = bytearray(header)
    for section in sections:
        output.extend(_encode_section(section, names))
    output.extend(GROUP_STRUCT.pack(0, 0, 0, 0))
    for section in sections:
        if section.section_type == TYPE_OVERLAY_GROUP:
            output.extend(_encode_group(section))
    for stub in stubs:
        output.extend(_encode_stub(stub))
    for stub in stubs:
        output.extend(_encode_stub_name(stub, names))
    output.extend(string_table)
    if len(output) > manifest_size:
        _fail("internal error: encoded manifest exceeds declared size")
    output.extend(bytes(manifest_size - len(output)))
    digest = hashlib.sha256(output).digest()
    output[MANIFEST_SHA256_OFFSET : MANIFEST_SHA256_OFFSET + 32] = digest
    return bytes(output), digest


def _write_zeros(stream: BinaryIO, count: int) -> None:
    block = bytes(min(IO_CHUNK_SIZE, max(1, count)))
    while count:
        amount = min(count, len(block))
        stream.write(block[:amount])
        count -= amount


def _copy_source(stream: BinaryIO, section: Section) -> None:
    assert section.source is not None
    checksum = 0
    copied = 0
    try:
        with section.source.open("rb") as source:
            while copied < section.stored_size:
                chunk = source.read(min(IO_CHUNK_SIZE, section.stored_size - copied))
                if not chunk:
                    _fail("{} payload became truncated while packing".format(section.name))
                stream.write(chunk)
                checksum = binascii.crc32(chunk, checksum)
                copied += len(chunk)
            if source.read(1):
                _fail("{} payload grew while packing".format(section.name))
    except ContainerError:
        raise
    except OSError as exc:
        _fail("cannot copy {} payload {}: {}".format(section.name, section.source, exc))
    if copied != section.stored_size or checksum & UINT32_MAX != section.crc32:
        _fail("{} payload changed after its manifest checksum was computed".format(section.name))


def _fsync_directory(path: pathlib.Path) -> None:
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def pack_container(manifest_path: pathlib.Path, output_path: pathlib.Path) -> Container:
    """Pack *manifest_path* into *output_path* and verify before replacement."""

    manifest_path = pathlib.Path(manifest_path).resolve()
    output_path = pathlib.Path(output_path).resolve()
    (
        fingerprint,
        overlay_load_address,
        overlay_slot_size,
        sections,
        stubs,
    ) = _parse_pack_inputs(manifest_path)
    source_paths = {section.source for section in sections if section.source is not None}
    if output_path == manifest_path or output_path in source_paths:
        _fail("output path must not replace the input manifest or a payload")
    parent = output_path.parent
    if not parent.is_dir():
        _fail("output directory does not exist: {}".format(parent))
    (
        sections,
        section_table_offset,
        group_table_offset,
        stub_table_offset,
        stub_name_table_offset,
        string_table_offset,
        manifest_size,
        string_table,
        names,
    ) = _assign_layout(sections, stubs)
    manifest, _digest = _encode_manifest(
        fingerprint,
        overlay_load_address,
        overlay_slot_size,
        sections,
        stubs,
        section_table_offset,
        group_table_offset,
        stub_table_offset,
        stub_name_table_offset,
        string_table_offset,
        manifest_size,
        string_table,
        names,
    )
    temporary_path: Optional[pathlib.Path] = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".{}.".format(output_path.name), suffix=".tmp", dir=str(parent)
        )
        temporary_path = pathlib.Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(manifest)
            cursor = len(manifest)
            for section in sections:
                if not section.has_payload:
                    continue
                if cursor > section.file_offset:
                    _fail("internal error: payload layout overlaps")
                _write_zeros(stream, section.file_offset - cursor)
                _copy_source(stream, section)
                cursor = section.file_offset + section.stored_size
            stream.flush()
            os.fsync(stream.fileno())
        verified = verify_container(temporary_path)
        os.replace(str(temporary_path), str(output_path))
        temporary_path = None
        _fsync_directory(parent)
        return dataclasses.replace(verified, path=output_path)
    except ContainerError:
        raise
    except OSError as exc:
        _fail("cannot atomically write {}: {}".format(output_path, exc))
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _read_exact(stream: BinaryIO, count: int, context: str) -> bytes:
    data = stream.read(count)
    if len(data) != count:
        _fail("{} is truncated".format(context))
    return data


def _decode_name(string_table: bytes, offset: int, length: int, context: str) -> str:
    if length == 0 or length > MAX_NAME_BYTES:
        _fail("{} name has invalid length {}".format(context, length))
    end = _checked_add(offset, length, "{} name range".format(context), len(string_table))
    if end > len(string_table):
        _fail("{} name is outside the string table".format(context))
    encoded = string_table[offset:end]
    try:
        name = encoded.decode("utf-8")
    except UnicodeDecodeError:
        _fail("{} name is not valid UTF-8".format(context))
    if _parse_name(name, "{} name".format(context)) != name:
        raise AssertionError("unreachable")
    return name


def _manifest_digest(raw: bytes) -> bytes:
    canonical = bytearray(raw)
    canonical[MANIFEST_SHA256_OFFSET : MANIFEST_SHA256_OFFSET + 32] = bytes(32)
    return hashlib.sha256(canonical).digest()


def _decode_sections(
    manifest: bytes,
    count: int,
    table_offset: int,
    string_table: bytes,
) -> Tuple[Section, ...]:
    sections = []
    for index in range(count):
        offset = table_offset + index * SECTION_ENTRY_SIZE
        fields = SECTION_STRUCT.unpack_from(manifest, offset)
        (
            section_type,
            codec,
            flags,
            section_id,
            name_offset,
            name_length,
            alignment,
            reserved0,
            virtual_address,
            file_offset,
            stored_size,
            memory_size,
            uncompressed_size,
            checksum,
            reserved1,
            reserved_tail,
        ) = fields
        context = "section[{}]".format(index)
        if reserved0 or reserved1 or reserved_tail != bytes(20):
            _fail("{} has nonzero reserved fields".format(context))
        name = _decode_name(string_table, name_offset, name_length, context)
        sections.append(
            Section(
                section_type,
                codec,
                flags,
                section_id,
                name,
                alignment,
                virtual_address,
                file_offset,
                stored_size,
                memory_size,
                uncompressed_size,
                checksum,
            )
        )
    expected = sorted(
        sections,
        key=lambda section: (
            section.section_type,
            section.section_id,
            section.name.encode("utf-8"),
        ),
    )
    if sections != expected:
        _fail("section table is not in canonical type/id/name order")
    return tuple(sections)


def _decode_group_records(
    manifest: bytes, count: int, table_offset: int
) -> Tuple[Tuple[int, int, int, int], ...]:
    records = []
    for index in range(count):
        offset = table_offset + index * GROUP_ENTRY_SIZE
        records.append(GROUP_STRUCT.unpack_from(manifest, offset))
    return tuple(records)


def _decode_stubs(
    manifest: bytes,
    count: int,
    table_offset: int,
    name_table_offset: int,
    string_table: bytes,
) -> Tuple[Stub, ...]:
    stubs = []
    for index in range(count):
        offset = table_offset + index * STUB_ENTRY_SIZE
        group_id, entry_offset = STUB_STRUCT.unpack_from(manifest, offset)
        name_offset, name_length = STUB_NAME_STRUCT.unpack_from(
            manifest, name_table_offset + index * STUB_NAME_ENTRY_SIZE
        )
        context = "stub[{}]".format(index)
        name = _decode_name(string_table, name_offset, name_length, context)
        stubs.append(Stub(index, group_id, entry_offset, 0, name))
    if len({stub.name for stub in stubs}) != len(stubs):
        _fail("stub names must be unique")
    return tuple(stubs)


def _validate_group_records(
    records: Sequence[Tuple[int, int, int, int]], sections: Sequence[Section]
) -> None:
    groups = [
        section
        for section in sections
        if section.section_type == TYPE_OVERLAY_GROUP
    ]
    if len(records) != len(groups) + 1:
        _fail("runtime group table count does not match overlay sections")
    if not records or records[0] != (0, 0, 0, 0):
        _fail("runtime group record zero must be the reserved resident entry")
    for index, (record, section) in enumerate(zip(records[1:], groups), start=1):
        source, size, checksum, flags = record
        expected = (
            section.file_offset,
            section.uncompressed_size,
            section.crc32,
            section.flags,
        )
        if record != expected:
            _fail(
                "runtime group record {} does not match overlay section {}: "
                "observed {!r}, expected {!r}".format(
                    index, section.name, (source, size, checksum, flags), expected
                )
            )


def _validate_string_table(
    string_table: bytes, sections: Sequence[Section], stubs: Sequence[Stub]
) -> None:
    expected, expected_positions = _build_string_table(sections, stubs)
    if string_table != expected:
        _fail("string table is not the canonical sorted unique encoding")
    # Re-encoding entries below also proves each decoded name selected the
    # canonical offset rather than an overlapping substring.
    for name in [section.name for section in sections] + [stub.name for stub in stubs]:
        if name not in expected_positions:
            _fail("name {!r} is absent from the canonical string table".format(name))


def _validate_name_references(
    manifest: bytes,
    sections: Sequence[Section],
    stubs: Sequence[Stub],
    section_table_offset: int,
    stub_name_table_offset: int,
) -> None:
    _table, positions = _build_string_table(sections, stubs)
    for index, section in enumerate(sections):
        fields = SECTION_STRUCT.unpack_from(
            manifest, section_table_offset + index * SECTION_ENTRY_SIZE
        )
        observed = (fields[4], fields[5])
        if observed != positions[section.name]:
            _fail(
                "section[{}] name does not use its canonical string-table range".format(
                    index
                )
            )
    for index, stub in enumerate(stubs):
        observed = STUB_NAME_STRUCT.unpack_from(
            manifest, stub_name_table_offset + index * STUB_NAME_ENTRY_SIZE
        )
        if observed != positions[stub.name]:
            _fail(
                "stub[{}] name does not use its canonical string-table range".format(
                    index
                )
            )


def _validate_stub_targets(stubs: Sequence[Stub], sections: Sequence[Section]) -> None:
    groups = {
        section.section_id: section
        for section in sections
        if section.section_type == TYPE_OVERLAY_GROUP
    }
    if not stubs:
        _fail("container must contain at least one overlay stub mapping")
    for stub in stubs:
        if stub.group_id == 0:
            _fail(
                "stub {} maps to reserved resident group zero".format(
                    stub.stub_id
                )
            )
        group = groups.get(stub.group_id)
        if group is None:
            _fail("stub {} references unknown overlay group {}".format(stub.stub_id, stub.group_id))
        if stub.entry_offset & (P2_INSTRUCTION_SIZE - 1):
            _fail("stub {} entry offset is not four-byte aligned".format(stub.stub_id))
        if stub.entry_offset > group.memory_size - P2_INSTRUCTION_SIZE:
            _fail("stub {} entry offset is outside overlay group {}".format(stub.stub_id, stub.group_id))


def _check_zero_range(stream: BinaryIO, start: int, end: int, context: str) -> None:
    if end < start:
        _fail("{} has a reversed range".format(context))
    stream.seek(start)
    remaining = end - start
    while remaining:
        chunk = _read_exact(stream, min(IO_CHUNK_SIZE, remaining), context)
        if any(chunk):
            _fail("{} contains nonzero padding".format(context))
        remaining -= len(chunk)


def _validate_payload_layout(
    stream: BinaryIO,
    sections: Sequence[Section],
    manifest_size: int,
    file_size: int,
) -> None:
    file_sections = [section for section in sections if section.has_payload]
    ranges = []
    for section in file_sections:
        end = _checked_add(section.file_offset, section.stored_size, "{} file range".format(section.name), file_size)
        if section.file_offset < manifest_size or end > file_size:
            _fail("{} payload is outside the container".format(section.name))
        ranges.append((section.file_offset, end, section.name))
    for previous, current in zip(sorted(ranges), sorted(ranges)[1:]):
        if current[0] < previous[1]:
            _fail("payload sections {!r} and {!r} overlap".format(previous[2], current[2]))
    cursor = manifest_size
    for section in file_sections:
        expected = _align_up(cursor, max(CONTAINER_ALIGNMENT, section.alignment), "{} canonical file offset".format(section.name))
        if section.file_offset != expected:
            _fail("{} has non-canonical file offset 0x{:x}; expected 0x{:x}".format(section.name, section.file_offset, expected))
        _check_zero_range(stream, cursor, expected, "padding before {}".format(section.name))
        cursor = section.file_offset + section.stored_size
    if cursor != file_size:
        _fail("file size does not end at the final canonical payload")


def _verify_payload_crc(stream: BinaryIO, sections: Sequence[Section]) -> None:
    for section in sections:
        if not section.has_payload:
            continue
        stream.seek(section.file_offset)
        checksum = _crc32_stream(stream, section.stored_size, "{} payload".format(section.name))
        if checksum != section.crc32:
            _fail("{} payload CRC32 mismatch: stored {:08x}, computed {:08x}".format(section.name, section.crc32, checksum))


def verify_container(path: pathlib.Path, verify_payloads: bool = True) -> Container:
    """Validate *path* fail-closed and return its decoded canonical metadata."""

    path = pathlib.Path(path).resolve()
    try:
        with path.open("rb") as stream:
            actual_size = os.fstat(stream.fileno()).st_size
            if actual_size < HEADER_SIZE:
                _fail("container is truncated before its fixed header")
            if actual_size > MAX_CONTAINER_SIZE:
                _fail("container exceeds the 32-bit target file-size limit")
            header_bytes = _read_exact(stream, HEADER_SIZE, "container header")
            fields = HEADER_STRUCT.unpack(header_bytes)
            (
                magic,
                version_major,
                version_minor,
                header_size,
                section_entry_size,
                group_entry_size,
                stub_entry_size,
                stub_name_entry_size,
                reserved16,
                flags,
                endian_tag,
                section_count,
                group_count,
                stub_count,
                reserved32,
                section_table_offset,
                group_table_offset,
                stub_table_offset,
                stub_name_table_offset,
                string_table_offset,
                string_table_size,
                manifest_size,
                file_size,
                fingerprint,
                stored_manifest_digest,
                overlay_load_address,
                overlay_slot_size,
                reserved_tail,
            ) = fields
            if magic != MAGIC:
                _fail("container magic is invalid")
            if (version_major, version_minor) != (VERSION_MAJOR, VERSION_MINOR):
                _fail("unsupported container version {}.{}".format(version_major, version_minor))
            if (
                header_size != HEADER_SIZE
                or section_entry_size != SECTION_ENTRY_SIZE
                or group_entry_size != GROUP_ENTRY_SIZE
                or stub_entry_size != STUB_ENTRY_SIZE
                or stub_name_entry_size != STUB_NAME_ENTRY_SIZE
            ):
                _fail("container ABI entry sizes do not match version 1")
            if reserved16 or reserved32 or reserved_tail != bytes(8):
                _fail("container header has nonzero reserved fields")
            if flags & ~HEADER_FLAG_MASK:
                _fail("container header has unknown flag bits 0x{:x}".format(flags & ~HEADER_FLAG_MASK))
            if endian_tag != ENDIAN_TAG:
                _fail("container endian tag is invalid")
            if not fingerprint or fingerprint == bytes(32):
                _fail("container build fingerprint is all zero")
            if section_count > MAX_SECTIONS or group_count > MAX_SECTIONS or stub_count > MAX_STUBS:
                _fail("container table count exceeds the host validation limit")
            if file_size != actual_size:
                _fail("declared file size {} does not equal actual size {}".format(file_size, actual_size))
            expected_section_offset = HEADER_SIZE
            section_bytes = _checked_mul(section_count, SECTION_ENTRY_SIZE, "section table size", MAX_MANIFEST_SIZE)
            expected_group_offset = _checked_add(expected_section_offset, section_bytes, "group table offset", MAX_MANIFEST_SIZE)
            group_bytes = _checked_mul(group_count, GROUP_ENTRY_SIZE, "group table size", MAX_MANIFEST_SIZE)
            expected_stub_offset = _checked_add(expected_group_offset, group_bytes, "stub table offset", MAX_MANIFEST_SIZE)
            stub_bytes = _checked_mul(stub_count, STUB_ENTRY_SIZE, "stub table size", MAX_MANIFEST_SIZE)
            expected_stub_name_offset = _checked_add(expected_stub_offset, stub_bytes, "stub-name table offset", MAX_MANIFEST_SIZE)
            stub_name_bytes = _checked_mul(stub_count, STUB_NAME_ENTRY_SIZE, "stub-name table size", MAX_MANIFEST_SIZE)
            expected_string_offset = _checked_add(expected_stub_name_offset, stub_name_bytes, "string table offset", MAX_MANIFEST_SIZE)
            expected_string_end = _checked_add(expected_string_offset, string_table_size, "string table end", MAX_MANIFEST_SIZE)
            expected_manifest_size = _align_up(expected_string_end, CONTAINER_ALIGNMENT, "manifest size")
            if (
                section_table_offset != expected_section_offset
                or group_table_offset != expected_group_offset
                or stub_table_offset != expected_stub_offset
                or stub_name_table_offset != expected_stub_name_offset
                or string_table_offset != expected_string_offset
                or manifest_size != expected_manifest_size
            ):
                _fail("container manifest tables are not in canonical contiguous layout")
            if manifest_size > MAX_MANIFEST_SIZE or manifest_size > file_size:
                _fail("declared manifest size is outside the container")
            stream.seek(0)
            manifest = _read_exact(stream, manifest_size, "container manifest")
            computed_manifest_digest = _manifest_digest(manifest)
            if computed_manifest_digest != stored_manifest_digest:
                _fail("container manifest SHA-256 mismatch")
            if any(manifest[expected_string_end:manifest_size]):
                _fail("container manifest contains nonzero alignment padding")
            string_table = manifest[string_table_offset:expected_string_end]
            sections = _decode_sections(manifest, section_count, section_table_offset, string_table)
            group_records = _decode_group_records(
                manifest, group_count, group_table_offset
            )
            stubs = _decode_stubs(
                manifest,
                stub_count,
                stub_table_offset,
                stub_name_table_offset,
                string_table,
            )
            _validate_string_table(string_table, sections, stubs)
            _validate_name_references(
                manifest,
                sections,
                stubs,
                section_table_offset,
                stub_name_table_offset,
            )
            _validate_sections(
                sections, overlay_load_address, overlay_slot_size
            )
            _validate_group_records(group_records, sections)
            if len({section.name for section in sections} | {stub.name for stub in stubs}) != len(sections) + len(stubs):
                _fail("section and stub names must be globally unique")
            _validate_stub_targets(stubs, sections)
            expected_flags = _header_flags(sections, stubs)
            if flags != expected_flags:
                _fail("container header presence flags do not match its tables")
            _validate_payload_layout(stream, sections, manifest_size, file_size)
            if verify_payloads:
                _verify_payload_crc(stream, sections)
            if os.fstat(stream.fileno()).st_size != actual_size:
                _fail("container size changed during verification")
    except ContainerError:
        raise
    except OSError as exc:
        _fail("cannot verify container {}: {}".format(path, exc))
    return Container(
        path=path,
        flags=flags,
        file_size=file_size,
        manifest_size=manifest_size,
        overlay_load_address=overlay_load_address,
        overlay_slot_size=overlay_slot_size,
        build_fingerprint=fingerprint,
        manifest_sha256=stored_manifest_digest,
        sections=sections,
        stubs=stubs,
    )


def container_listing(container: Container) -> Mapping[str, object]:
    return {
        "format": "p2-python-container-v1",
        "version": "{}.{}".format(VERSION_MAJOR, VERSION_MINOR),
        "path": str(container.path),
        "file_size": container.file_size,
        "manifest_size": container.manifest_size,
        "overlay_load_address": "0x{:08x}".format(
            container.overlay_load_address
        ),
        "overlay_slot_size": container.overlay_slot_size,
        "build_fingerprint": container.build_fingerprint.hex(),
        "manifest_sha256": container.manifest_sha256.hex(),
        "sections": [
            {
                "type": SECTION_TYPE_NAMES[section.section_type],
                "id": section.section_id,
                "name": section.name,
                "codec": CODEC_NAMES[section.codec],
                "flags": [
                    name
                    for name, bit in SECTION_FLAG_NAMES.items()
                    if section.flags & bit
                ],
                "alignment": section.alignment,
                "virtual_address": "0x{:08x}".format(section.virtual_address),
                "file_offset": section.file_offset,
                "stored_size": section.stored_size,
                "memory_size": section.memory_size,
                "uncompressed_size": section.uncompressed_size,
                "crc32": "{:08x}".format(section.crc32),
            }
            for section in container.sections
        ],
        "stubs": [
            {
                "id": stub.stub_id,
                "name": stub.name,
                "group_id": stub.group_id,
                "entry_offset": stub.entry_offset,
            }
            for stub in container.stubs
        ],
    }


def _command_pack(args: argparse.Namespace) -> int:
    container = pack_container(args.manifest, args.output)
    print(
        "HOST-VERIFIED P2 Python container: {} ({} bytes, {} sections, {} stubs)".format(
            container.path,
            container.file_size,
            len(container.sections),
            len(container.stubs),
        )
    )
    return 0


def _command_verify(args: argparse.Namespace) -> int:
    container = verify_container(args.container)
    print(
        "HOST-VERIFIED P2 Python container: {} SHA256={}".format(
            container.path, container.manifest_sha256.hex()
        )
    )
    return 0


def _command_list(args: argparse.Namespace) -> int:
    container = verify_container(args.container)
    print(json.dumps(container_listing(container), indent=2, sort_keys=True))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    pack = subparsers.add_parser("pack", help="pack a JSON manifest")
    pack.add_argument("manifest", type=pathlib.Path)
    pack.add_argument("output", type=pathlib.Path)
    pack.set_defaults(handler=_command_pack)
    verify = subparsers.add_parser("verify", help="verify a container")
    verify.add_argument("container", type=pathlib.Path)
    verify.set_defaults(handler=_command_verify)
    list_parser = subparsers.add_parser("list", help="verify and list container metadata")
    list_parser.add_argument("container", type=pathlib.Path)
    list_parser.set_defaults(handler=_command_list)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except ContainerError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    sys.exit(main())
