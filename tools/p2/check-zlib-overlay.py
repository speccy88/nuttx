#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Fail-closed archive and final-map audit for P2 overlay-backed zlib.

The compiler's ``-p2-hub-overlays-all`` mode is allowed to keep an unsafe
function resident.  That fallback is useful for general overlays, but it is
not acceptable for the constrained P2 CPython profile: linked zlib code must
be an overlay body (apart from its four-byte resident stubs), and zlib objects
must live in explicit external-data sections.

This checker reads, but never modifies, the application archive and LLD map.
It audits every zlib archive member, including members that the final link did
not extract, then checks the address of every live zlib map contribution.
"""

from __future__ import annotations

import argparse
import io
import pathlib
import re
import struct
import sys
from dataclasses import dataclass
from typing import Iterator, Pattern, Sequence

try:
    from elftools.elf.constants import SH_FLAGS
    from elftools.elf.elffile import ELFFile
    from elftools.common.exceptions import ELFError
except ImportError as exc:  # pragma: no cover - exercised only on broken hosts
    raise SystemExit("ERROR: check-zlib-overlay.py requires pyelftools") from exc


AR_MAGIC = b"!<arch>\n"
ELF_MAGIC = b"\x7fELF"
BODY_RE = re.compile(r"^\.p2\.overlay\.body\.([0-9a-f]{8})$")
STUB_SECTION = ".p2.overlay.stubs"
XDATA_RE = re.compile(r"^\.p2\.xdata(?:\.[A-Za-z0-9_]+)*$")
XBSS_RE = re.compile(r"^\.p2\.xbss(?:\.[A-Za-z0-9_]+)*$")
ENTRY_SECTION = ".p2.xdata.ro.overlay.entries"
DEFAULT_MEMBER_RE = r"(?:^|[./])system[./]zlib(?:[./_]|$)"
P2_ELF_MACHINE = 0x12C

# LLD's map contribution format is:
#
#   VMA LMA SIZE ALIGN archive(member):(.input.section)
#
# Paths may contain spaces, so parse the four fixed numeric columns first and
# split the provenance from the right-hand ``(member):(.section)`` suffix.

MAP_LINE_RE = re.compile(
    r"^\s*([0-9A-Fa-f]+)\s+([0-9A-Fa-f]+)\s+"
    r"([0-9A-Fa-f]+)\s+([0-9A-Fa-f]+)\s+"
    r"(.+)\(([^()]*)\):\(([^()]*)\)\s*$"
)


class CheckError(ValueError):
    """An input cannot prove the required zlib overlay invariant."""


@dataclass(frozen=True)
class ArchiveMember:
    name: str
    data: bytes


@dataclass
class ArchiveReport:
    members: int = 0
    body_sections: int = 0
    stub_sections: int = 0
    external_sections: int = 0
    functions: int = 0
    objects: int = 0


@dataclass
class MapReport:
    contributions: int = 0
    members: int = 0
    body_bytes: int = 0
    stub_bytes: int = 0
    xdata_bytes: int = 0
    xbss_bytes: int = 0


def _bounded(data: bytes, start: int, size: int, what: str) -> bytes:
    if start < 0 or size < 0 or start > len(data) or size > len(data) - start:
        raise CheckError(f"{what} is outside its input")
    return data[start : start + size]


def _decode_name(raw: bytes) -> str:
    return raw.decode("utf-8", "backslashreplace")


def archive_members(path: pathlib.Path) -> Iterator[ArchiveMember]:
    """Yield ordinary members from a GNU, BSD, or LLVM Unix archive."""

    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CheckError(f"cannot read archive {path}: {exc.strerror}") from exc

    if data.startswith(b"!<thin>\n"):
        raise CheckError(f"{path}: thin archives are not supported")
    if not data.startswith(AR_MAGIC):
        raise CheckError(f"{path}: invalid Unix archive magic")

    offset = len(AR_MAGIC)
    long_names = b""
    while offset < len(data):
        header = _bounded(data, offset, 60, f"{path}: archive member header")
        if header[58:60] != b"`\n":
            raise CheckError(f"{path}: malformed archive member header")
        try:
            size = int(header[48:58].decode("ascii").strip(), 10)
        except (UnicodeDecodeError, ValueError) as exc:
            raise CheckError(f"{path}: invalid archive member size") from exc

        payload = _bounded(data, offset + 60, size, f"{path}: archive member")
        raw_name = header[:16].rstrip()
        if raw_name in (b"/", b"/SYM64/", b"/SYM64"):
            member_name = raw_name.rstrip(b"/") or b"/"
        elif raw_name == b"//":
            long_names = payload
            member_name = b"//"
        elif raw_name.startswith(b"#1/"):
            try:
                name_size = int(raw_name[3:], 10)
            except ValueError as exc:
                raise CheckError(f"{path}: invalid BSD archive name") from exc
            member_name = _bounded(payload, 0, name_size, "BSD archive name")
            payload = payload[name_size:]
        elif raw_name.startswith(b"/") and raw_name[1:].isdigit():
            name_offset = int(raw_name[1:], 10)
            if name_offset >= len(long_names):
                raise CheckError(f"{path}: archive long-name offset is invalid")
            end = long_names.find(b"/\n", name_offset)
            if end < 0:
                end = long_names.find(b"\0", name_offset)
            if end < 0:
                raise CheckError(f"{path}: unterminated archive long name")
            member_name = long_names[name_offset:end]
        else:
            member_name = raw_name.rstrip(b"/")

        if member_name not in (b"/", b"//", b"/SYM64") and not member_name.startswith(
            b"__.SYMDEF"
        ):
            yield ArchiveMember(_decode_name(member_name), payload)

        offset += 60 + size
        if offset & 1:
            offset += 1

    if offset != len(data):
        raise CheckError(f"{path}: truncated archive alignment byte")


def _body_group(name: str) -> int | None:
    match = BODY_RE.fullmatch(name)
    if match is None:
        return None
    group = int(match.group(1), 16)
    if group == 0:
        raise CheckError("overlay group zero is reserved")
    return group


def _external_kind(name: str) -> str | None:
    if XDATA_RE.fullmatch(name):
        return "xdata"
    if XBSS_RE.fullmatch(name):
        return "xbss"
    return None


def _allowed_code(name: str) -> bool:
    return name == STUB_SECTION or _body_group(name) is not None


def audit_archive(
    path: pathlib.Path, member_pattern: Pattern[str]
) -> tuple[ArchiveReport, dict[str, dict[str, int]]]:
    report = ArchiveReport()
    matched_names: set[str] = set()
    member_sections: dict[str, dict[str, int]] = {}
    errors: list[str] = []

    for member in archive_members(path):
        if member_pattern.search(member.name) is None:
            continue
        report.members += 1
        if member.name in matched_names:
            errors.append(f"{path}: duplicate zlib archive member {member.name!r}")
        matched_names.add(member.name)
        label = f"{path}({member.name})"
        if not member.data.startswith(ELF_MAGIC):
            errors.append(f"{label}: zlib archive member is not an ELF object")
            continue

        try:
            elf = ELFFile(io.BytesIO(member.data))
            sections = list(elf.iter_sections())
        except (ELFError, struct.error, ValueError) as exc:
            errors.append(f"{label}: invalid ELF object: {exc}")
            continue

        if elf.header["e_type"] != "ET_REL":
            errors.append(f"{label}: zlib archive member is not ET_REL")
        if elf.elfclass != 32 or not elf.little_endian:
            errors.append(
                f"{label}: zlib archive member is not little-endian ELF32"
            )
        if elf.header["e_machine"] not in (P2_ELF_MACHINE, "EM_P2"):
            errors.append(
                f"{label}: ELF machine {elf.header['e_machine']!r} is not P2 "
                f"(0x{P2_ELF_MACHINE:x})"
            )

        member_body_bytes = 0
        member_stub_bytes = 0
        member_entry_bytes = 0
        member_body_functions = 0
        member_stub_functions = 0
        member_entry_objects = 0
        section_sizes: dict[str, int] = {}
        member_sections[member.name] = section_sizes

        for section in sections:
            size = int(section["sh_size"])
            flags = int(section["sh_flags"])
            if size == 0 or not (flags & SH_FLAGS.SHF_ALLOC):
                continue

            name = section.name
            section_sizes[name] = section_sizes.get(name, 0) + size
            executable = bool(flags & SH_FLAGS.SHF_EXECINSTR)
            if executable:
                try:
                    allowed = _allowed_code(name)
                except CheckError as exc:
                    errors.append(f"{label}:{name}: {exc}")
                    continue
                if not allowed:
                    errors.append(
                        f"{label}:{name}: nonempty allocatable executable section "
                        "is not an exact overlay body or stub section"
                    )
                    continue
                if name == STUB_SECTION:
                    report.stub_sections += 1
                    member_stub_bytes += size
                else:
                    report.body_sections += 1
                    member_body_bytes += size
            else:
                kind = _external_kind(name)
                if kind is None:
                    errors.append(
                        f"{label}:{name}: nonempty allocatable data section is not "
                        ".p2.xdata* or .p2.xbss*"
                    )
                    continue
                if kind == "xdata" and section["sh_type"] == "SHT_NOBITS":
                    errors.append(f"{label}:{name}: initialized xdata is SHT_NOBITS")
                    continue
                if kind == "xbss" and section["sh_type"] != "SHT_NOBITS":
                    errors.append(f"{label}:{name}: xbss is not SHT_NOBITS")
                    continue
                report.external_sections += 1
                if name == ENTRY_SECTION:
                    member_entry_bytes += size

        symtab = elf.get_section_by_name(".symtab")
        if symtab is None:
            errors.append(f"{label}: missing ELF symbol table")
            continue

        for symbol in symtab.iter_symbols():
            symbol_type = symbol["st_info"]["type"]
            size = int(symbol["st_size"])
            index = symbol["st_shndx"]
            if symbol_type not in ("STT_FUNC", "STT_OBJECT") or size == 0:
                continue
            if index in ("SHN_UNDEF", "SHN_ABS"):
                continue
            if not isinstance(index, int) or index < 0 or index >= len(sections):
                errors.append(
                    f"{label}:{symbol.name}: nonempty {symbol_type} has invalid "
                    f"section index {index}"
                )
                continue

            section = sections[index]
            section_name = section.name
            flags = int(section["sh_flags"])
            if symbol_type == "STT_FUNC":
                report.functions += 1
                try:
                    allowed = _allowed_code(section_name)
                except CheckError as exc:
                    errors.append(f"{label}:{symbol.name}: {exc}")
                    continue
                if not (
                    allowed
                    and flags & SH_FLAGS.SHF_ALLOC
                    and flags & SH_FLAGS.SHF_EXECINSTR
                ):
                    errors.append(
                        f"{label}:{symbol.name}: nonempty FUNC is defined in "
                        f"unexpected section {section_name}"
                    )
                elif section_name == STUB_SECTION and size != 4:
                    errors.append(
                        f"{label}:{symbol.name}: overlay stub FUNC is {size} "
                        "bytes instead of one four-byte veneer"
                    )
                elif section_name == STUB_SECTION:
                    member_stub_functions += 1
                elif BODY_RE.fullmatch(section_name):
                    member_body_functions += 1
            else:
                report.objects += 1
                if not (
                    _external_kind(section_name) is not None
                    and flags & SH_FLAGS.SHF_ALLOC
                    and not (flags & SH_FLAGS.SHF_EXECINSTR)
                ):
                    errors.append(
                        f"{label}:{symbol.name}: nonempty OBJECT is defined in "
                        f"unexpected section {section_name}"
                    )
                elif section_name == ENTRY_SECTION:
                    member_entry_objects += 1
                    if size != 8:
                        errors.append(
                            f"{label}:{symbol.name}: overlay entry OBJECT is "
                            f"{size} bytes instead of eight"
                        )

        if member_body_bytes or member_stub_bytes or member_entry_bytes:
            if member_body_bytes == 0:
                errors.append(f"{label}: overlay stubs have no body sections")
            if member_stub_bytes == 0 or member_stub_bytes % 4:
                errors.append(
                    f"{label}: overlay stub section size {member_stub_bytes} is "
                    "not a nonzero multiple of four"
                )
            expected_entries = (member_stub_bytes // 4) * 8
            if member_entry_bytes != expected_entries:
                errors.append(
                    f"{label}: overlay entry bytes {member_entry_bytes} do not "
                    f"match {member_stub_bytes} stub bytes (expected "
                    f"{expected_entries})"
                )
            if not (
                member_body_functions
                == member_stub_functions
                == member_entry_objects
            ):
                errors.append(
                    f"{label}: overlay symbol cardinality differs: "
                    f"bodies={member_body_functions}, "
                    f"stubs={member_stub_functions}, "
                    f"entries={member_entry_objects}"
                )

    if report.members == 0:
        errors.append(
            f"{path}: no archive members match zlib regex {member_pattern.pattern!r}"
        )
    if report.body_sections == 0:
        errors.append(f"{path}: zlib archive has no nonempty overlay body sections")
    if report.stub_sections == 0:
        errors.append(f"{path}: zlib archive has no nonempty overlay stub sections")
    if report.external_sections == 0:
        errors.append(f"{path}: zlib archive has no nonempty xdata/xbss sections")
    if errors:
        raise CheckError("\n".join(errors))
    return report, member_sections


def _inside(start: int, size: int, lower: int, upper: int) -> bool:
    return size >= 0 and start >= lower and start + size <= upper


def _overlaps(start: int, size: int, lower: int, upper: int) -> bool:
    return size > 0 and start < upper and start + size > lower


def audit_map(
    path: pathlib.Path,
    archive: pathlib.Path,
    member_pattern: Pattern[str],
    archive_sections: dict[str, dict[str, int]],
    slot_start: int,
    slot_end: int,
    xmem_start: int,
    xmem_end: int,
) -> MapReport:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise CheckError(f"cannot read linker map {path}: {exc.strerror}") from exc

    errors: list[str] = []
    live_members: set[str] = set()
    report = MapReport()
    saw_body = False
    saw_stub = False
    saw_external = False

    for line_number, line in enumerate(lines, 1):
        match = MAP_LINE_RE.match(line)
        if match is None:
            continue
        vma = int(match.group(1), 16)
        size = int(match.group(3), 16)
        archive_label = match.group(5).strip()
        member = match.group(6)
        section = match.group(7)
        map_archive = pathlib.Path(archive_label)
        if not map_archive.is_absolute():
            map_archive = path.parent / map_archive
        try:
            same_archive = map_archive.resolve() == archive.resolve()
        except OSError:
            same_archive = False
        if not same_archive:
            continue
        if member_pattern.search(member) is None:
            continue

        report.contributions += 1
        live_members.add(member)
        location = f"{path}:{line_number}:{archive.name}({member}):({section})"
        if member not in archive_sections:
            errors.append(f"{location}: live member is absent from the audited archive")
        elif section not in archive_sections[member]:
            errors.append(
                f"{location}: live section is absent from the audited archive member"
            )
        elif size > archive_sections[member][section]:
            errors.append(
                f"{location}: live contribution size 0x{size:x} exceeds audited "
                f"section size 0x{archive_sections[member][section]:x}"
            )

        if section == STUB_SECTION:
            saw_stub = saw_stub or size > 0
            report.stub_bytes += size
            if not _inside(vma, size, 0, slot_start):
                errors.append(
                    f"{location}: resident stub VMA 0x{vma:x}+0x{size:x} is "
                    f"outside Hub below slot [0x0,0x{slot_start:x})"
                )
            continue

        try:
            group = _body_group(section)
        except CheckError as exc:
            errors.append(f"{location}: {exc}")
            continue
        if group is not None:
            saw_body = saw_body or size > 0
            report.body_bytes += size
            if not _inside(vma, size, slot_start, slot_end):
                errors.append(
                    f"{location}: overlay body VMA 0x{vma:x}+0x{size:x} is "
                    f"outside slot [0x{slot_start:x},0x{slot_end:x})"
                )
            continue

        kind = _external_kind(section)
        if kind is not None:
            saw_external = saw_external or size > 0
            if kind == "xdata":
                report.xdata_bytes += size
            else:
                report.xbss_bytes += size
            if not _inside(vma, size, xmem_start, xmem_end):
                errors.append(
                    f"{location}: external data VMA 0x{vma:x}+0x{size:x} is "
                    f"outside tagged PSRAM [0x{xmem_start:x},0x{xmem_end:x})"
                )
            continue

        errors.append(
            f"{location}: unexpected live zlib section; resident code/tables or "
            "a malformed P2 section suffix escaped the overlay container"
        )

    report.members = len(live_members)
    if report.contributions == 0:
        errors.append(f"{path}: no live zlib contributions from {archive.name}")
    if not saw_stub:
        errors.append(f"{path}: live zlib map has no nonempty overlay stubs")
    if not saw_body:
        errors.append(f"{path}: live zlib map has no nonempty overlay bodies")
    if not saw_external:
        errors.append(f"{path}: live zlib map has no nonempty xdata/xbss")
    if errors:
        raise CheckError("\n".join(errors))
    return report


def integer(value: str) -> int:
    try:
        result = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer {value!r}") from exc
    if result < 0:
        raise argparse.ArgumentTypeError("address bounds must be nonnegative")
    return result


def compile_member_regex(value: str) -> Pattern[str]:
    try:
        return re.compile(value)
    except re.error as exc:
        raise argparse.ArgumentTypeError(f"invalid member regex: {exc}") from exc


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", required=True, type=pathlib.Path, dest="map_path")
    parser.add_argument("--archive", required=True, type=pathlib.Path)
    parser.add_argument("--slot-start", required=True, type=integer)
    parser.add_argument("--slot-end", required=True, type=integer)
    parser.add_argument("--xmem-start", required=True, type=integer)
    parser.add_argument("--xmem-end", required=True, type=integer)
    parser.add_argument(
        "--member-regex",
        type=compile_member_regex,
        default=re.compile(DEFAULT_MEMBER_RE),
        help=f"archive-member regex (default: {DEFAULT_MEMBER_RE})",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.slot_start >= args.slot_end:
        raise CheckError("overlay slot start must be below slot end")
    if args.xmem_start >= args.xmem_end:
        raise CheckError("tagged PSRAM start must be below its end")
    if _overlaps(
        args.slot_start,
        args.slot_end - args.slot_start,
        args.xmem_start,
        args.xmem_end,
    ):
        raise CheckError("overlay slot and tagged PSRAM bounds overlap")

    archive_report, member_names = audit_archive(args.archive, args.member_regex)
    map_report = audit_map(
        args.map_path,
        args.archive,
        args.member_regex,
        member_names,
        args.slot_start,
        args.slot_end,
        args.xmem_start,
        args.xmem_end,
    )
    external_bytes = map_report.xdata_bytes + map_report.xbss_bytes
    print(
        "STATICALLY-VERIFIED P2 zlib overlay: "
        f"archive_members={archive_report.members} "
        f"functions={archive_report.functions} objects={archive_report.objects} "
        f"live_members={map_report.members} "
        f"stub_bytes={map_report.stub_bytes} "
        f"body_bytes={map_report.body_bytes} "
        f"external_bytes={external_bytes}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CheckError as exc:
        for message in str(exc).splitlines():
            print(f"ERROR: {message}", file=sys.stderr)
        raise SystemExit(1)
