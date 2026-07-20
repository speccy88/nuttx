#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Synthetic positive and fail-closed tests for check-zlib-overlay.py."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from elftools.elf.elffile import ELFFile


ROOT = pathlib.Path(__file__).resolve().parents[3]
CHECKER = ROOT / "tools/p2/check-zlib-overlay.py"

GOOD_SOURCE = r"""
int zlib_body(int value)
{
  return value + 2;
}

const int zlib_table = 7;
"""


class CheckZlibOverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.work = pathlib.Path(self.temporary.name)
        self.member = "adler32.c.synthetic.system.zlib_1.o"
        self.compiler = self._find_tool("clang")
        self.archiver = self._find_tool("llvm-ar")
        if self.compiler is None or self.archiver is None:
            self.skipTest("P2LLVM_ROOT with P2 clang and llvm-ar is required")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _find_tool(name: str) -> str | None:
        p2llvm = os.environ.get("P2LLVM_ROOT")
        if not p2llvm:
            return None
        tool = pathlib.Path(p2llvm) / "bin" / name
        return str(tool) if tool.is_file() else None

    def _compile(
        self,
        source: str,
        member: str | None = None,
        *,
        transform: bool = True,
    ) -> pathlib.Path:
        assert self.compiler is not None
        member = member or self.member
        source_path = self.work / (member + ".c")
        object_path = self.work / member
        source_path.write_text(source, encoding="utf-8")
        command = [
            self.compiler,
            "--target=p2",
            "-Os",
            "-ffunction-sections",
            "-fdata-sections",
            "-mllvm",
            "-p2-unified-memory",
        ]
        if transform:
            command += [
                "-mllvm",
                "-p2-hub-overlays",
                "-mllvm",
                "-p2-hub-overlays-all",
                "-mllvm",
                "-p2-hub-overlay-link-assigned",
                "-mllvm",
                f"-p2-hub-overlay-source-root={self.work}",
                "-mllvm",
                "-p2-hub-overlay-source-namespace=zlib-test",
                "-mllvm",
                "-p2-hub-overlay-source-variant=v1",
                "-mllvm",
                "-p2-hub-overlay-slot-address=0x66000",
                "-mllvm",
                "-p2-externalize-data",
            ]
        command += ["-c", str(source_path), "-o", str(object_path)]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if result.returncode:
            self.fail(f"P2 fixture compiler failed: {result.stderr.strip()}")
        self.assertEqual(object_path.read_bytes()[:4], b"\x7fELF")
        return object_path

    @staticmethod
    def _alloc_section_sizes(object_path: pathlib.Path) -> dict[str, int]:
        with object_path.open("rb") as stream:
            elf = ELFFile(stream)
            return {
                section.name: int(section["sh_size"])
                for section in elf.iter_sections()
                if int(section["sh_size"]) > 0
                and int(section["sh_flags"]) & 0x2
            }

    def _good_object(
        self, member: str | None = None
    ) -> tuple[pathlib.Path, str, dict[str, int]]:
        object_path = self._compile(GOOD_SOURCE, member)
        sizes = self._alloc_section_sizes(object_path)
        bodies = [
            name
            for name in sizes
            if name.startswith(".p2.overlay.body.")
            or name.startswith(".p2.overlay.auto.")
        ]
        self.assertEqual(len(bodies), 1, sizes)
        self.assertRegex(
            bodies[0],
            r"^\.p2\.overlay\.auto\."
            r"[0-9a-f]{64}\.[0-9a-f]{8}\.[0-9a-f]{64}$",
        )
        self.assertIn(".p2.overlay.stubs", sizes)
        self.assertIn(".p2.xdata.ro.overlay.entries", sizes)
        return object_path, bodies[0], sizes

    def _archive(self, *objects: pathlib.Path) -> pathlib.Path:
        assert self.archiver is not None
        archive = self.work / "libapps.a"
        result = subprocess.run(
            [self.archiver, "rcs", str(archive), *(str(path) for path in objects)],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            self.fail(f"P2 fixture archiver failed: {result.stderr.strip()}")
        return archive

    def _fixture(
        self,
    ) -> tuple[pathlib.Path, str, dict[str, int]]:
        object_path, body, sizes = self._good_object()
        return self._archive(object_path), body, sizes

    def _map_text(
        self,
        archive: pathlib.Path,
        body_section: str,
        sizes: dict[str, int],
        member: str | None = None,
        *,
        body_vma: int = 0x66000,
        stub_vma: int = 0x41000,
        xdata_vma: int = 0x10000000,
        extra: tuple[str, int, int] | None = None,
    ) -> str:
        member = member or self.member
        rows = [
            (
                stub_vma,
                stub_vma,
                sizes.get(".p2.overlay.stubs", 4),
                ".p2.overlay.stubs",
            ),
            (
                body_vma,
                0x03000000,
                sizes.get(body_section, 4),
                body_section,
            ),
            (
                xdata_vma,
                0x02000000,
                sizes.get(".p2.xdata.ro", 4),
                ".p2.xdata.ro",
            ),
        ]
        if extra is not None:
            section, vma, size = extra
            rows.append((vma, vma, size, section))
        return "".join(
            f" {vma:08x} {lma:08x} {size:x} 4 "
            f"{archive}({member}):({section})\n"
            for vma, lma, size, section in rows
        )

    def _run(
        self,
        archive: pathlib.Path,
        map_text: str,
        *,
        map_archive: pathlib.Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        map_path = self.work / "nuttx.map"
        map_path.write_text(map_text, encoding="utf-8")
        command = [
            sys.executable,
            "-B",
            str(CHECKER),
            "--map",
            str(map_path),
            "--archive",
            str(archive),
            "--slot-start",
            "0x66000",
            "--slot-end",
            "0x7c000",
            "--xmem-start",
            "0x10000000",
            "--xmem-end",
            "0x12000000",
        ]
        if map_archive is not None:
            command += ["--map-archive", str(map_archive)]
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_accepts_overlay_code_external_data_and_bounded_map(self) -> None:
        archive, body, sizes = self._fixture()
        result = self._run(archive, self._map_text(archive, body, sizes))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("STATICALLY-VERIFIED P2 zlib overlay", result.stdout)
        self.assertIn("archive_members=1", result.stdout)
        self.assertIn("live_members=1", result.stdout)

    def test_accepts_immutable_snapshot_for_linked_archive(self) -> None:
        archive, body, sizes = self._fixture()
        snapshot = self.work / "zlib-link-input-libapps.a"
        snapshot.write_bytes(archive.read_bytes())
        result = self._run(
            snapshot,
            self._map_text(archive, body, sizes),
            map_archive=archive,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("STATICALLY-VERIFIED P2 zlib overlay", result.stdout)

    def test_snapshot_requires_the_linker_map_archive_identity(self) -> None:
        archive, body, sizes = self._fixture()
        snapshot = self.work / "zlib-link-input-libapps.a"
        snapshot.write_bytes(archive.read_bytes())
        result = self._run(snapshot, self._map_text(archive, body, sizes))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no live zlib contributions", result.stderr)

    def test_rejects_snapshot_that_differs_from_linked_archive(self) -> None:
        archive, body, sizes = self._fixture()
        snapshot = self.work / "zlib-link-input-libapps.a"
        snapshot.write_bytes(archive.read_bytes() + b"\n")
        result = self._run(
            snapshot,
            self._map_text(archive, body, sizes),
            map_archive=archive,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not exactly match linker-map archive", result.stderr)

    def test_rejects_archive_without_zlib_members(self) -> None:
        member = "ordinary.o"
        object_path, body, sizes = self._good_object(member)
        archive = self._archive(object_path)
        result = self._run(
            archive, self._map_text(archive, body, sizes, member)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no archive members match zlib regex", result.stderr)

    def test_rejects_resident_executable_archive_section(self) -> None:
        good, body, sizes = self._good_object()
        bad_member = "crc32.c.synthetic.system.zlib_2.o"
        bad = self._compile(
            "int resident_zlib_function(int value) { return value + 3; }",
            bad_member,
            transform=False,
        )
        archive = self._archive(good, bad)
        result = self._run(archive, self._map_text(archive, body, sizes))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nonempty allocatable executable section", result.stderr)
        self.assertIn(".text.resident_zlib_function", result.stderr)

    def test_rejects_resident_archive_object(self) -> None:
        good, body, sizes = self._good_object()
        bad_member = "crc32.c.synthetic.system.zlib_2.o"
        bad = self._compile(
            "const int resident_zlib_table = 11;",
            bad_member,
            transform=False,
        )
        archive = self._archive(good, bad)
        result = self._run(archive, self._map_text(archive, body, sizes))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nonempty allocatable data section", result.stderr)
        self.assertIn(".rodata.resident_zlib_table", result.stderr)

    def test_rejects_malformed_archive_body_suffix(self) -> None:
        object_path, body, sizes = self._good_object()
        malformed = body[:-1] + "X"
        data = object_path.read_bytes()
        self.assertEqual(len(body), len(malformed))
        self.assertIn(body.encode(), data)
        object_path.write_bytes(data.replace(body.encode(), malformed.encode(), 1))
        archive = self._archive(object_path)
        result = self._run(archive, self._map_text(archive, body, sizes))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not an exact overlay body or stub section", result.stderr)
        self.assertIn(malformed, result.stderr)

    def test_rejects_missing_entry_despite_valid_auto_body(self) -> None:
        object_path, body, sizes = self._good_object()
        entry_section = ".p2.xdata.ro.overlay.entries"
        renamed_entry = ".p2.xdata.ro.overlay.entryzz"
        data = object_path.read_bytes()
        self.assertEqual(len(entry_section), len(renamed_entry))
        self.assertIn(entry_section.encode(), data)
        object_path.write_bytes(
            data.replace(entry_section.encode(), renamed_entry.encode(), 1)
        )
        archive = self._archive(object_path)
        result = self._run(archive, self._map_text(archive, body, sizes))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("overlay entry bytes 0 do not match", result.stderr)
        self.assertIn(
            "overlay symbol cardinality differs: bodies=1, stubs=1, entries=0",
            result.stderr,
        )

    def test_rejects_body_outside_passed_slot(self) -> None:
        archive, body, sizes = self._fixture()
        result = self._run(
            archive,
            self._map_text(archive, body, sizes, body_vma=0x7BFF0),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside slot [0x66000,0x7c000)", result.stderr)

    def test_rejects_stub_not_resident_below_slot(self) -> None:
        archive, body, sizes = self._fixture()
        result = self._run(
            archive,
            self._map_text(archive, body, sizes, stub_vma=0x80000),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside Hub below slot [0x0,0x66000)", result.stderr)

    def test_rejects_external_data_outside_tagged_psram(self) -> None:
        archive, body, sizes = self._fixture()
        result = self._run(
            archive,
            self._map_text(archive, body, sizes, xdata_vma=0x00100000),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside tagged PSRAM", result.stderr)

    def test_rejects_unexpected_live_resident_section(self) -> None:
        archive, body, sizes = self._fixture()
        result = self._run(
            archive,
            self._map_text(
                archive, body, sizes, extra=(".rodata", 0x48000, 4)
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected live zlib section", result.stderr)

    def test_rejects_malformed_live_body_suffix(self) -> None:
        archive, body, sizes = self._fixture()
        malformed = body[:-1]
        result = self._run(
            archive,
            self._map_text(archive, malformed, sizes),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("malformed P2 section suffix", result.stderr)
        self.assertIn("live zlib map has no nonempty overlay bodies", result.stderr)

    def test_rejects_map_member_absent_from_audited_archive(self) -> None:
        archive, body, sizes = self._fixture()
        result = self._run(
            archive,
            self._map_text(
                archive,
                body,
                sizes,
                member="inflate.c.synthetic.system.zlib_1.o",
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("live member is absent from the audited archive", result.stderr)


if __name__ == "__main__":
    unittest.main()
