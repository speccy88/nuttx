#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Turn a linked P2 CPython ELF into resident and PSRAM artifacts.

The full ELF deliberately contains extraction-only sections for CPython
external data and Hub overlay groups.  This tool binds those sections to the
resident image with one SHA-256 fingerprint, packs them with the stdlib ROMFS,
and removes them from the ELF that loadp2 downloads into Hub RAM.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Iterable, Sequence

from elftools.elf.constants import SH_FLAGS
from elftools.elf.elffile import ELFFile

import p2_python_container as container

P2_ELF_MACHINE = 0x12C
P2_PSRAM_BASE = 0x10000000
P2_PSRAM_SIZE = 0x02000000
P2_HUB_LOAD_LIMIT = 0x0007C000
FINGERPRINT_SECTION = ".p2.python.fingerprint"
XDATA_SECTION = ".p2.xdata"
XBSS_SECTION = ".p2.xbss"
OVERLAY_PREFIX = ".p2.overlay.group."
OVERLAY_STUBS = ".p2.overlay.stubs"
OVERLAY_GROUPS = ".p2.overlay.groups"
LEGACY_OVERLAY_ENTRIES = ".p2.overlay.entries"
OVERLAY_ENTRIES_START = "__p2_overlay_entries_start"
OVERLAY_ENTRIES_END = "__p2_overlay_entries_end"
OVERLAY_GROUPS_START = "__p2_overlay_groups_start"
OVERLAY_GROUPS_END = "__p2_overlay_groups_end"
OVERLAY_SLOT_START = "__p2_overlay_slot_start"
OVERLAY_SLOT_END = "__p2_overlay_slot_end"
OVERLAY_GROUP_RECORD_SIZE = 16
DEFAULT_BACKING_ADDRESS = 0x10300000


class PackageError(ValueError):
    """A linked image violates the P2 Python packaging contract."""


@dataclass(frozen=True)
class ElfSection:
    name: str
    address: int
    offset: int
    size: int
    alignment: int
    section_type: str
    flags: int
    data: bytes


@dataclass(frozen=True)
class LinkedImage:
    fingerprint: ElfSection
    xdata: ElfSection
    xbss: ElfSection
    groups: tuple[tuple[int, ElfSection], ...]
    group_workspace: ElfSection
    stubs: ElfSection
    entries: ElfSection
    slot_start: int
    slot_end: int


def _fail(message: str) -> None:
    raise PackageError(message)


def _number(value: str) -> int:
    try:
        result = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("invalid integer: {}".format(value)) from exc
    if result < 0:
        raise argparse.ArgumentTypeError("integer must not be negative")
    return result


def _section_record(section) -> ElfSection:
    section_type = section["sh_type"]
    data = b"" if section_type == "SHT_NOBITS" else section.data()
    return ElfSection(
        name=section.name,
        address=int(section["sh_addr"]),
        offset=int(section["sh_offset"]),
        size=int(section["sh_size"]),
        alignment=max(1, int(section["sh_addralign"])),
        section_type=section_type,
        flags=int(section["sh_flags"]),
        data=data,
    )


def _one(elf: ELFFile, name: str) -> ElfSection:
    matches = [section for section in elf.iter_sections() if section.name == name]
    if len(matches) != 1:
        _fail("linked ELF must contain exactly one {} section".format(name))
    return _section_record(matches[0])


def _symbol_value(elf: ELFFile, name: str) -> int:
    table = elf.get_section_by_name(".symtab")
    if table is None:
        _fail("linked ELF has no symbol table")
    matches = [
        symbol
        for symbol in table.iter_symbols()
        if symbol.name == name and symbol["st_shndx"] != "SHN_UNDEF"
    ]
    if len(matches) != 1:
        _fail("linked ELF must define exactly one {} symbol".format(name))
    return int(matches[0]["st_value"])


def _external_slice(
    elf: ELFFile, section: ElfSection, start_name: str, end_name: str
) -> ElfSection:
    start = _symbol_value(elf, start_name)
    end = _symbol_value(elf, end_name)
    section_end = section.address + section.size
    if start < section.address or end < start or end > section_end:
        _fail(
            "external range {}..{} is outside {}".format(
                start_name, end_name, section.name
            )
        )
    relative = start - section.address
    size = end - start
    return ElfSection(
        name=".p2.xdata.ro.overlay.entries",
        address=start,
        offset=section.offset + relative,
        size=size,
        alignment=8,
        section_type=section.section_type,
        flags=section.flags,
        data=section.data[relative : relative + size],
    )


def inspect_linked_elf(path: pathlib.Path, slot_size: int) -> LinkedImage:
    try:
        with path.open("rb") as stream:
            elf = ELFFile(stream)
            if elf.elfclass != 32 or not elf.little_endian:
                _fail("linked image must be a little-endian ELF32 file")
            if int(elf.header["e_machine"]) != P2_ELF_MACHINE:
                _fail("linked image is not a Propeller 2 ELF (machine 0x12c)")
            if elf.header["e_type"] != "ET_EXEC" or int(elf.header["e_entry"]) != 0:
                _fail("linked P2 image must be an executable with entry address zero")

            fingerprint = _one(elf, FINGERPRINT_SECTION)
            xdata = _one(elf, XDATA_SECTION)
            xbss = _one(elf, XBSS_SECTION)
            stubs = _one(elf, OVERLAY_STUBS)
            group_workspace = _one(elf, OVERLAY_GROUPS)
            group_workspace_start = _symbol_value(elf, OVERLAY_GROUPS_START)
            group_workspace_end = _symbol_value(elf, OVERLAY_GROUPS_END)
            slot_start = _symbol_value(elf, OVERLAY_SLOT_START)
            slot_end = _symbol_value(elf, OVERLAY_SLOT_END)
            if elf.get_section_by_name(LEGACY_OVERLAY_ENTRIES) is not None:
                _fail("overlay entries must be part of initialized .p2.xdata")
            entries = _external_slice(
                elf, xdata, OVERLAY_ENTRIES_START, OVERLAY_ENTRIES_END
            )
            groups = []
            for section in elf.iter_sections():
                if not section.name.startswith(OVERLAY_PREFIX):
                    continue
                suffix = section.name[len(OVERLAY_PREFIX) :]
                if len(suffix) != 8:
                    _fail("invalid overlay output section name {}".format(section.name))
                try:
                    group = int(suffix, 16)
                except ValueError:
                    _fail("invalid overlay output section name {}".format(section.name))
                if group == 0:
                    _fail("overlay output section uses reserved group zero")
                groups.append((group, _section_record(section)))
    except PackageError:
        raise
    except (OSError, ValueError) as exc:
        _fail("cannot inspect linked ELF {}: {}".format(path, exc))

    groups.sort(key=lambda item: item[0])
    if [group for group, _section in groups] != list(range(1, len(groups) + 1)):
        _fail("linked overlay group IDs must be contiguous from one")
    if not groups:
        _fail("linked ELF does not contain any overlay groups")
    if fingerprint.size != 32 or fingerprint.section_type == "SHT_NOBITS":
        _fail("resident fingerprint section must contain exactly 32 file bytes")
    if fingerprint.data != bytes(32):
        _fail("resident fingerprint section is not in its canonical zero state")
    if not fingerprint.flags & SH_FLAGS.SHF_ALLOC:
        _fail("resident fingerprint section is not allocatable")
    if xdata.section_type == "SHT_NOBITS" or not xdata.data:
        _fail("CPython external initialized-data section is empty or NOBITS")
    if xdata.address != P2_PSRAM_BASE or xdata.size != len(xdata.data):
        _fail("CPython external initialized data has an invalid PSRAM layout")
    if xbss.section_type != "SHT_NOBITS" or xbss.size == 0:
        _fail("CPython external zero-fill section is empty or file-backed")
    if xbss.address < xdata.address + xdata.size:
        _fail("CPython external zero-fill section overlaps initialized data")
    if stubs.size == 0 or stubs.size % 4:
        _fail("overlay stub section is empty or not made of four-byte veneers")
    if entries.address != xdata.address or entries.address & 7:
        _fail("immutable overlay entries are not the aligned prefix of .p2.xdata")
    if entries.size != stubs.size // 4 * 8:
        _fail("overlay entry count does not match overlay stub count")
    slot_address = groups[0][1].address
    if slot_address != slot_start:
        _fail(
            "overlay group VMA 0x{:08x} does not match resident slot "
            "start 0x{:08x}".format(slot_address, slot_start)
        )
    if slot_end < slot_start or slot_end - slot_start != slot_size:
        _fail(
            "resident overlay slot span 0x{:08x}..0x{:08x} does not "
            "match configured size 0x{:x}".format(
                slot_start, slot_end, slot_size
            )
        )
    if slot_size == 0 or slot_start + slot_size > P2_HUB_LOAD_LIMIT:
        _fail("configured overlay slot lies outside loadp2-safe Hub RAM")
    if (
        group_workspace_end < group_workspace_start
        or group_workspace.address != group_workspace_start
        or group_workspace.address + group_workspace.size
        != group_workspace_end
    ):
        _fail("resident overlay group symbols do not cover the workspace section")
    expected_workspace_size = (len(groups) + 1) * OVERLAY_GROUP_RECORD_SIZE
    if group_workspace.size != expected_workspace_size:
        _fail(
            "resident overlay group workspace is {} bytes; expected {} "
            "for {} linked groups plus resident group zero".format(
                group_workspace.size, expected_workspace_size, len(groups)
            )
        )
    for group, section in groups:
        if section.section_type == "SHT_NOBITS" or section.address != slot_address:
            _fail(
                "overlay group {} does not use the shared file-backed slot".format(
                    group
                )
            )
        if section.size == 0 or section.size > slot_size or section.size % 4:
            _fail(
                "overlay group {} has an invalid instruction image size".format(group)
            )
    for index in range(stubs.size // 4):
        group, offset = struct.unpack_from("<II", entries.data, index * 8)
        if group == 0 or group > len(groups):
            _fail("overlay stub {} references unknown group {}".format(index, group))
        group_size = groups[group - 1][1].size
        if offset % 4 or offset > group_size - 4:
            _fail("overlay stub {} has an out-of-range entry offset".format(index))
    return LinkedImage(
        fingerprint,
        xdata,
        xbss,
        tuple(groups),
        group_workspace,
        stubs,
        entries,
        slot_start,
        slot_end,
    )


def canonical_fingerprint(elf_bytes: bytes, section: ElfSection) -> bytes:
    end = section.offset + section.size
    if section.offset < 0 or end > len(elf_bytes):
        _fail("fingerprint section lies outside the ELF file")
    canonical = bytearray(elf_bytes)
    canonical[section.offset : end] = bytes(section.size)
    return hashlib.sha256(canonical).digest()


def _atomic_write(path: pathlib.Path, data: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", dir=path.parent
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_manifest(
    image: LinkedImage,
    fingerprint: bytes,
    romfs: pathlib.Path,
    payload_dir: pathlib.Path,
    manifest_path: pathlib.Path,
    slot_size: int,
) -> None:
    payload_dir.mkdir(parents=True, exist_ok=True)
    xdata_path = payload_dir / "python-xdata.bin"
    romfs_path = payload_dir / "python-stdlib-romfs.img"
    _atomic_write(xdata_path, image.xdata.data)
    _atomic_write(romfs_path, romfs.read_bytes())
    overlay_groups = []
    for group, section in image.groups:
        payload = payload_dir / "overlay-{:08x}.bin".format(group)
        _atomic_write(payload, section.data)
        overlay_groups.append(
            {
                "alignment": max(4, section.alignment),
                "id": group,
                "load_address": "0x{:08x}".format(section.address),
                "name": "python.overlay.{:08x}".format(group),
                "path": os.path.relpath(payload, manifest_path.parent),
            }
        )
    stubs = []
    for index in range(image.stubs.size // 4):
        group, offset = struct.unpack_from("<II", image.entries.data, index * 8)
        stubs.append(
            {
                "entry_offset": offset,
                "group_id": group,
                "id": index,
                "name": "python.stub.{:08x}".format(index),
            }
        )
    manifest = {
        "build_fingerprint": fingerprint.hex(),
        "format": container.FORMAT_NAME,
        "initialized_globals": [
            {
                "address": "0x{:08x}".format(image.xdata.address),
                "alignment": max(16, image.xdata.alignment),
                "id": 0,
                "name": "python.globals.initialized",
                "path": os.path.relpath(xdata_path, manifest_path.parent),
            }
        ],
        "overlay_groups": overlay_groups,
        "overlay_slot_size": slot_size,
        "stdlib_romfs": {
            "alignment": 16,
            "name": "python.stdlib.romfs",
            "path": os.path.relpath(romfs_path, manifest_path.parent),
        },
        "stubs": stubs,
        "zero_fill": [
            {
                "address": "0x{:08x}".format(image.xbss.address),
                "alignment": max(16, image.xbss.alignment),
                "id": 0,
                "name": "python.globals.zero",
                "size": image.xbss.size,
            }
        ],
    }
    encoded = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write(manifest_path, encoded)


def _run_objcopy(
    objcopy: pathlib.Path,
    full_elf: pathlib.Path,
    resident_elf: pathlib.Path,
    remove_sections: Iterable[str],
) -> None:
    if not objcopy.is_file() or not os.access(objcopy, os.X_OK):
        _fail("llvm-objcopy is missing or not executable: {}".format(objcopy))
    resident_elf.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=resident_elf.name + ".", dir=resident_elf.parent
    )
    os.close(descriptor)
    temporary = pathlib.Path(temporary_name)
    temporary.unlink()

    # LLD may inherit SHF_EXECINSTR for the linker-created fingerprint from
    # the preceding Hub text section.  The resident verifier scans every
    # executable section for forbidden P2 instructions, so random SHA-256
    # words must be normalized to read-only data before publication.

    command = [
        str(objcopy),
        "--set-section-flags={}=alloc,readonly,data".format(
            FINGERPRINT_SECTION
        ),
    ]
    command.extend("--remove-section={}".format(name) for name in remove_sections)
    command.extend([str(full_elf), str(temporary)])
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if result.returncode:
            _fail(
                "llvm-objcopy failed ({}): {}".format(
                    result.returncode, (result.stderr or result.stdout).strip()
                )
            )
        os.chmod(temporary, full_elf.stat().st_mode & 0o777)
        os.replace(temporary, resident_elf)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def sanitize_resident_program_headers(path: pathlib.Path) -> None:
    """Turn extraction-only PT_LOAD records left by objcopy into PT_NULL.

    LLVM objcopy removes the requested sections but intentionally preserves
    their old program-header records.  A loader must never mistake those
    stale PSRAM/overlay records for resident content.
    """

    data = bytearray(path.read_bytes())
    elf = ELFFile(io.BytesIO(data))
    if elf.elfclass != 32 or not elf.little_endian:
        _fail("resident program-header sanitizer requires little-endian ELF32")
    allocated = [
        section
        for section in elf.iter_sections()
        if int(section["sh_flags"]) & SH_FLAGS.SHF_ALLOC
        and int(section["sh_size"]) != 0
    ]
    stale = []
    for index, segment in enumerate(elf.iter_segments()):
        if segment["p_type"] != "PT_LOAD":
            continue
        if not any(segment.section_in_segment(section) for section in allocated):
            stale.append(index)
    phoff = int(elf.header["e_phoff"])
    phentsize = int(elf.header["e_phentsize"])
    phnum = int(elf.header["e_phnum"])
    if phentsize < 32 or phoff + phentsize * phnum > len(data):
        _fail("resident ELF has an invalid program-header table")
    for index in stale:
        struct.pack_into("<I", data, phoff + index * phentsize, 0)
    if stale:
        _atomic_write(path, bytes(data), path.stat().st_mode & 0o777)


def verify_resident_elf(path: pathlib.Path, fingerprint: bytes) -> None:
    try:
        with path.open("rb") as stream:
            elf = ELFFile(stream)
            section_names = {section.name for section in elf.iter_sections()}
            forbidden = {XDATA_SECTION, XBSS_SECTION}
            forbidden.update(
                name for name in section_names if name.startswith(OVERLAY_PREFIX)
            )
            if forbidden & section_names:
                _fail("resident ELF still contains extraction-only sections")
            resident_fingerprint = elf.get_section_by_name(FINGERPRINT_SECTION)
            if (
                resident_fingerprint is None
                or resident_fingerprint.data() != fingerprint
            ):
                _fail("resident ELF fingerprint does not match its container")
            if int(resident_fingerprint["sh_flags"]) & SH_FLAGS.SHF_EXECINSTR:
                _fail("resident ELF fingerprint section must not be executable")
            for segment in elf.iter_segments():
                if segment["p_type"] != "PT_LOAD" or int(segment["p_memsz"]) == 0:
                    continue
                start = int(segment["p_vaddr"])
                end = start + int(segment["p_memsz"])
                if start < 0 or end > P2_HUB_LOAD_LIMIT:
                    _fail(
                        "resident ELF has PT_LOAD 0x{:08x}..0x{:08x} "
                        "outside Hub RAM".format(start, end)
                    )
                if int(segment["p_paddr"]) != start:
                    _fail(
                        "resident ELF PT_LOAD at 0x{:08x} has physical "
                        "address 0x{:08x}".format(start, int(segment["p_paddr"]))
                    )
    except PackageError:
        raise
    except (OSError, ValueError) as exc:
        _fail("cannot validate resident ELF {}: {}".format(path, exc))


def _remove_publish_path(path: pathlib.Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _publish_transaction(
    staged_and_final: Sequence[tuple[pathlib.Path, pathlib.Path]],
    backup_root: pathlib.Path,
) -> None:
    """Atomically replace a validated generation, rolling back on error."""

    backup_root.mkdir(parents=True, exist_ok=True)
    journal: list[tuple[pathlib.Path, pathlib.Path, bool, bool]] = []
    try:
        for index, (staged, final) in enumerate(staged_and_final):
            if not staged.exists():
                _fail("staged package output is missing: {}".format(staged))
            final.parent.mkdir(parents=True, exist_ok=True)
            backup = backup_root / "{:02d}".format(index)
            had_previous = os.path.lexists(final)
            if had_previous:
                os.replace(final, backup)
            journal.append((final, backup, had_previous, False))
            os.replace(staged, final)
            journal[-1] = (final, backup, had_previous, True)
    except (OSError, PackageError) as exc:
        rollback_errors = []
        for final, backup, had_previous, installed in reversed(journal):
            try:
                if installed and os.path.lexists(final):
                    _remove_publish_path(final)
                if had_previous and os.path.lexists(backup):
                    os.replace(backup, final)
            except OSError as rollback_error:
                rollback_errors.append(str(rollback_error))
        message = "cannot publish validated package generation: {}".format(exc)
        if rollback_errors:
            message += "; rollback errors: " + "; ".join(rollback_errors)
        _fail(message)


def package(args: argparse.Namespace) -> None:
    input_elf = args.elf.resolve()
    full_elf = args.full_elf.resolve()
    resident_elf = args.resident_elf.resolve()
    romfs = args.romfs.resolve()
    manifest = args.manifest.resolve()
    output = args.container.resolve()
    payload_dir = args.payload_dir.resolve()
    if not input_elf.is_file() or not romfs.is_file() or romfs.stat().st_size == 0:
        _fail("linked ELF and nonempty stdlib ROMFS inputs are required")
    publish_targets = (full_elf, resident_elf, output, manifest, payload_dir)
    if len({input_elf, romfs, *publish_targets}) != 7:
        _fail(
            "ELF, ROMFS, package files, manifest, and payload directory "
            "must be distinct"
        )
    image = inspect_linked_elf(input_elf, args.slot_size)
    external_end = image.xbss.address + image.xbss.size
    reserve_end = P2_PSRAM_BASE + args.reserve_size
    if args.reserve_size == 0 or args.reserve_size > P2_PSRAM_SIZE:
        _fail("reserved prefix is outside physical 32-MiB PSRAM")
    if args.backing_address & (container.CONTAINER_ALIGNMENT - 1):
        _fail("container backing address is not 16-byte aligned")
    if external_end > args.backing_address:
        _fail("external CPython data overlaps the configured container backing address")
    if args.backing_address < P2_PSRAM_BASE or args.backing_address >= reserve_end:
        _fail("container backing address is outside the reserved PSRAM prefix")

    publish_root = pathlib.Path(
        os.path.commonpath([str(path) for path in publish_targets])
    )
    if not publish_root.is_dir():
        publish_root = publish_root.parent
    if not publish_root.is_dir():
        _fail("package outputs do not share an existing staging parent")

    linked_bytes = input_elf.read_bytes()
    fingerprint = canonical_fingerprint(linked_bytes, image.fingerprint)
    patched = bytearray(linked_bytes)
    patched[
        image.fingerprint.offset : image.fingerprint.offset + image.fingerprint.size
    ] = fingerprint

    with tempfile.TemporaryDirectory(
        prefix=".p2-package-stage.", dir=publish_root
    ) as temporary:
        staging_root = pathlib.Path(temporary)

        def staged(final: pathlib.Path) -> pathlib.Path:
            return staging_root / final.relative_to(publish_root)

        staged_full = staged(full_elf)
        staged_resident = staged(resident_elf)
        staged_output = staged(output)
        staged_manifest = staged(manifest)
        staged_payload_dir = staged(payload_dir)
        _atomic_write(staged_full, bytes(patched), input_elf.stat().st_mode & 0o777)
        _write_manifest(
            image,
            fingerprint,
            romfs,
            staged_payload_dir,
            staged_manifest,
            args.slot_size,
        )
        packed = container.pack_container(staged_manifest, staged_output)
        if args.backing_address + packed.file_size > reserve_end:
            _fail(
                "Python container exceeds reserved PSRAM: end 0x{:08x}, "
                "limit 0x{:08x}".format(
                    args.backing_address + packed.file_size, reserve_end
                )
            )

        removals = [XDATA_SECTION, XBSS_SECTION]
        removals.extend(section.name for _group, section in image.groups)
        _run_objcopy(args.objcopy.resolve(), staged_full, staged_resident, removals)
        sanitize_resident_program_headers(staged_resident)
        verify_resident_elf(staged_resident, fingerprint)
        verified = container.verify_container(staged_output)
        if verified.build_fingerprint != fingerprint:
            _fail("packed container fingerprint changed during verification")

        # No user-visible output changes until every capacity and integrity
        # check above has passed.  Publication itself is journaled and rolls
        # the complete previous generation back if any rename fails.

        _publish_transaction(
            (
                (staged_payload_dir, payload_dir),
                (staged_manifest, manifest),
                (staged_output, output),
                (staged_full, full_elf),
                (staged_resident, resident_elf),
            ),
            staging_root / ".previous-generation",
        )
    print(
        "HOST-VERIFIED P2 Python package: resident={} container={} bytes={} "
        "fingerprint={}".format(
            resident_elf, output, verified.file_size, fingerprint.hex()
        )
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", required=True, type=pathlib.Path)
    parser.add_argument("--full-elf", required=True, type=pathlib.Path)
    parser.add_argument("--resident-elf", required=True, type=pathlib.Path)
    parser.add_argument("--romfs", required=True, type=pathlib.Path)
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    parser.add_argument("--payload-dir", required=True, type=pathlib.Path)
    parser.add_argument("--container", required=True, type=pathlib.Path)
    parser.add_argument("--objcopy", required=True, type=pathlib.Path)
    parser.add_argument("--slot-size", required=True, type=_number)
    parser.add_argument("--reserve-size", required=True, type=_number)
    parser.add_argument(
        "--backing-address", type=_number, default=DEFAULT_BACKING_ADDRESS
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        package(_build_parser().parse_args(argv))
    except (PackageError, container.ContainerError) as exc:
        print("p2_python_package.py: error: {}".format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
