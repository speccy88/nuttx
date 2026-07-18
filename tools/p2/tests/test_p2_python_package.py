# SPDX-License-Identifier: Apache-2.0

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from elftools.elf.constants import SH_FLAGS
from elftools.elf.elffile import ELFFile

ROOT = pathlib.Path(__file__).resolve().parents[3]
TOOLS = ROOT / "tools" / "p2"
sys.path.insert(0, str(TOOLS))

import p2_python_container as container  # noqa: E402
import p2_python_package as package  # noqa: E402

ASSEMBLY = r"""
  .section .p2.entry,"ax",@progbits
  .globl __entry
__entry:
  nop

  .section .p2.python.fingerprint,"ax",@progbits
  .balign 4
  .zero 32

  .section .p2.overlay.stubs,"ax",@progbits
  .long 0

  .section .p2.xdata.ro.overlay.entries,"a",@progbits
  .balign 8
  .long 1
  .long 0

  .section .p2.overlay.body.00000001,"ax",@progbits
  nop
  nop

  .section .p2.xdata,"aw",@progbits
  .balign 16
  .long 0x12345678

  .section .p2.xbss,"aw",@nobits
  .balign 16
  .space 64
"""


LINKER_SCRIPT = r"""
ENTRY(__entry)
PHDRS
{
  hub PT_LOAD FLAGS(7);
  overlay PT_LOAD FLAGS(5);
  xdata PT_LOAD FLAGS(6);
}
SECTIONS
{
  .p2.entry 0 : { *(.p2.entry) } :hub
  .text 0xa00 : { *(.text .text.*) } :hub
  .p2.python.fingerprint : { *(.p2.python.fingerprint) } :hub
  .p2.overlay.stubs : { *(.p2.overlay.stubs) } :hub
  .p2.overlay.groups :
  {
    __p2_overlay_groups_start = ABSOLUTE(.);
    . += 32;
    __p2_overlay_groups_end = ABSOLUTE(.);
  } :hub
  __p2_overlay_slot_start = 0x5c000;
  __p2_overlay_slot_end = 0x7c000;
  .p2.overlay.group.00000001 0x5c000 : AT(0x03000000)
    { *(.p2.overlay.body.00000001) } :overlay
  .p2.xdata 0x10000000 : AT(0x02000000)
  {
    __p2_overlay_entries_start = ABSOLUTE(.);
    KEEP(*(.p2.xdata.ro.overlay.entries))
    __p2_overlay_entries_end = ABSOLUTE(.);
    *(.p2.xdata .p2.xdata.*)
  } :xdata
  .p2.xbss (NOLOAD) : { *(.p2.xbss) } :xdata
}
"""


class P2PythonPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = pathlib.Path(os.environ.get("P2LLVM_ROOT", ""))
        cls.clang = root / "bin" / "clang"
        cls.lld = root / "bin" / "ld.lld"
        cls.objcopy = root / "bin" / "llvm-objcopy"
        if not all(path.is_file() for path in (cls.clang, cls.lld, cls.objcopy)):
            raise unittest.SkipTest(
                "P2LLVM_ROOT does not select a complete P2 toolchain"
            )

    def _linked_fixture(self, root, linker_script=LINKER_SCRIPT):
        source = root / "fixture.S"
        script = root / "fixture.ld"
        obj = root / "fixture.o"
        elf = root / "fixture.elf"
        source.write_text(ASSEMBLY, encoding="utf-8")
        script.write_text(linker_script, encoding="utf-8")
        subprocess.run(
            [str(self.clang), "--target=p2", "-c", str(source), "-o", str(obj)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [str(self.lld), "-T", str(script), str(obj), "-o", str(elf)],
            check=True,
            capture_output=True,
            text=True,
        )
        return elf

    def test_full_package_round_trip_and_resident_filter(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            elf = self._linked_fixture(root)
            romfs = root / "stdlib.img"
            romfs.write_bytes(b"-rom1fs-" + bytes(range(64)))
            args = package._build_parser().parse_args(
                [
                    "--elf",
                    str(elf),
                    "--full-elf",
                    str(root / "full.elf"),
                    "--resident-elf",
                    str(root / "resident.elf"),
                    "--romfs",
                    str(romfs),
                    "--manifest",
                    str(root / "package" / "manifest.json"),
                    "--payload-dir",
                    str(root / "package" / "payloads"),
                    "--container",
                    str(root / "python.p2py"),
                    "--objcopy",
                    str(self.objcopy),
                    "--slot-size",
                    "0x20000",
                    "--reserve-size",
                    "0xc00000",
                ]
            )
            package.package(args)
            packed = container.verify_container(root / "python.p2py")
            manifest = json.loads(
                (root / "package" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                packed.build_fingerprint.hex(), manifest["build_fingerprint"]
            )
            published_romfs = (
                root / "package" / manifest["stdlib_romfs"]["path"]
            ).resolve()
            self.assertEqual(
                published_romfs,
                (root / "package" / "payloads" / "python-stdlib-romfs.img").resolve(),
            )
            self.assertEqual(published_romfs.read_bytes(), romfs.read_bytes())
            self.assertEqual([group.group_id for group in packed.stubs], [1])
            self.assertEqual(packed.overlay_load_address, 0x5C000)
            self.assertEqual(packed.overlay_slot_size, 0x20000)
            package.verify_resident_elf(root / "resident.elf", packed.build_fingerprint)

            with (root / "resident.elf").open("rb") as stream:
                resident = ELFFile(stream)
                resident_fingerprint = resident.get_section_by_name(
                    package.FINGERPRINT_SECTION
                )
                self.assertIsNotNone(resident_fingerprint)
                self.assertFalse(
                    int(resident_fingerprint["sh_flags"])
                    & SH_FLAGS.SHF_EXECINSTR
                )

            linked = package.inspect_linked_elf(elf, 0x20000)
            self.assertEqual(linked.entries.address, 0x10000000)
            self.assertEqual(linked.entries.data, bytes((1, 0, 0, 0, 0, 0, 0, 0)))
            self.assertEqual(linked.slot_start, 0x5C000)
            self.assertEqual(linked.slot_end, 0x7C000)
            self.assertEqual(linked.group_workspace.size, 32)

    def test_rejects_overlay_resident_contract_mismatches(self):
        cases = (
            (
                LINKER_SCRIPT.replace(
                    ".p2.overlay.group.00000001 0x5c000",
                    ".p2.overlay.group.00000001 0x5b000",
                ),
                "does not match resident slot start",
            ),
            (
                LINKER_SCRIPT.replace(
                    "__p2_overlay_slot_end = 0x7c000;",
                    "__p2_overlay_slot_end = 0x7b000;",
                ),
                "resident overlay slot span",
            ),
            (
                LINKER_SCRIPT.replace(". += 32;", ". += 16;"),
                "resident overlay group workspace is 16 bytes; expected 32",
            ),
        )
        for linker_script, message in cases:
            with (
                self.subTest(message=message),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = pathlib.Path(temporary)
                elf = self._linked_fixture(root, linker_script)
                with self.assertRaisesRegex(package.PackageError, message):
                    package.inspect_linked_elf(elf, 0x20000)

    def test_resident_verifier_rejects_executable_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            elf = self._linked_fixture(root)
            romfs = root / "stdlib.img"
            romfs.write_bytes(b"-rom1fs-test")
            args = package._build_parser().parse_args(
                [
                    "--elf",
                    str(elf),
                    "--full-elf",
                    str(root / "full.elf"),
                    "--resident-elf",
                    str(root / "resident.elf"),
                    "--romfs",
                    str(romfs),
                    "--manifest",
                    str(root / "package" / "manifest.json"),
                    "--payload-dir",
                    str(root / "package" / "payloads"),
                    "--container",
                    str(root / "python.p2py"),
                    "--objcopy",
                    str(self.objcopy),
                    "--slot-size",
                    "0x20000",
                    "--reserve-size",
                    "0xc00000",
                ]
            )
            package.package(args)
            packed = container.verify_container(root / "python.p2py")
            executable = root / "resident-executable-fingerprint.elf"
            subprocess.run(
                [
                    str(self.objcopy),
                    "--set-section-flags=.p2.python.fingerprint="
                    "alloc,readonly,code",
                    str(root / "resident.elf"),
                    str(executable),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            with self.assertRaisesRegex(
                package.PackageError, "fingerprint section must not be executable"
            ):
                package.verify_resident_elf(executable, packed.build_fingerprint)

    def test_rejects_a_prepatched_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            elf = self._linked_fixture(root)
            linked = package.inspect_linked_elf(elf, 0x20000)
            data = bytearray(elf.read_bytes())
            data[linked.fingerprint.offset] = 1
            elf.write_bytes(data)
            with self.assertRaisesRegex(package.PackageError, "canonical zero state"):
                package.inspect_linked_elf(elf, 0x20000)

    def test_rejects_unaligned_or_nonphysical_backing_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            elf = self._linked_fixture(root)
            romfs = root / "stdlib.img"
            romfs.write_bytes(b"-rom1fs-test")
            common = [
                "--elf",
                str(elf),
                "--full-elf",
                str(root / "full.elf"),
                "--resident-elf",
                str(root / "resident.elf"),
                "--romfs",
                str(romfs),
                "--manifest",
                str(root / "package" / "manifest.json"),
                "--payload-dir",
                str(root / "package" / "payloads"),
                "--container",
                str(root / "python.p2py"),
                "--objcopy",
                str(self.objcopy),
                "--slot-size",
                "0x20000",
            ]
            unaligned = package._build_parser().parse_args(
                common
                + [
                    "--reserve-size",
                    "0xc00000",
                    "--backing-address",
                    "0x10200001",
                ]
            )
            with self.assertRaisesRegex(package.PackageError, "16-byte aligned"):
                package.package(unaligned)

            nonphysical = package._build_parser().parse_args(
                common + ["--reserve-size", "0x2000010"]
            )
            with self.assertRaisesRegex(package.PackageError, "32-MiB PSRAM"):
                package.package(nonphysical)

    def test_oversize_failure_preserves_complete_previous_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            elf = self._linked_fixture(root)
            romfs = root / "stdlib.img"
            romfs.write_bytes(b"-rom1fs-" + bytes(range(64)))
            full = root / "full.elf"
            resident = root / "resident.elf"
            manifest = root / "package" / "manifest.json"
            payloads = root / "package" / "payloads"
            output = root / "python.p2py"
            manifest.parent.mkdir(parents=True)
            payloads.mkdir()

            previous = {
                full: b"previous-full",
                resident: b"previous-resident",
                manifest: b"previous-manifest",
                output: b"previous-container",
                payloads / "previous.bin": b"previous-payload",
            }
            for path, data in previous.items():
                path.write_bytes(data)

            args = package._build_parser().parse_args(
                [
                    "--elf",
                    str(elf),
                    "--full-elf",
                    str(full),
                    "--resident-elf",
                    str(resident),
                    "--romfs",
                    str(romfs),
                    "--manifest",
                    str(manifest),
                    "--payload-dir",
                    str(payloads),
                    "--container",
                    str(output),
                    "--objcopy",
                    str(self.objcopy),
                    "--slot-size",
                    "0x20000",
                    # Leave only sixteen bytes after the configured backing
                    # address, guaranteeing a capacity failure after every
                    # candidate output has been staged.
                    "--reserve-size",
                    hex(package.DEFAULT_BACKING_ADDRESS - package.P2_PSRAM_BASE + 16),
                ]
            )
            with self.assertRaisesRegex(package.PackageError, "exceeds reserved PSRAM"):
                package.package(args)

            for path, data in previous.items():
                self.assertEqual(path.read_bytes(), data)
            self.assertEqual(
                sorted(path.name for path in payloads.iterdir()),
                ["previous.bin"],
            )


if __name__ == "__main__":
    unittest.main()
