#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

import p2_python_container as container  # noqa: E402


SCRIPT = pathlib.Path(container.__file__).resolve()


def rewrite_manifest_digest(data):
    manifest_size = struct.unpack_from("<Q", data, 0x60)[0]
    mutable = bytearray(data)
    start = container.MANIFEST_SHA256_OFFSET
    mutable[start : start + container.MANIFEST_SHA256_SIZE] = bytes(
        container.MANIFEST_SHA256_SIZE
    )
    digest = hashlib.sha256(mutable[:manifest_size]).digest()
    mutable[start : start + len(digest)] = digest
    return mutable


def mutate_section(data, index, field, value):
    mutable = bytearray(data)
    table_offset = struct.unpack_from("<Q", mutable, 0x30)[0]
    offset = table_offset + index * container.SECTION_ENTRY_SIZE
    fields = list(container.SECTION_STRUCT.unpack_from(mutable, offset))
    fields[field] = value
    container.SECTION_STRUCT.pack_into(mutable, offset, *fields)
    return rewrite_manifest_digest(mutable)


def mutate_stub(data, index, field, value):
    mutable = bytearray(data)
    table_offset = struct.unpack_from("<Q", mutable, 0x40)[0]
    offset = table_offset + index * container.STUB_ENTRY_SIZE
    fields = list(container.STUB_STRUCT.unpack_from(mutable, offset))
    fields[field] = value
    container.STUB_STRUCT.pack_into(mutable, offset, *fields)
    return rewrite_manifest_digest(mutable)


def mutate_group(data, index, field, value):
    mutable = bytearray(data)
    table_offset = struct.unpack_from("<Q", mutable, 0x38)[0]
    offset = table_offset + index * container.GROUP_ENTRY_SIZE
    fields = list(container.GROUP_STRUCT.unpack_from(mutable, offset))
    fields[field] = value
    container.GROUP_STRUCT.pack_into(mutable, offset, *fields)
    return rewrite_manifest_digest(mutable)


class ContainerFixture:
    def __init__(self, root):
        self.root = root
        self.globals_path = root / "globals.bin"
        self.group0_path = root / "group-zero.bin"
        self.group1_path = root / "group-one.bin"
        self.romfs_path = root / "stdlib.img"
        self.globals_path.write_bytes(b"initialized-globals")
        self.group0_path.write_bytes(bytes(range(32)))
        self.group1_path.write_bytes(bytes(range(64, 88)))
        self.romfs_path.write_bytes(b"ROMFS\x00deterministic-python-stdlib")
        self.manifest_path = root / "container.json"
        self.output_path = root / "python.p2py"
        self.manifest = {
            "format": container.FORMAT_NAME,
            "build_fingerprint": hashlib.sha256(b"resident-nuttx-elf").hexdigest(),
            "overlay_slot_size": 64,
            "initialized_globals": [
                {
                    "id": 0,
                    "name": "python.globals.initialized",
                    "path": self.globals_path.name,
                    "address": "0x10000000",
                    "alignment": 16,
                }
            ],
            "zero_fill": [
                {
                    "id": 0,
                    "name": "python.globals.zero",
                    "address": "0x10000100",
                    "size": 128,
                    "alignment": 16,
                }
            ],
            # Deliberately reverse input order. Canonical output is by ID.
            "overlay_groups": [
                {
                    "id": 2,
                    "name": "python.overlay.cold",
                    "path": self.group1_path.name,
                    "load_address": "0x50000",
                    "alignment": 16,
                },
                {
                    "id": 1,
                    "name": "python.overlay.hot",
                    "path": self.group0_path.name,
                    "load_address": "0x50000",
                    "alignment": 16,
                },
            ],
            "stubs": [
                {
                    "id": 2,
                    "name": "stub.cold.last",
                    "group_id": 2,
                    "entry_offset": 20,
                },
                {
                    "id": 0,
                    "name": "stub.hot.first",
                    "group_id": 1,
                    "entry_offset": 0,
                },
                {
                    "id": 1,
                    "name": "stub.hot.second",
                    "group_id": 1,
                    "entry_offset": 4,
                },
            ],
            "stdlib_romfs": {
                "name": "python.stdlib.romfs",
                "path": self.romfs_path.name,
                "alignment": 16,
            },
        }
        self.write_manifest()

    def write_manifest(self, value=None):
        self.manifest_path.write_text(
            json.dumps(self.manifest if value is None else value, indent=2) + "\n",
            encoding="utf-8",
        )

    def pack(self):
        return container.pack_container(self.manifest_path, self.output_path)


class P2PythonContainerTests(unittest.TestCase):
    def test_round_trip_layout_and_listing(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ContainerFixture(pathlib.Path(temporary))
            packed = fixture.pack()
            verified = container.verify_container(fixture.output_path)
            self.assertEqual(packed, verified)
            self.assertEqual(packed.build_fingerprint.hex(), fixture.manifest["build_fingerprint"])
            self.assertEqual([section.section_type for section in packed.sections], [1, 2, 3, 3, 4])
            self.assertEqual([stub.stub_id for stub in packed.stubs], [0, 1, 2])
            self.assertEqual([stub.group_id for stub in packed.stubs], [1, 1, 2])
            for section in packed.sections:
                if section.has_payload:
                    self.assertEqual(
                        section.file_offset % max(16, section.alignment), 0
                    )
            listing = container.container_listing(packed)
            self.assertEqual(listing["format"], "p2-python-container-v1")
            self.assertEqual(listing["sections"][2]["name"], "python.overlay.hot")
            self.assertEqual(listing["stubs"][2]["entry_offset"], 20)

    def test_runtime_tables_match_resident_abi_exactly(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ContainerFixture(pathlib.Path(temporary))
            packed = fixture.pack()
            data = fixture.output_path.read_bytes()
            header = container.HEADER_STRUCT.unpack_from(data)
            self.assertEqual(header[5], container.GROUP_ENTRY_SIZE)
            self.assertEqual(header[6], container.STUB_ENTRY_SIZE)
            self.assertEqual(header[25], 0x50000)
            self.assertEqual(header[26], 64)
            self.assertEqual(container.GROUP_ENTRY_SIZE, 16)
            self.assertEqual(container.STUB_ENTRY_SIZE, 8)
            group_table_offset = header[16]
            stub_table_offset = header[17]
            groups = [
                section
                for section in packed.sections
                if section.section_type == container.TYPE_OVERLAY_GROUP
            ]
            self.assertEqual(
                container.GROUP_STRUCT.unpack_from(data, group_table_offset),
                (0, 0, 0, 0),
            )
            for section in groups:
                observed = container.GROUP_STRUCT.unpack_from(
                    data,
                    group_table_offset
                    + section.section_id * container.GROUP_ENTRY_SIZE,
                )
                self.assertEqual(
                    observed,
                    (
                        section.file_offset,
                        section.uncompressed_size,
                        section.crc32,
                        section.flags,
                    ),
                )
            for index, stub in enumerate(packed.stubs):
                observed = container.STUB_STRUCT.unpack_from(
                    data,
                    stub_table_offset + index * container.STUB_ENTRY_SIZE,
                )
                self.assertEqual(observed, (stub.group_id, stub.entry_offset))

            corrupt = mutate_group(data, 1, 0, groups[0].file_offset + 4)
            fixture.output_path.write_bytes(corrupt)
            with self.assertRaisesRegex(
                container.ContainerError, "runtime group record"
            ):
                container.verify_container(fixture.output_path)
            reserved = mutate_group(data, 0, 3, 1)
            fixture.output_path.write_bytes(reserved)
            with self.assertRaisesRegex(
                container.ContainerError, "reserved resident entry"
            ):
                container.verify_container(fixture.output_path)

    def test_output_is_deterministic_across_json_and_array_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            fixture = ContainerFixture(root)
            fixture.pack()
            first = fixture.output_path.read_bytes()
            reordered = {
                key: fixture.manifest[key]
                for key in reversed(list(fixture.manifest.keys()))
            }
            reordered["overlay_groups"] = list(
                reversed(reordered["overlay_groups"])
            )
            reordered["stubs"] = list(reversed(reordered["stubs"]))
            reordered["initialized_globals"][0] = {
                key: reordered["initialized_globals"][0][key]
                for key in reversed(
                    list(reordered["initialized_globals"][0].keys())
                )
            }
            fixture.write_manifest(reordered)
            second_path = root / "second.p2py"
            container.pack_container(fixture.manifest_path, second_path)
            self.assertEqual(first, second_path.read_bytes())

    def test_cli_pack_verify_and_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ContainerFixture(pathlib.Path(temporary))
            packed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "pack",
                    str(fixture.manifest_path),
                    str(fixture.output_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(packed.returncode, 0, packed.stderr)
            self.assertIn("HOST-VERIFIED", packed.stdout)
            verified = subprocess.run(
                [sys.executable, str(SCRIPT), "verify", str(fixture.output_path)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            listed = subprocess.run(
                [sys.executable, str(SCRIPT), "list", str(fixture.output_path)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(listed.returncode, 0, listed.stderr)
            decoded = json.loads(listed.stdout)
            self.assertEqual(decoded["stubs"][0]["name"], "stub.hot.first")

    def test_manifest_and_payload_corruption_are_distinguished(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ContainerFixture(pathlib.Path(temporary))
            packed = fixture.pack()
            original = bytearray(fixture.output_path.read_bytes())
            original[0x70] ^= 1
            fixture.output_path.write_bytes(original)
            with self.assertRaisesRegex(container.ContainerError, "manifest SHA-256"):
                container.verify_container(fixture.output_path)

            fixture.pack()
            original = bytearray(fixture.output_path.read_bytes())
            first_payload = next(
                section for section in packed.sections if section.has_payload
            )
            original[first_payload.file_offset] ^= 1
            fixture.output_path.write_bytes(original)
            with self.assertRaisesRegex(container.ContainerError, "payload CRC32"):
                container.verify_container(fixture.output_path)

    def test_truncation_and_trailing_data_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ContainerFixture(pathlib.Path(temporary))
            fixture.pack()
            original = fixture.output_path.read_bytes()
            fixture.output_path.write_bytes(original[:-1])
            with self.assertRaisesRegex(container.ContainerError, "file size"):
                container.verify_container(fixture.output_path)
            fixture.output_path.write_bytes(original + b"X")
            with self.assertRaisesRegex(container.ContainerError, "file size"):
                container.verify_container(fixture.output_path)

    def test_table_offsets_counts_and_arithmetic_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ContainerFixture(pathlib.Path(temporary))
            fixture.pack()
            original = fixture.output_path.read_bytes()

            shifted = bytearray(original)
            group_offset = struct.unpack_from("<Q", shifted, 0x38)[0]
            struct.pack_into("<Q", shifted, 0x38, group_offset + 1)
            fixture.output_path.write_bytes(rewrite_manifest_digest(shifted))
            with self.assertRaisesRegex(container.ContainerError, "canonical contiguous"):
                container.verify_container(fixture.output_path)

            excessive_count = bytearray(original)
            struct.pack_into("<I", excessive_count, 0x20, (1 << 32) - 1)
            fixture.output_path.write_bytes(
                rewrite_manifest_digest(excessive_count)
            )
            with self.assertRaisesRegex(container.ContainerError, "count exceeds"):
                container.verify_container(fixture.output_path)

            overflowing_strings = bytearray(original)
            struct.pack_into("<Q", overflowing_strings, 0x58, (1 << 64) - 1)
            fixture.output_path.write_bytes(
                rewrite_manifest_digest(overflowing_strings)
            )
            with self.assertRaisesRegex(container.ContainerError, "overflows"):
                container.verify_container(fixture.output_path)

    def test_overlapping_and_out_of_range_payloads_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ContainerFixture(pathlib.Path(temporary))
            packed = fixture.pack()
            original = fixture.output_path.read_bytes()
            # Section order is init, zero, group 1, group 2, ROMFS. Move ROMFS
            # into the initialized-global payload and re-sign the manifest.
            init_offset = packed.sections[0].file_offset
            overlap = mutate_section(original, 4, 9, init_offset + 4)
            fixture.output_path.write_bytes(overlap)
            with self.assertRaisesRegex(container.ContainerError, "overlap"):
                container.verify_container(fixture.output_path)

            outside = mutate_section(original, 4, 9, len(original) - 1)
            fixture.output_path.write_bytes(outside)
            with self.assertRaisesRegex(
                container.ContainerError, "file range|outside the container"
            ):
                container.verify_container(fixture.output_path)

            overflow = mutate_section(original, 4, 9, (1 << 64) - 1)
            fixture.output_path.write_bytes(overflow)
            with self.assertRaisesRegex(container.ContainerError, "overflows"):
                container.verify_container(fixture.output_path)

    def test_nonzero_payload_padding_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ContainerFixture(pathlib.Path(temporary))
            packed = fixture.pack()
            data = bytearray(fixture.output_path.read_bytes())
            payloads = [section for section in packed.sections if section.has_payload]
            gap_start = payloads[0].file_offset + payloads[0].stored_size
            gap_end = payloads[1].file_offset
            self.assertLess(gap_start, gap_end)
            data[gap_start] = 0xA5
            fixture.output_path.write_bytes(data)
            with self.assertRaisesRegex(container.ContainerError, "nonzero padding"):
                container.verify_container(fixture.output_path)

    def test_stub_group_and_entry_bounds_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ContainerFixture(pathlib.Path(temporary))
            fixture.pack()
            original = fixture.output_path.read_bytes()
            resident_group = mutate_stub(original, 0, 0, 0)
            fixture.output_path.write_bytes(resident_group)
            with self.assertRaisesRegex(
                container.ContainerError, "reserved resident group zero"
            ):
                container.verify_container(fixture.output_path)
            bad_group = mutate_stub(original, 0, 0, 99)
            fixture.output_path.write_bytes(bad_group)
            with self.assertRaisesRegex(container.ContainerError, "unknown overlay group"):
                container.verify_container(fixture.output_path)
            bad_entry = mutate_stub(original, 0, 1, 32)
            fixture.output_path.write_bytes(bad_entry)
            with self.assertRaisesRegex(container.ContainerError, "outside overlay group"):
                container.verify_container(fixture.output_path)

    def test_unknown_codec_and_reserved_data_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ContainerFixture(pathlib.Path(temporary))
            fixture.pack()
            original = fixture.output_path.read_bytes()
            unknown_codec = mutate_section(original, 0, 1, 1)
            fixture.output_path.write_bytes(unknown_codec)
            with self.assertRaisesRegex(container.ContainerError, "unsupported codec"):
                container.verify_container(fixture.output_path)
            nonzero_reserved = mutate_section(original, 0, 7, 1)
            fixture.output_path.write_bytes(nonzero_reserved)
            with self.assertRaisesRegex(container.ContainerError, "reserved"):
                container.verify_container(fixture.output_path)

    def test_external_overlap_and_range_overflow_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ContainerFixture(pathlib.Path(temporary))
            fixture.manifest["zero_fill"][0]["address"] = "0x10000010"
            fixture.write_manifest()
            with self.assertRaisesRegex(container.ContainerError, "overlap"):
                fixture.pack()
            fixture.manifest["zero_fill"][0]["address"] = hex(
                container.P2_PSRAM_END - 4
            )
            fixture.manifest["zero_fill"][0]["size"] = 8
            fixture.write_manifest()
            with self.assertRaisesRegex(container.ContainerError, "outside tagged"):
                fixture.pack()

    def test_alignment_and_instruction_size_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ContainerFixture(pathlib.Path(temporary))
            fixture.manifest["overlay_groups"][0]["load_address"] = "0x50002"
            fixture.write_manifest()
            with self.assertRaisesRegex(container.ContainerError, "aligned"):
                fixture.pack()
            fixture.manifest["overlay_groups"][0]["load_address"] = "0x50000"
            fixture.group1_path.write_bytes(b"123456")
            fixture.write_manifest()
            with self.assertRaisesRegex(container.ContainerError, "whole number"):
                fixture.pack()
            fixture.group1_path.write_bytes(bytes(range(64, 88)))
            fixture.manifest["overlay_groups"][0]["alignment"] = 3
            fixture.write_manifest()
            with self.assertRaisesRegex(container.ContainerError, "power of two"):
                fixture.pack()

    def test_overlay_groups_require_one_slot_inside_pinned_hub_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ContainerFixture(pathlib.Path(temporary))
            fixture.manifest["overlay_groups"][0]["load_address"] = "0x51000"
            fixture.write_manifest()
            with self.assertRaisesRegex(container.ContainerError, "one fixed"):
                fixture.pack()
            fixture.manifest["overlay_groups"][0]["load_address"] = "0x7bff0"
            fixture.manifest["overlay_groups"][1]["load_address"] = "0x7bff0"
            fixture.write_manifest()
            with self.assertRaisesRegex(container.ContainerError, "Hub load window"):
                fixture.pack()

    def test_configured_slot_size_rejects_oversized_decoded_groups(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ContainerFixture(pathlib.Path(temporary))
            fixture.manifest["overlay_slot_size"] = 28
            fixture.write_manifest()
            with self.assertRaisesRegex(container.ContainerError, "exceeds configured"):
                fixture.pack()

            fixture.manifest["overlay_slot_size"] = 64
            fixture.write_manifest()
            fixture.pack()
            corrupt = bytearray(fixture.output_path.read_bytes())
            struct.pack_into("<I", corrupt, 0xB4, 28)
            corrupt = rewrite_manifest_digest(corrupt)
            fixture.output_path.write_bytes(corrupt)
            with self.assertRaisesRegex(container.ContainerError, "exceeds configured"):
                container.verify_container(fixture.output_path)

    def test_atomic_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ContainerFixture(pathlib.Path(temporary))
            fixture.output_path.write_bytes(b"existing-good-output")
            fixture.group0_path.write_bytes(b"")
            with self.assertRaisesRegex(container.ContainerError, "must not be empty"):
                fixture.pack()
            self.assertEqual(
                fixture.output_path.read_bytes(), b"existing-good-output"
            )
            self.assertEqual(
                list(fixture.root.glob(".python.p2py.*.tmp")), []
            )

    def test_duplicate_json_keys_and_noncanonical_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ContainerFixture(pathlib.Path(temporary))
            fixture.manifest_path.write_text(
                '{"format":"x","format":"y"}\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(container.ContainerError, "duplicate JSON key"):
                fixture.pack()
            fixture.write_manifest()
            fixture.manifest["overlay_groups"][0]["id"] = 1
            fixture.write_manifest()
            with self.assertRaisesRegex(container.ContainerError, "unique"):
                fixture.pack()
            fixture.manifest["overlay_groups"][0]["id"] = 3
            fixture.write_manifest()
            with self.assertRaisesRegex(container.ContainerError, "contiguous from one"):
                fixture.pack()


if __name__ == "__main__":
    unittest.main()
