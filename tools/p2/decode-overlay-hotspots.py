#!/usr/bin/env python3
#
# SPDX-License-Identifier: Apache-2.0
"""Decode bounded P2 overlay HOT telemetry with an LLD link map.

The resident runtime intentionally records numeric identities only.  This
keeps the on-board profiler bounded and independent of debug strings.  This
tool joins those records with the matching ``nuttx.map`` so a caller group and
call-site offset resolve to an overlay body, while a target stub index resolves
to the public function name retained in the resident veneer table.

Input is strict: fixed-width uppercase hexadecimal telemetry is part of the
HIL evidence ABI.  Interspersed serial lines are ignored, but a malformed HOT
line, incomplete snapshot, duplicate transport ordinal, or map identity
mismatch fails the
decode rather than producing a plausible-looking profile.  The command-line
interface additionally requires the owning build and HIL status documents so
the map, artifacts, upload receipt, and raw serial length are cryptographically
and structurally bound before any decoded output is emitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


HOT_CAPACITY = 8
MAX_OVERLAY_GROUPS = 1024
UINT64_MAX = (1 << 64) - 1

HEADER_RE = re.compile(rb"^P2PY:HOT:(SAMPLE|FINAL):N=([0-9A-F]{2}):T=([0-9A-F]{16})$")
ENTRY_RE = re.compile(
    rb"^P2PY:HOT:(SAMPLE|FINAL):R=([0-9A-F]{2})"
    rb":CG=([0-9A-F]{8}):CO=([0-9A-F]{8})"
    rb":TG=([0-9A-F]{8}):TS=([0-9A-F]{8})"
    rb":C=([0-9A-F]{16}):E=([0-9A-F]{16})$"
)
ERROR_RE = re.compile(rb"^P2PY:HOT:(SAMPLE|FINAL):ERROR=(-[1-9][0-9]*)$")
UPLOAD_PASS_RE = re.compile(
    rb"^P2PY:UPLOAD:PASS:SIZE=(0|[1-9][0-9]*):" rb"CRC=([0-9A-F]{8}):RXDROPS=0$"
)
MAP_ROW_RE = re.compile(
    r"^\s*([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+" r"([0-9a-fA-F]+)\s+([0-9]+)\s+(.+?)\s*$"
)
GROUP_SECTION_RE = re.compile(r"^\.p2\.overlay\.group\.([0-9a-fA-F]{8})$")
BODY_RE = re.compile(r"^__p2_ovlbody\.([0-9]+)\.(.+)$")
OUTPUT_SECTION_RE = re.compile(r"^\.[A-Za-z0-9_.-]+$")
ASSIGNMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$")
INTEGER_RE = re.compile(r"^(?:0[xX][0-9a-fA-F]+|[0-9]+)$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CRC32_RE = re.compile(r"^[0-9A-F]{8}$")

EXECUTABLE_OUTPUT_RE = re.compile(
    r"^(?:\.text(?:\..*)?|\.init(?:\..*)?|\.fini(?:\..*)?|\.ramfunc(?:\..*)?|"
    r"\.vectors(?:\..*)?|\.p2\.(?:entry|cog|lut))$"
)


class DecodeError(ValueError):
    """An evidence or map invariant failed."""


@dataclass(frozen=True)
class Symbol:
    address: int
    size: int
    name: str

    @property
    def end(self) -> int:
        return self.address + self.size


@dataclass(frozen=True)
class OverlayGroup:
    group: int
    address: int
    size: int
    symbols: Tuple[Symbol, ...]

    @property
    def end(self) -> int:
        return self.address + self.size


@dataclass(frozen=True)
class MapIndex:
    slot_start: int
    slot_end: int
    stubs_start: int
    stubs_end: int
    group_count: int
    groups: Dict[int, OverlayGroup]
    stubs: Dict[int, str]
    resident: Tuple[Symbol, ...]


@dataclass(frozen=True)
class HotEntry:
    stage: str
    rank: int
    caller_group: int
    caller_offset: int
    target_group: int
    target_stub: int
    count: int
    error: int


@dataclass(frozen=True)
class HotSnapshot:
    stage: str
    total: int
    entries: Tuple[HotEntry, ...]


@dataclass(frozen=True)
class DecodedEntry:
    stage: str
    rank: int
    caller_group: int
    caller_offset: int
    caller: str
    caller_function_offset: Optional[int]
    target_group: int
    target_stub: int
    target: str
    count: int
    error: int
    lower_bound: int


@dataclass(frozen=True)
class DecodedSnapshot:
    stage: str
    total: int
    entries: Tuple[DecodedEntry, ...]


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise DecodeError(f"{path}: {exc.strerror}") from exc


def _unique_json_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    output: Dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise DecodeError(f"JSON object has duplicate key {key!r}")
        output[key] = value
    return output


def _read_json(path: Path, description: str) -> Mapping[str, Any]:
    data = _read(path)
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise DecodeError(f"{description} is not valid UTF-8") from exc
    try:
        value = json.loads(text, object_pairs_hook=_unique_json_object)
    except DecodeError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise DecodeError(f"invalid {description}: {exc}") from exc
    if not isinstance(value, dict):
        raise DecodeError(f"{description} root is not an object")
    return value


def _require_mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise DecodeError(f"{description} is not an object")
    return value


def _require_string(value: Any, description: str) -> str:
    if not isinstance(value, str):
        raise DecodeError(f"{description} is not a string")
    return value


def _require_int(value: Any, description: str) -> int:
    if type(value) is not int or value < 0:
        raise DecodeError(f"{description} is not a non-negative integer")
    return value


def _require_sha256(value: Any, description: str) -> str:
    text = _require_string(value, description)
    if SHA256_RE.fullmatch(text) is None:
        raise DecodeError(f"{description} is not lowercase SHA-256")
    return text


def _build_file(files: Mapping[str, Any], name: str) -> Tuple[str, int]:
    try:
        raw = files[name]
    except KeyError as exc:
        raise DecodeError(f"build status is missing files[{name!r}]") from exc
    entry = _require_mapping(raw, f"build files[{name!r}]")
    try:
        digest = _require_sha256(entry["sha256"], f"build files[{name!r}].sha256")
        size = _require_int(entry["size"], f"build files[{name!r}].size")
    except KeyError as exc:
        raise DecodeError(f"build files[{name!r}] is missing {exc.args[0]!r}") from exc
    return digest, size


def validate_evidence_binding(
    map_data: bytes,
    serial_data: bytes,
    build_status: Mapping[str, Any],
    hil_status: Mapping[str, Any],
) -> None:
    """Bind a raw HOT log to the exact map and HIL artifact identities."""

    if build_status.get("format") != "p2-build-artifact-v1":
        raise DecodeError("build status format is not p2-build-artifact-v1")
    if build_status.get("status") != "PASS":
        raise DecodeError("build status is not PASS")

    files = _require_mapping(build_status.get("files"), "build status files")
    map_sha, map_size = _build_file(files, "nuttx.map")
    actual_map_sha = hashlib.sha256(map_data).hexdigest()
    if map_size != len(map_data) or map_sha != actual_map_sha:
        raise DecodeError("passed map does not match build status files['nuttx.map']")

    build_artifacts = {
        "resident ELF": _build_file(files, "nuttx"),
        "image": _build_file(files, "nuttx.bin"),
        "container": _build_file(files, "nuttx.p2py"),
    }
    if build_status.get("elf_sha256") != build_artifacts["resident ELF"][0]:
        raise DecodeError("build elf_sha256 disagrees with files['nuttx']")
    if build_status.get("binary_sha256") != build_artifacts["image"][0]:
        raise DecodeError("build binary_sha256 disagrees with files['nuttx.bin']")

    hil_format = hil_status.get("format")
    if (
        not isinstance(hil_format, str)
        or re.fullmatch(r"p2-python-hil(?:-(?:smoke|overnight))?-v1", hil_format)
        is None
    ):
        raise DecodeError("HIL status has an unsupported format")
    if hil_status.get("status") not in {"PASS", "SMOKE_PASS", "FAIL"}:
        raise DecodeError("HIL status has an invalid status value")

    inputs = _require_mapping(hil_status.get("inputs"), "HIL status inputs")
    hil_fields = {
        "resident ELF": ("resident_elf_sha256", "resident_elf_size"),
        "image": ("image_sha256", "image_size"),
        "container": ("container_sha256", "container_size"),
    }
    for artifact, (sha_field, size_field) in hil_fields.items():
        try:
            hil_sha = _require_sha256(inputs[sha_field], f"HIL inputs.{sha_field}")
            hil_size = _require_int(inputs[size_field], f"HIL inputs.{size_field}")
        except KeyError as exc:
            raise DecodeError(f"HIL inputs is missing {exc.args[0]!r}") from exc
        if (hil_sha, hil_size) != build_artifacts[artifact]:
            raise DecodeError(
                f"{artifact} hash/size differs between build and HIL status"
            )

    try:
        serial_rx_bytes = _require_int(
            hil_status["serial_rx_bytes"], "HIL serial_rx_bytes"
        )
    except KeyError as exc:
        raise DecodeError("HIL status is missing serial_rx_bytes") from exc
    if serial_rx_bytes != len(serial_data):
        raise DecodeError(
            f"serial log has {len(serial_data)} bytes, HIL status records "
            f"{serial_rx_bytes}"
        )

    try:
        container_size = _require_int(
            inputs["container_size"], "HIL inputs.container_size"
        )
        container_crc = _require_string(
            inputs["container_crc32"], "HIL inputs.container_crc32"
        )
    except KeyError as exc:
        raise DecodeError(f"HIL inputs is missing {exc.args[0]!r}") from exc
    if CRC32_RE.fullmatch(container_crc) is None:
        raise DecodeError("HIL inputs.container_crc32 is not uppercase CRC-32")

    upload_count = 0
    for line_number, raw_line in enumerate(serial_data.splitlines(), 1):
        line = raw_line.rstrip(b"\r")
        if not line.startswith(b"P2PY:UPLOAD:PASS:"):
            continue
        match = UPLOAD_PASS_RE.fullmatch(line)
        if match is None:
            raise DecodeError(
                f"serial line {line_number}: malformed upload PASS record"
            )
        upload_count += 1
        upload_size = int(match.group(1), 10)
        upload_crc = match.group(2).decode("ascii")
        if upload_size != container_size or upload_crc != container_crc:
            raise DecodeError(
                f"serial line {line_number}: upload PASS size/CRC does not "
                "match HIL container inputs"
            )

    if upload_count == 0:
        raise DecodeError("serial evidence has no exact P2PY:UPLOAD:PASS record")


def _clean_body_name(name: str) -> str:
    match = BODY_RE.fullmatch(name)
    return match.group(2) if match else name


def _is_symbol_name(name: str) -> bool:
    return (
        bool(name)
        and not name.startswith("/")
        and not name.startswith("LONG (")
        and " = " not in name
        and ":(" not in name
        and not name.startswith("*(")
    )


def _evaluate_map_assignment(
    name: str,
    assignments: Mapping[str, Tuple[str, int, int]],
    active: Tuple[str, ...] = (),
) -> int:
    if name in active:
        raise DecodeError(
            "cyclic link-map assignment: " + " -> ".join(active + (name,))
        )
    try:
        expression, location, line_number = assignments[name]
    except KeyError as exc:
        raise DecodeError(f"link map assignment {name} is unresolved") from exc

    def evaluate(expression: str) -> int:
        expression = expression.strip()
        if expression.startswith("ABSOLUTE (") and expression.endswith(")"):
            return evaluate(expression[len("ABSOLUTE (") : -1])
        if expression.startswith("(") and expression.endswith(")"):
            return evaluate(expression[1:-1])
        if expression == ".":
            return location
        if INTEGER_RE.fullmatch(expression):
            return int(expression, 0)

        for operator in (" + ", " - "):
            if operator in expression:
                left, right = expression.rsplit(operator, 1)
                left_value = evaluate(left)
                right_value = evaluate(right)
                return (
                    left_value + right_value
                    if operator.strip() == "+"
                    else left_value - right_value
                )

        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expression):
            return _evaluate_map_assignment(expression, assignments, active + (name,))
        raise DecodeError(
            f"map line {line_number}: unsupported assignment for {name}: "
            f"{expression!r}"
        )

    value = evaluate(expression)
    if value < 0:
        raise DecodeError(f"map line {line_number}: {name} is negative")
    return value


def parse_map(data: bytes) -> MapIndex:
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise DecodeError("link map is not valid UTF-8") from exc

    current_output = ""
    current_group: Optional[int] = None
    assignments: Dict[str, Tuple[str, int, int]] = {}
    group_sections: Dict[int, Tuple[int, int, int]] = {}
    group_symbols: Dict[int, List[Symbol]] = {}
    stubs_section: Optional[Tuple[int, int, int]] = None
    stub_symbols: List[Symbol] = []
    resident_candidates: List[Symbol] = []

    for line_number, line in enumerate(text.splitlines(), 1):
        match = MAP_ROW_RE.match(line)
        if match is None:
            continue
        address = int(match.group(1), 16)
        size = int(match.group(3), 16)
        name = match.group(5)

        assignment_match = ASSIGNMENT_RE.fullmatch(name)
        if assignment_match:
            assignment = assignment_match.group(1)
            if assignment in assignments:
                raise DecodeError(
                    f"map line {line_number}: duplicate assignment {assignment}"
                )

            assignments[assignment] = (
                assignment_match.group(2),
                address,
                line_number,
            )
            continue

        group_match = GROUP_SECTION_RE.fullmatch(name)
        if group_match:
            current_output = name
            current_group = int(group_match.group(1), 16)
            if current_group == 0:
                raise DecodeError(f"map line {line_number}: overlay group zero")
            if current_group in group_sections:
                raise DecodeError(
                    f"map line {line_number}: duplicate overlay group "
                    f"{current_group}"
                )

            group_sections[current_group] = (address, size, line_number)
            group_symbols[current_group] = []
            continue

        if OUTPUT_SECTION_RE.fullmatch(name):
            current_output = name
            current_group = None
            if name == ".p2.overlay.stubs":
                if stubs_section is not None:
                    raise DecodeError(
                        f"map line {line_number}: duplicate .p2.overlay.stubs"
                    )

                stubs_section = (address, size, line_number)
            continue

        if not _is_symbol_name(name) or size == 0:
            continue

        symbol = Symbol(address, size, _clean_body_name(name))
        if current_group is not None and BODY_RE.fullmatch(name):
            group_symbols[current_group].append(symbol)
        elif current_output == ".p2.overlay.stubs" and size == 4:
            stub_symbols.append(symbol)
        elif EXECUTABLE_OUTPUT_RE.fullmatch(current_output):
            resident_candidates.append(symbol)

    required_boundaries = {
        "__p2_overlay_stubs_start",
        "__p2_overlay_stubs_end",
        "__p2_overlay_slot_start",
        "__p2_overlay_slot_end",
    }
    missing_boundaries = sorted(required_boundaries - set(assignments))
    if missing_boundaries:
        raise DecodeError("link map is missing " + ", ".join(missing_boundaries))

    if "__p2_overlay_group_count" not in assignments:
        raise DecodeError("link map is missing __p2_overlay_group_count")
    group_count = _evaluate_map_assignment("__p2_overlay_group_count", assignments)
    if group_count < 2 or group_count > MAX_OVERLAY_GROUPS:
        raise DecodeError(
            f"link map overlay group count {group_count} is outside 2.."
            f"{MAX_OVERLAY_GROUPS}"
        )
    if not group_sections:
        raise DecodeError("link map has no .p2.overlay.group.GGGGGGGG output")

    expected_groups = set(range(1, group_count))
    actual_groups = set(group_sections)
    if actual_groups != expected_groups:
        missing = sorted(expected_groups - actual_groups)
        extra = sorted(actual_groups - expected_groups)
        details = []
        if missing:
            details.append("missing " + ", ".join(str(group) for group in missing[:16]))
        if extra:
            details.append("extra " + ", ".join(str(group) for group in extra[:16]))
        raise DecodeError(
            f"overlay group outputs do not match count {group_count}: "
            + "; ".join(details)
        )

    # LLD prints the current location, not the assigned value, in the first
    # VMA column for aliases such as ``slot_end = P2_HUB_END``.  Evaluate the
    # right-hand expression for exact boundaries.  slot_start is the one
    # exception in the current linker script: its expression contains a
    # command-line ``--defsym`` omitted from the map, while the assignment is
    # intentionally emitted at that exact location.

    slot_start = assignments["__p2_overlay_slot_start"][1]
    stubs_start = _evaluate_map_assignment("__p2_overlay_stubs_start", assignments)
    stubs_end = _evaluate_map_assignment("__p2_overlay_stubs_end", assignments)
    slot_end = _evaluate_map_assignment("__p2_overlay_slot_end", assignments)
    if slot_start >= slot_end or (slot_start & 3) != 0 or (slot_end & 3) != 0:
        raise DecodeError(
            f"invalid overlay slot boundary 0x{slot_start:x}..0x{slot_end:x}"
        )

    if stubs_section is None:
        raise DecodeError("link map has no .p2.overlay.stubs output")
    section_start, section_size, section_line = stubs_section
    if (
        stubs_start >= stubs_end
        or (stubs_start & 3) != 0
        or (stubs_end & 3) != 0
        or section_start != stubs_start
        or section_size != stubs_end - stubs_start
    ):
        raise DecodeError(
            f"map line {section_line}: .p2.overlay.stubs boundary/size "
            "does not match __p2_overlay_stubs_start/end"
        )
    if not stub_symbols:
        raise DecodeError("link map has no four-byte .p2.overlay.stubs symbols")

    stubs: Dict[int, str] = {}
    for symbol in stub_symbols:
        delta = symbol.address - stubs_start
        if delta < 0 or delta % 4 or symbol.end > stubs_end:
            raise DecodeError(
                f"stub {symbol.name!r} at 0x{symbol.address:x} is not indexed "
                f"inside 0x{stubs_start:x}..0x{stubs_end:x}"
            )
        index = delta // 4
        if index in stubs:
            raise DecodeError(f"duplicate stub index {index}")
        stubs[index] = symbol.name

    expected_stubs = set(range((stubs_end - stubs_start) // 4))
    if set(stubs) != expected_stubs:
        missing = sorted(expected_stubs - set(stubs))
        extra = sorted(set(stubs) - expected_stubs)
        details = []
        if missing:
            details.append("missing " + ", ".join(str(index) for index in missing[:16]))
        if extra:
            details.append("extra " + ", ".join(str(index) for index in extra[:16]))
        raise DecodeError(
            "stub indexes do not exactly fill the declared boundary: "
            + "; ".join(details)
        )

    normalized_groups: Dict[int, OverlayGroup] = {}
    for group in sorted(group_sections):
        address, size, line_number = group_sections[group]
        if size < 4 or (address & 3) != 0 or (size & 3) != 0:
            raise DecodeError(
                f"map line {line_number}: group {group} has invalid boundary "
                f"0x{address:x}+0x{size:x}"
            )
        if address == slot_start:
            if address + size > slot_end:
                raise DecodeError(
                    f"map line {line_number}: group {group} exceeds pageable "
                    "overlay slot"
                )
        elif address >= slot_start or address + size > slot_start:
            raise DecodeError(
                f"map line {line_number}: fixed group {group} at "
                f"0x{address:x}+0x{size:x} overlaps or follows pageable slot"
            )

        ordered = tuple(
            sorted(group_symbols[group], key=lambda item: (item.address, item.name))
        )
        for symbol in ordered:
            if (
                (symbol.address & 3) != 0
                or (symbol.size & 3) != 0
                or symbol.address < address + 4
                or symbol.end > address + size
            ):
                raise DecodeError(
                    f"group {group} body {symbol.name!r} at "
                    f"0x{symbol.address:x}+0x{symbol.size:x} exceeds its "
                    "declared boundary"
                )

        normalized_groups[group] = OverlayGroup(group, address, size, ordered)

    if not any(group.address == slot_start for group in normalized_groups.values()):
        raise DecodeError("link map has no pageable group at __p2_overlay_slot_start")
    if not any(group.symbols for group in normalized_groups.values()):
        raise DecodeError("link map has no overlay body symbols")

    resident_limit = min(group.address for group in normalized_groups.values())
    resident = tuple(
        sorted(
            (
                symbol
                for symbol in resident_candidates
                if symbol.address >= 0x400
                and (symbol.address & 3) == 0
                and (symbol.size & 3) == 0
                and symbol.end <= resident_limit
            ),
            key=lambda item: (item.address, item.name),
        )
    )
    if not resident:
        raise DecodeError("link map has no executable resident body symbols")

    return MapIndex(
        slot_start=slot_start,
        slot_end=slot_end,
        stubs_start=stubs_start,
        stubs_end=stubs_end,
        group_count=group_count,
        groups=normalized_groups,
        stubs=stubs,
        resident=resident,
    )


def parse_hot(data: bytes) -> Tuple[HotSnapshot, ...]:
    snapshots: List[HotSnapshot] = []
    stage: Optional[str] = None
    total = 0
    expected = 0
    entries: List[HotEntry] = []

    def finish() -> None:
        nonlocal stage, entries
        if stage is None:
            return
        if len(entries) != expected:
            raise DecodeError(
                f"HOT {stage} snapshot declares {expected} entries but has "
                f"{len(entries)}"
            )
        ranks = [entry.rank for entry in entries]
        if ranks != list(range(expected)):
            raise DecodeError(
                f"HOT {stage} ranks are {ranks!r}, expected "
                f"{list(range(expected))!r}"
            )
        keys = [
            (
                entry.caller_group,
                entry.caller_offset,
                entry.target_group,
                entry.target_stub,
            )
            for entry in entries
        ]
        if len(set(keys)) != len(keys):
            raise DecodeError(f"HOT {stage} snapshot has duplicate transition keys")

        ordered = sorted(
            entries,
            key=lambda entry: (
                -entry.count,
                entry.error,
                entry.caller_group,
                entry.caller_offset,
                entry.target_group,
                entry.target_stub,
            ),
        )

        count_sum = sum(entry.count for entry in entries)
        if total != UINT64_MAX and count_sum != total:
            raise DecodeError(
                f"HOT {stage} counts total 0x{count_sum:x}, expected " f"0x{total:x}"
            )

        ranked = tuple(
            replace(entry, rank=rank) for rank, entry in enumerate(ordered)
        )
        snapshots.append(HotSnapshot(stage, total, ranked))
        stage = None
        entries = []

    saw_hot = False
    for line_number, raw_line in enumerate(data.splitlines(), 1):
        line = raw_line.rstrip(b"\r")
        if not line.startswith(b"P2PY:HOT:"):
            continue
        saw_hot = True
        target_error = ERROR_RE.fullmatch(line)
        if target_error:
            error_stage = target_error.group(1).decode("ascii")
            error = int(target_error.group(2), 10)
            raise DecodeError(
                f"serial line {line_number}: HOT {error_stage} target "
                f"reported error {error}"
            )

        header = HEADER_RE.fullmatch(line)
        if header:
            finish()
            stage = header.group(1).decode("ascii")
            expected = int(header.group(2), 16)
            total = int(header.group(3), 16)
            if expected > HOT_CAPACITY:
                raise DecodeError(
                    f"serial line {line_number}: HOT {stage} declares "
                    f"{expected} entries, capacity is {HOT_CAPACITY}"
                )
            continue

        entry_match = ENTRY_RE.fullmatch(line)
        if entry_match is None:
            raise DecodeError(
                f"serial line {line_number}: malformed HOT record: "
                f"{line.decode('ascii', 'backslashreplace')!r}"
            )
        entry_stage = entry_match.group(1).decode("ascii")
        if stage is None:
            raise DecodeError(
                f"serial line {line_number}: HOT entry precedes its header"
            )
        if entry_stage != stage:
            raise DecodeError(
                f"serial line {line_number}: HOT entry stage {entry_stage} "
                f"does not match header stage {stage}"
            )
        values = [int(value, 16) for value in entry_match.groups()[1:]]
        (
            rank,
            caller_group,
            caller_offset,
            target_group,
            target_stub,
            count,
            error,
        ) = values
        if count == 0:
            raise DecodeError(f"serial line {line_number}: HOT entry count is zero")
        if error > count:
            raise DecodeError(
                f"serial line {line_number}: error 0x{error:x} exceeds "
                f"count 0x{count:x}"
            )
        entries.append(
            HotEntry(
                entry_stage,
                rank,
                caller_group,
                caller_offset,
                target_group,
                target_stub,
                count,
                error,
            )
        )

    finish()
    if not saw_hot:
        raise DecodeError("serial evidence contains no P2PY:HOT records")
    return tuple(snapshots)


def _resolve(
    symbols: Iterable[Symbol], address: int, description: str
) -> Tuple[str, int]:
    matches = [symbol for symbol in symbols if symbol.address <= address < symbol.end]
    if not matches:
        raise DecodeError(f"{description} 0x{address:08X} is unresolved")
    # Prefer the smallest containing symbol.  LLD maps can include a public
    # alias and an implementation symbol covering the same instructions.
    symbol = min(matches, key=lambda item: (item.size, -item.address, item.name))
    return symbol.name, address - symbol.address


def decode(
    index: MapIndex, snapshots: Sequence[HotSnapshot]
) -> Tuple[DecodedSnapshot, ...]:
    decoded = []
    for snapshot in snapshots:
        output_entries = []
        for entry in snapshot.entries:
            if (entry.caller_offset & 3) != 0:
                raise DecodeError(
                    f"HOT {entry.stage} rank {entry.rank}: caller offset "
                    f"0x{entry.caller_offset:x} is not four-byte aligned"
                )

            target_group = index.groups.get(entry.target_group)
            if target_group is None:
                raise DecodeError(
                    f"HOT {entry.stage} rank {entry.rank}: unknown target "
                    f"group {entry.target_group}"
                )
            if not target_group.symbols:
                raise DecodeError(
                    f"HOT {entry.stage} rank {entry.rank}: target group "
                    f"{entry.target_group} is empty"
                )

            try:
                target = index.stubs[entry.target_stub]
            except KeyError as exc:
                raise DecodeError(
                    f"HOT {entry.stage} rank {entry.rank}: unknown target "
                    f"stub {entry.target_stub}"
                ) from exc

            if target not in {symbol.name for symbol in target_group.symbols}:
                raise DecodeError(
                    f"HOT {entry.stage} rank {entry.rank}: stub "
                    f"{entry.target_stub} ({target}) is not in target group "
                    f"{entry.target_group}"
                )

            if entry.caller_group == 0:
                caller_address = entry.caller_offset
                resident_end = min(group.address for group in index.groups.values())
                if caller_address < 0x400 or caller_address >= resident_end:
                    raise DecodeError(
                        f"HOT {entry.stage} rank {entry.rank}: resident caller "
                        f"0x{caller_address:x} is outside executable Hub range"
                    )
                caller, function_offset = _resolve(
                    index.resident, caller_address, "resident caller"
                )
            else:
                caller_group = index.groups.get(entry.caller_group)
                if caller_group is None:
                    raise DecodeError(
                        f"HOT {entry.stage} rank {entry.rank}: unknown caller "
                        f"group {entry.caller_group}"
                    )
                if not caller_group.symbols:
                    raise DecodeError(
                        f"HOT {entry.stage} rank {entry.rank}: caller group "
                        f"{entry.caller_group} is empty"
                    )
                if entry.target_group == entry.caller_group:
                    raise DecodeError(
                        f"HOT {entry.stage} rank {entry.rank}: same-group "
                        "transition cannot enter the HOT stream"
                    )
                if entry.caller_offset >= caller_group.size:
                    raise DecodeError(
                        f"HOT {entry.stage} rank {entry.rank}: caller offset "
                        f"0x{entry.caller_offset:x} exceeds group "
                        f"{entry.caller_group} size 0x{caller_group.size:x}"
                    )

                caller_address = caller_group.address + entry.caller_offset
                caller, function_offset = _resolve(
                    caller_group.symbols,
                    caller_address,
                    f"caller group {entry.caller_group}",
                )

            output_entries.append(
                DecodedEntry(
                    stage=entry.stage,
                    rank=entry.rank,
                    caller_group=entry.caller_group,
                    caller_offset=entry.caller_offset,
                    caller=caller,
                    caller_function_offset=function_offset,
                    target_group=entry.target_group,
                    target_stub=entry.target_stub,
                    target=target,
                    count=entry.count,
                    error=entry.error,
                    lower_bound=entry.count - entry.error,
                )
            )
        decoded.append(
            DecodedSnapshot(snapshot.stage, snapshot.total, tuple(output_entries))
        )
    return tuple(decoded)


def _format(decoded: Sequence[DecodedSnapshot]) -> str:
    lines = []
    for sequence, snapshot in enumerate(decoded):
        lines.append(
            f"P2HOTDECODE:SNAP={sequence:02X}:STAGE={snapshot.stage}:"
            f"N={len(snapshot.entries):02X}:T={snapshot.total:016X}"
        )
        for entry in snapshot.entries:
            offset = (
                "?"
                if entry.caller_function_offset is None
                else f"0x{entry.caller_function_offset:X}"
            )
            lines.append(
                f"P2HOTDECODE:R={entry.rank:02X}:CG={entry.caller_group:08X}:"
                f"CO={entry.caller_offset:08X}:CALLER={entry.caller}+{offset}:"
                f"TG={entry.target_group:08X}:TS={entry.target_stub:08X}:"
                f"TARGET={entry.target}:C={entry.count:016X}:"
                f"E={entry.error:016X}:LB={entry.lower_bound:016X}"
            )
    return "\n".join(lines) + "\n"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", required=True, type=Path, dest="map_path")
    parser.add_argument("--serial-log", required=True, type=Path)
    parser.add_argument(
        "--build-status",
        required=True,
        type=Path,
        help="PASS p2-build-artifact-v1 status.json that owns --map",
    )
    parser.add_argument(
        "--hil-status",
        required=True,
        type=Path,
        help="P2 Python HIL status.json that owns --serial-log",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable JSON array instead of line records",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    map_data = _read(args.map_path)
    serial_data = _read(args.serial_log)
    build_status = _read_json(args.build_status, "build status")
    hil_status = _read_json(args.hil_status, "HIL status")
    validate_evidence_binding(map_data, serial_data, build_status, hil_status)
    index = parse_map(map_data)
    snapshots = parse_hot(serial_data)
    decoded = decode(index, snapshots)
    if args.json:
        json.dump([asdict(snapshot) for snapshot in decoded], sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(_format(decoded))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except DecodeError as exc:
        print(f"decode-overlay-hotspots.py: error: {exc}", file=sys.stderr)
        raise SystemExit(1)
