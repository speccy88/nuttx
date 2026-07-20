#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

"""Executable host tests for the target-side P2 Python container reader."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/p2"))
sys.path.insert(0, str(ROOT / "tools/p2/tests"))

import p2_python_container as abi  # noqa: E402
from test_p2_python_container import ContainerFixture  # noqa: E402

RUNTIME = ROOT / "arch/p2/src/common/p2_python_container.c"
HEADER = ROOT / "arch/p2/include/python_container.h"
PROBE = ROOT / "tools/p2/probes/python-container-runtime-host.c"

LOAD_ADDRESS = 0x50000
SLOT_SIZE = 64
PSRAM_BASE = 0x10000000
PSRAM_SIZE = 2 * 1024 * 1024
BACKING_OFFSET = 0x80000


class RuntimeInfo(ctypes.Structure):
    _fields_ = [
        ("file_size", ctypes.c_uint32),
        ("manifest_size", ctypes.c_uint32),
        ("section_count", ctypes.c_uint32),
        ("group_count", ctypes.c_uint32),
        ("stub_count", ctypes.c_uint32),
        ("overlay_load_address", ctypes.c_uint32),
        ("overlay_slot_size", ctypes.c_uint32),
    ]


def resign_manifest(data: bytes | bytearray) -> bytes:
    result = bytearray(data)
    manifest_size = struct.unpack_from("<Q", result, 0x60)[0]
    start = abi.MANIFEST_SHA256_OFFSET
    result[start : start + abi.MANIFEST_SHA256_SIZE] = bytes(
        abi.MANIFEST_SHA256_SIZE
    )
    digest = hashlib.sha256(result[:manifest_size]).digest()
    result[start : start + len(digest)] = digest
    return bytes(result)


def write_u32(data: bytes, offset: int, value: int, *, resign: bool = True) -> bytes:
    result = bytearray(data)
    struct.pack_into("<I", result, offset, value)
    return resign_manifest(result) if resign else bytes(result)


def write_u64(data: bytes, offset: int, value: int, *, resign: bool = True) -> bytes:
    result = bytearray(data)
    struct.pack_into("<Q", result, offset, value)
    return resign_manifest(result) if resign else bytes(result)


class PythonContainerRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build = tempfile.TemporaryDirectory()
        test_include = pathlib.Path(cls.build.name) / "include"
        cls.test_include = test_include
        (test_include / "nuttx").mkdir(parents=True)
        (test_include / "arch").mkdir()
        (test_include / "nuttx/config.h").write_text(
            "#define CONFIG_P2_HUB_OVERLAYS 1\n"
            "#define CONFIG_FORTIFY_SOURCE 0\n"
            "#define CONFIG_LIBC_OPEN_MAX 64\n",
            encoding="ascii",
        )
        for name in ("overlay.h", "python_container.h"):
            target = ROOT / "arch/p2/include" / name
            (test_include / "arch" / name).write_text(
                f'#include "{target}"\n', encoding="ascii"
            )
        suffix = ".dylib" if sys.platform == "darwin" else ".so"
        cls.library_path = pathlib.Path(cls.build.name) / f"runtime{suffix}"
        link_mode = "-dynamiclib" if sys.platform == "darwin" else "-shared"
        result = subprocess.run(
            [
                "clang",
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Wshadow",
                "-Wundef",
                "-Wstrict-prototypes",
                "-Werror",
                "-fPIC",
                link_mode,
                "-D__NuttX__",
                f"-I{test_include}",
                f"-I{ROOT / 'include'}",
                str(RUNTIME),
                str(PROBE),
                "-o",
                str(cls.library_path),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)

        cls.library = ctypes.CDLL(str(cls.library_path))
        cls.library.p2_probe_validate.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(RuntimeInfo),
        ]
        cls.library.p2_probe_validate.restype = ctypes.c_int
        cls.library.p2_probe_initialize.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_uint32,
            ctypes.c_size_t,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_size_t,
        ]
        cls.library.p2_probe_initialize.restype = ctypes.c_int
        cls.library.p2_probe_reset.restype = None
        cls.library.p2_probe_reuse_workspace.restype = None
        cls.library.p2_probe_loader_published.restype = ctypes.c_int
        cls.library.p2_probe_install_calls.restype = ctypes.c_size_t
        cls.library.p2_probe_uninstall_calls.restype = ctypes.c_size_t
        cls.library.p2_probe_register_calls.restype = ctypes.c_size_t
        cls.library.p2_probe_early_register_calls.restype = ctypes.c_size_t
        cls.library.p2_probe_set_register_result.argtypes = [ctypes.c_int]
        cls.library.p2_probe_set_register_result.restype = None
        cls.library.p2_probe_load_group.argtypes = [ctypes.c_uint32]
        cls.library.p2_probe_load_group.restype = ctypes.c_int
        cls.library.p2_probe_load_group_modified.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        cls.library.p2_probe_load_group_modified.restype = ctypes.c_int
        cls.library.p2_probe_set_group_flags.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        cls.library.p2_probe_set_group_flags.restype = None
        cls.library.p2_probe_hub.restype = ctypes.POINTER(ctypes.c_uint8)
        cls.library.p2_probe_romfs_address.restype = ctypes.c_size_t
        cls.library.p2_probe_romfs_size.restype = ctypes.c_size_t
        cls.library.p2_probe_source_read_calls.restype = ctypes.c_size_t
        cls.library.p2_probe_backing_read_calls.restype = ctypes.c_size_t
        cls.library.p2_probe_backing_header_reads.restype = ctypes.c_size_t
        cls.library.p2_probe_backing_write_calls.restype = ctypes.c_size_t
        cls.library.p2_probe_backing_write_bytes.restype = ctypes.c_size_t

    @classmethod
    def tearDownClass(cls) -> None:
        cls.build.cleanup()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = ContainerFixture(pathlib.Path(self.temporary.name))
        self.packed = self.fixture.pack()
        self.data = self.fixture.output_path.read_bytes()
        self.fingerprint = bytes.fromhex(
            self.fixture.manifest["build_fingerprint"]
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def validate(
        self,
        data: bytes | None = None,
        fingerprint: bytes | None = None,
        load_address: int = LOAD_ADDRESS,
        slot_size: int = SLOT_SIZE,
    ) -> tuple[int, RuntimeInfo]:
        image = self.data if data is None else data
        identity = self.fingerprint if fingerprint is None else fingerprint
        image_buffer = ctypes.create_string_buffer(image)
        identity_buffer = ctypes.create_string_buffer(identity)
        info = RuntimeInfo()
        result = self.library.p2_probe_validate(
            image_buffer,
            len(image),
            identity_buffer,
            load_address,
            slot_size,
            ctypes.byref(info),
        )
        return result, info

    def initialize(
        self,
        data: bytes | None = None,
        *,
        backing_offset: int = BACKING_OFFSET,
        workspace_count: int = 32,
        corrupt_copy: int = 0,
        source_is_backing: bool = False,
        source_backing_address: int | None = None,
        source_backing_size: int | None = None,
        actual_alias: bool | None = None,
        reset: bool = True,
    ) -> tuple[int, ctypes.Array[ctypes.c_uint8]]:
        if reset:
            self.library.p2_probe_reset()

        image = self.data if data is None else data
        image_buffer = ctypes.create_string_buffer(image)
        identity_buffer = ctypes.create_string_buffer(self.fingerprint)
        psram = (ctypes.c_uint8 * PSRAM_SIZE)()
        ctypes.memset(psram, 0xA5, len(psram))
        if actual_alias is None:
            actual_alias = source_is_backing
        if source_backing_address is None:
            source_backing_address = (
                PSRAM_BASE + backing_offset if source_is_backing else 0
            )
        if source_backing_size is None:
            source_backing_size = len(image) if source_is_backing else 0
        source_buffer: ctypes.c_void_p | ctypes.Array[ctypes.c_char]
        if actual_alias:
            self.assertLessEqual(backing_offset + len(image), len(psram))
            ctypes.memmove(
                ctypes.byref(psram, backing_offset), image_buffer, len(image)
            )
            source_buffer = ctypes.cast(
                ctypes.byref(psram, backing_offset), ctypes.c_void_p
            )
        else:
            source_buffer = image_buffer
        result = self.library.p2_probe_initialize(
            source_buffer,
            len(image),
            identity_buffer,
            LOAD_ADDRESS,
            SLOT_SIZE,
            psram,
            len(psram),
            backing_offset,
            workspace_count,
            corrupt_copy,
            source_is_backing,
            source_backing_address,
            source_backing_size,
        )
        return result, psram

    def io_counts(self) -> tuple[int, int, int, int, int]:
        return (
            self.library.p2_probe_source_read_calls(),
            self.library.p2_probe_backing_read_calls(),
            self.library.p2_probe_backing_header_reads(),
            self.library.p2_probe_backing_write_calls(),
            self.library.p2_probe_backing_write_bytes(),
        )

    def test_validates_packer_v1_and_reports_bounded_metadata(self) -> None:
        result, info = self.validate()
        self.assertEqual(result, 0)
        self.assertEqual(info.file_size, len(self.data))
        self.assertEqual(info.manifest_size, self.packed.manifest_size)
        self.assertEqual(info.section_count, len(self.packed.sections))
        self.assertEqual(info.group_count, 3)
        self.assertEqual(info.stub_count, len(self.packed.stubs))
        self.assertEqual(info.overlay_load_address, LOAD_ADDRESS)
        self.assertEqual(info.overlay_slot_size, SLOT_SIZE)

    def test_rejects_every_representative_truncation_and_trailing_data(self) -> None:
        manifest_size = self.packed.manifest_size
        lengths = {
            0,
            1,
            abi.HEADER_SIZE - 1,
            abi.HEADER_SIZE,
            manifest_size - 1,
            manifest_size,
            len(self.data) - 1,
        }
        for length in sorted(lengths):
            with self.subTest(length=length):
                self.assertLess(self.validate(self.data[:length])[0], 0)
        self.assertLess(self.validate(self.data + b"trailing")[0], 0)

    def test_rejects_manifest_payload_and_reserved_byte_corruption(self) -> None:
        manifest = bytearray(self.data)
        manifest[0x70] ^= 1
        self.assertLess(self.validate(bytes(manifest))[0], 0)

        payload = bytearray(self.data)
        payload[self.packed.sections[0].file_offset] ^= 1
        self.assertLess(self.validate(bytes(payload))[0], 0)

        reserved = bytearray(self.data)
        reserved[0x16] = 1
        self.assertLess(self.validate(resign_manifest(reserved))[0], 0)

    def test_rejects_unknown_flags_types_codecs_and_64_bit_overflow(self) -> None:
        section_table = struct.unpack_from("<Q", self.data, 0x30)[0]
        cases = {
            "header flag": write_u32(self.data, 0x18, 0x80000000),
            "section flag": write_u32(
                self.data, section_table + 4, 0x80000009
            ),
            "section type": bytearray(self.data),
            "codec": bytearray(self.data),
            "wide table": write_u64(self.data, 0x30, 1 << 32),
        }
        struct.pack_into("<H", cases["section type"], section_table, 99)
        cases["section type"] = resign_manifest(cases["section type"])
        struct.pack_into("<H", cases["codec"], section_table + 2, 1)
        cases["codec"] = resign_manifest(cases["codec"])
        for name, image in cases.items():
            with self.subTest(name=name):
                self.assertLess(self.validate(bytes(image))[0], 0)

    def test_rejects_wrong_fingerprint_and_overlay_contract(self) -> None:
        wrong = bytearray(self.fingerprint)
        wrong[0] ^= 1
        self.assertLess(self.validate(fingerprint=bytes(wrong))[0], 0)
        self.assertLess(self.validate(load_address=LOAD_ADDRESS + 4)[0], 0)
        self.assertLess(self.validate(slot_size=SLOT_SIZE + 4)[0], 0)

    def test_rejects_group_and_stub_table_inconsistency(self) -> None:
        group_table = struct.unpack_from("<Q", self.data, 0x38)[0]
        stub_table = struct.unpack_from("<Q", self.data, 0x40)[0]
        bad_group = write_u32(
            self.data,
            group_table + abi.GROUP_ENTRY_SIZE,
            self.packed.sections[2].file_offset + 4,
        )
        bad_stub = write_u32(self.data, stub_table, 0)
        bad_entry = write_u32(self.data, stub_table + 4, SLOT_SIZE)
        for image in (bad_group, bad_stub, bad_entry):
            self.assertLess(self.validate(image)[0], 0)

    def test_initialize_copies_rechecks_and_publishes_only_valid_data(self) -> None:
        result, psram = self.initialize()
        self.assertEqual(result, 0)
        self.assertEqual(self.library.p2_probe_install_calls(), 1)
        self.assertEqual(self.library.p2_probe_register_calls(), 1)
        self.assertEqual(self.library.p2_probe_early_register_calls(), 0)
        self.assertEqual(self.library.p2_probe_loader_published(), 1)
        source_reads, backing_reads, header_reads, writes, written = (
            self.io_counts()
        )
        self.assertGreater(source_reads, 0)
        self.assertGreater(backing_reads, 0)
        self.assertEqual(header_reads, 1)
        self.assertGreater(writes, 0)
        self.assertEqual(written, len(self.data))
        globals_data = self.fixture.globals_path.read_bytes()
        self.assertEqual(bytes(psram[: len(globals_data)]), globals_data)
        self.assertEqual(bytes(psram[0x100:0x180]), bytes(0x80))
        self.assertEqual(
            bytes(psram[BACKING_OFFSET : BACKING_OFFSET + len(self.data)]),
            self.data,
        )

        romfs_address = self.library.p2_probe_romfs_address()
        romfs_size = self.library.p2_probe_romfs_size()
        romfs = self.fixture.romfs_path.read_bytes()
        self.assertEqual(romfs_size, len(romfs))
        romfs_offset = romfs_address - PSRAM_BASE
        self.assertEqual(bytes(psram[romfs_offset : romfs_offset + romfs_size]), romfs)

        self.assertEqual(self.library.p2_probe_load_group(1), 0)
        hub = self.library.p2_probe_hub()
        overlay = self.fixture.group0_path.read_bytes()
        self.assertEqual(bytes(hub[: len(overlay)]), overlay)
        self.assertLess(self.library.p2_probe_load_group(0), 0)
        self.assertLess(self.library.p2_probe_load_group(3), 0)

        self.library.p2_probe_reuse_workspace()
        self.assertEqual(self.library.p2_probe_load_group(1), 0)

    def test_initialize_exact_in_place_validates_once_without_copying(self) -> None:
        result, psram = self.initialize(source_is_backing=True)
        self.assertEqual(result, 0)
        source_reads, backing_reads, header_reads, writes, written = (
            self.io_counts()
        )
        self.assertEqual(source_reads, 0)
        self.assertGreater(backing_reads, 0)
        self.assertEqual(header_reads, 1)
        self.assertEqual(writes, 0)
        self.assertEqual(written, 0)
        self.assertEqual(
            bytes(psram[BACKING_OFFSET : BACKING_OFFSET + len(self.data)]),
            self.data,
        )
        globals_data = self.fixture.globals_path.read_bytes()
        self.assertEqual(bytes(psram[: len(globals_data)]), globals_data)
        self.assertEqual(bytes(psram[0x100:0x180]), bytes(0x80))
        self.assertEqual(self.library.p2_probe_load_group(1), 0)
        hub = self.library.p2_probe_hub()
        overlay = self.fixture.group0_path.read_bytes()
        self.assertEqual(bytes(hub[: len(overlay)]), overlay)

    def test_in_place_contract_rejects_partial_or_mismatched_claims(self) -> None:
        exact_address = PSRAM_BASE + BACKING_OFFSET
        cases = (
            ("short range", True, exact_address, len(self.data) - 1),
            ("wrong base", True, exact_address + 4, len(self.data)),
            ("missing base", True, 0, len(self.data)),
            ("disabled address", False, exact_address, 0),
            ("disabled size", False, 0, len(self.data)),
        )
        for name, enabled, address, size in cases:
            with self.subTest(name=name):
                result, psram = self.initialize(
                    source_is_backing=enabled,
                    source_backing_address=address,
                    source_backing_size=size,
                    actual_alias=False,
                )
                self.assertEqual(result, -errno.EINVAL)
                self.assertEqual(self.io_counts(), (0, 0, 0, 0, 0))
                self.assertEqual(
                    bytes(psram[BACKING_OFFSET : BACKING_OFFSET + 512]),
                    bytes([0xA5]) * 512,
                )
                self.assertEqual(self.library.p2_probe_romfs_address(), 0)

    def test_false_exact_alias_claim_validates_target_and_never_publishes(self) -> None:
        result, psram = self.initialize(
            source_is_backing=True, actual_alias=False
        )
        self.assertLess(result, 0)
        source_reads, backing_reads, header_reads, writes, written = (
            self.io_counts()
        )
        self.assertEqual(source_reads, 0)
        self.assertGreater(backing_reads, 0)
        self.assertEqual(header_reads, 1)
        self.assertEqual((writes, written), (0, 0))
        self.assertEqual(
            bytes(psram[BACKING_OFFSET : BACKING_OFFSET + 512]),
            bytes([0xA5]) * 512,
        )
        self.assertEqual(self.library.p2_probe_romfs_address(), 0)
        self.assertEqual(self.library.p2_probe_install_calls(), 0)
        self.assertEqual(self.library.p2_probe_register_calls(), 0)
        self.assertEqual(self.library.p2_probe_early_register_calls(), 0)
        self.assertEqual(self.library.p2_probe_loader_published(), 0)
        self.assertLess(self.library.p2_probe_load_group(1), 0)

    def test_invalid_attempt_does_not_publish_and_valid_retry_succeeds(self) -> None:
        corrupt = bytearray(self.data)
        corrupt[self.packed.sections[0].file_offset] ^= 1

        failed, _ = self.initialize(bytes(corrupt))
        self.assertLess(failed, 0)
        self.assertEqual(self.library.p2_probe_install_calls(), 0)
        self.assertEqual(self.library.p2_probe_register_calls(), 0)
        self.assertEqual(self.library.p2_probe_loader_published(), 0)
        self.assertLess(self.library.p2_probe_load_group(1), 0)

        retried, _ = self.initialize(reset=False)
        self.assertEqual(retried, 0)
        self.assertEqual(self.library.p2_probe_install_calls(), 1)
        self.assertEqual(self.library.p2_probe_register_calls(), 1)
        self.assertEqual(self.library.p2_probe_early_register_calls(), 0)
        self.assertEqual(self.library.p2_probe_loader_published(), 1)
        self.assertEqual(self.library.p2_probe_load_group(1), 0)

    def test_loader_registration_failure_rolls_back_install_for_retry(self) -> None:
        self.library.p2_probe_reset()
        self.library.p2_probe_set_register_result(-errno.EILSEQ)

        failed, _ = self.initialize(reset=False)
        self.assertEqual(failed, -errno.EILSEQ)
        self.assertEqual(self.library.p2_probe_install_calls(), 1)
        self.assertEqual(self.library.p2_probe_register_calls(), 1)
        self.assertEqual(self.library.p2_probe_uninstall_calls(), 1)
        self.assertEqual(self.library.p2_probe_loader_published(), 0)
        self.assertEqual(self.library.p2_probe_romfs_address(), 0)

        self.library.p2_probe_set_register_result(0)
        retried, _ = self.initialize(reset=False)
        self.assertEqual(retried, 0)
        self.assertEqual(self.library.p2_probe_install_calls(), 2)
        self.assertEqual(self.library.p2_probe_register_calls(), 2)
        self.assertEqual(self.library.p2_probe_uninstall_calls(), 1)
        self.assertEqual(self.library.p2_probe_loader_published(), 1)

    def test_preflight_failure_never_writes_psram(self) -> None:
        corrupt = bytearray(self.data)
        corrupt[self.packed.sections[0].file_offset] ^= 1
        result, psram = self.initialize(bytes(corrupt))
        self.assertLess(result, 0)
        self.assertEqual(bytes(psram[:4096]), bytes([0xA5]) * 4096)
        self.assertEqual(
            bytes(psram[BACKING_OFFSET : BACKING_OFFSET + 4096]),
            bytes([0xA5]) * 4096,
        )

    def test_rejects_backing_overlap_small_workspace_and_copy_corruption(self) -> None:
        overlap_result, overlap = self.initialize(backing_offset=0)
        self.assertLess(overlap_result, 0)
        self.assertEqual(bytes(overlap[:512]), bytes([0xA5]) * 512)

        workspace_result, _ = self.initialize(workspace_count=2)
        self.assertLess(workspace_result, 0)

        corrupt_result, corrupt = self.initialize(corrupt_copy=1)
        self.assertLess(corrupt_result, 0)
        self.assertEqual(bytes(corrupt[:512]), bytes([0xA5]) * 512)

    def test_loader_uses_installed_descriptor_without_record_reread(self) -> None:
        result, psram = self.initialize()
        self.assertEqual(result, 0)
        group_table = struct.unpack_from("<Q", self.data, 0x38)[0]
        record = BACKING_OFFSET + group_table + abi.GROUP_ENTRY_SIZE
        psram[record] ^= 4
        reads = self.library.p2_probe_backing_read_calls()
        self.assertEqual(self.library.p2_probe_load_group(1), 0)
        self.assertEqual(self.library.p2_probe_backing_read_calls() - reads, 1)
        hub = self.library.p2_probe_hub()
        overlay = self.fixture.group0_path.read_bytes()
        self.assertEqual(bytes(hub[: len(overlay)]), overlay)

    def test_loader_rejects_arguments_not_matching_installed_descriptor(self) -> None:
        result, _ = self.initialize()
        self.assertEqual(result, 0)
        reads = self.library.p2_probe_backing_read_calls()
        self.assertLess(self.library.p2_probe_load_group_modified(1, 4, 0), 0)
        self.assertLess(self.library.p2_probe_load_group_modified(1, 0, 4), 0)
        self.library.p2_probe_set_group_flags(1, 0)
        self.assertLess(self.library.p2_probe_load_group(1), 0)
        self.assertEqual(self.library.p2_probe_backing_read_calls(), reads)

    def test_source_has_no_allocator_or_storage_dependency(self) -> None:
        source = RUNTIME.read_text()
        header = HEADER.read_text()
        self.assertNotRegex(source, r"\b(malloc|calloc|realloc|free)\s*\(")
        self.assertNotRegex(
            source, r"(?<!>)\b(open|close|read|lseek|ioctl|mount)\s*\("
        )
        self.assertIn("p2_overlay_install_groups", source)
        self.assertIn("p2_overlay_get_group", source)
        self.assertIn("p2_overlay_uninstall_groups", source)
        self.assertIn("p2_overlay_register_loader", source)
        self.assertIn("__has_attribute(p2_hub_resident)", source)
        self.assertIn("apply_to = function", source)
        self.assertIn("group_workspace", header)
        self.assertIn("source_is_backing", header)
        self.assertIn("source_backing_address", header)
        self.assertIn("source_backing_size", header)
        first_validate = source.index("p2_container_validate_internal(")
        first_copy = source.index("p2_container_copy_to_backing(")
        self.assertLess(first_validate, first_copy)
        loader = source[source.index("int p2_python_container_overlay_loader") :]
        self.assertEqual(loader.count("p2_container_target_read("), 1)
        self.assertNotIn("container->group_table_offset", loader)

    def test_p2_crc_acceleration_preserves_portable_host_fallback(self) -> None:
        source = RUNTIME.read_text()
        self.assertIn("#ifdef CONFIG_ARCH_P2", source)
        self.assertIn("#  include <arch/hub_crc32.h>", source)
        self.assertIn("return p2_hub_crc32_update(crc, data, size);", source)
        self.assertIn("#ifndef CONFIG_ARCH_P2", source)
        self.assertIn("P2_CONTAINER_CRC_POLYNOMIAL", source)
        self.assertIn(
            "crc = p2_container_crc32_update(crc, buffer, chunk);", source
        )

    @unittest.skipUnless(
        os.environ.get("P2LLVM_ROOT"), "set P2LLVM_ROOT for P2 codegen check"
    )
    def test_runtime_compiles_for_p2_with_unified_memory_enabled(self) -> None:
        toolchain = pathlib.Path(os.environ["P2LLVM_ROOT"])
        clang = toolchain / "bin/clang"
        nm = toolchain / "bin/llvm-nm"
        obj = pathlib.Path(self.build.name) / "p2_python_container.o"
        result = subprocess.run(
            [
                str(clang),
                "--target=p2",
                "-fno-builtin",
                "-Os",
                "-Wall",
                "-Wextra",
                "-Wshadow",
                "-Wundef",
                "-Wstrict-prototypes",
                "-Werror",
                f"-I{self.test_include}",
                f"-I{ROOT / 'include'}",
                f"-I{ROOT / 'sched'}",
                "-mllvm",
                "-p2-unified-memory",
                "-c",
                str(RUNTIME),
                "-o",
                str(obj),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        undefined = subprocess.run(
            [str(nm), "-u", str(obj)],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        self.assertIn("__p2_xmem_memcpy", undefined)
        self.assertIn("p2_overlay_install_groups", undefined)
        self.assertIn("p2_overlay_get_group", undefined)
        self.assertIn("p2_overlay_uninstall_groups", undefined)
        self.assertNotRegex(undefined, r"\b(malloc|calloc|realloc|free)\b")


if __name__ == "__main__":
    unittest.main()
