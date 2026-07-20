#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace


SCRIPT = pathlib.Path(__file__).parents[1] / "decode-overlay-hotspots.py"
SPEC = importlib.util.spec_from_file_location("decode_overlay_hotspots", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
decoder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = decoder
SPEC.loader.exec_module(decoder)


# This is intentionally shaped like the real r5 LLD map rather than a symbol
# list.  It includes linker assignments, input-section rows, xdata before the
# overlay outputs, local body indexes that restart, an empty group, and one
# future-compatible fixed bank whose VMA differs from the pageable slot.

MAP = b"""\
       0        0        0     1 P2_HUB_ORIGIN = 0x00000000
       0        0        0     1 P2_HUB_SIZE = 0x00002040
       0        0        0     1 P2_HUB_END = P2_HUB_ORIGIN + P2_HUB_SIZE
     400      400       20     4 .text
     400      400       20     4         /build/libapps.a(caller.o):(.text.resident_caller)
     400      400       20     1                 resident_caller
     420      420       10     4 .rodata
     420      420       10     1                 resident_data
    1000     1000        c     4 .p2.overlay.stubs
    1000     1000        0     1         __p2_overlay_stubs_start = ABSOLUTE ( . )
    1000     1000        c     4         /apps/libpython.a(one.o):(.p2.overlay.stubs)
    1000     1000        4     1                 caller_one
    1004     1004        4     1                 helper_one
    1008     1008        4     1                 target_two
    100c     100c        0     1         __p2_overlay_stubs_end = ABSOLUTE ( . )
    1100     1100       40     4 .p2.overlay.groups
    2000     2000        0     1 __p2_overlay_slot_start = P2_HUB_RUNTIME_END
    2000     2000        0     1 __p2_overlay_slot_end = P2_HUB_END
10000000  2000000       10     8 .p2.xdata
10000000  2000000        8     1                 __p2_ovlentry.0.not_resident
10000008  2000008        0     1 __p2_overlay_group_count = 4
    1800     3000       30     4 .p2.overlay.group.00000001
    1800     3000        4     1         LONG ( 0 )
    1804     3004       10     4         /apps/libpython.a(one.o):(.p2.overlay.auto.a)
    1804     3004       10     1                 __p2_ovlbody.0.caller_one
    1814     3014       1c     4         /apps/libpython.a(one.o):(.p2.overlay.auto.b)
    1814     3014       1c     1                 __p2_ovlbody.1.helper_one
    2000     4000       20     4 .p2.overlay.group.00000002
    2000     4000        4     1         LONG ( 0 )
    2004     4004       1c     4         /apps/libpython.a(two.o):(.p2.overlay.auto.c)
    2004     4004       1c     1                 __p2_ovlbody.0.target_two
    2000     5000        4     4 .p2.overlay.group.00000003
    2000     5000        4     1         LONG ( 0 )
       0        0       10     8 .symtab
"""


def hot_entry(
    rank,
    caller_group,
    caller_offset,
    target_group,
    target_stub,
    count,
    error=0,
    stage="SAMPLE",
):
    return (
        (
            "P2PY:HOT:{stage}:R={rank:02X}:CG={caller_group:08X}:"
            "CO={caller_offset:08X}:TG={target_group:08X}:TS={target_stub:08X}:"
            "C={count:016X}:E={error:016X}\r\n"
        )
        .format(
            stage=stage,
            rank=rank,
            caller_group=caller_group,
            caller_offset=caller_offset,
            target_group=target_group,
            target_stub=target_stub,
            count=count,
            error=error,
        )
        .encode("ascii")
    )


def hot_snapshot(stage, total, entries):
    return "P2PY:HOT:{stage}:N={count:02X}:T={total:016X}\r\n".format(
        stage=stage, count=len(entries), total=total
    ).encode("ascii") + b"".join(entries)


SAMPLE_ENTRIES = (
    hot_entry(0, 1, 8, 2, 2, 0x100, 4),
    hot_entry(1, 0, 0x408, 1, 1, 0x80, 0),
)
FINAL_ENTRIES = (hot_entry(0, 1, 0x18, 2, 2, 0x200, 0x10, stage="FINAL"),)
SERIAL = (
    b"noise\r\n"
    b"P2PY:UPLOAD:PASS:SIZE=103:CRC=A1B2C3D4:RXDROPS=0\r\n"
    + hot_snapshot("SAMPLE", 0x180, SAMPLE_ENTRIES)
    + hot_snapshot("FINAL", 0x200, FINAL_ENTRIES)
)


ARTIFACT_HASHES = {
    "nuttx": "11" * 32,
    "nuttx.bin": "22" * 32,
    "nuttx.p2py": "33" * 32,
}
ARTIFACT_SIZES = {"nuttx": 101, "nuttx.bin": 102, "nuttx.p2py": 103}


def build_status(map_data=MAP):
    files = {
        name: {"sha256": ARTIFACT_HASHES[name], "size": ARTIFACT_SIZES[name]}
        for name in ARTIFACT_HASHES
    }
    files["nuttx.map"] = {
        "sha256": hashlib.sha256(map_data).hexdigest(),
        "size": len(map_data),
    }
    return {
        "format": "p2-build-artifact-v1",
        "status": "PASS",
        "elf_sha256": ARTIFACT_HASHES["nuttx"],
        "binary_sha256": ARTIFACT_HASHES["nuttx.bin"],
        "files": files,
    }


def hil_status(serial_data=SERIAL):
    return {
        "format": "p2-python-hil-smoke-v1",
        "status": "FAIL",
        "serial_rx_bytes": len(serial_data),
        "inputs": {
            "resident_elf_sha256": ARTIFACT_HASHES["nuttx"],
            "resident_elf_size": ARTIFACT_SIZES["nuttx"],
            "image_sha256": ARTIFACT_HASHES["nuttx.bin"],
            "image_size": ARTIFACT_SIZES["nuttx.bin"],
            "container_sha256": ARTIFACT_HASHES["nuttx.p2py"],
            "container_size": ARTIFACT_SIZES["nuttx.p2py"],
            "container_crc32": "A1B2C3D4",
        },
    }


class DecodeOverlayHotspotsTests(unittest.TestCase):
    def test_actual_shaped_map_and_multiple_snapshots_decode_exact_names(self):
        index = decoder.parse_map(MAP)
        self.assertEqual(index.slot_start, 0x2000)
        self.assertEqual(index.slot_end, 0x2040)
        self.assertEqual(index.stubs_start, 0x1000)
        self.assertEqual(index.stubs_end, 0x100C)
        self.assertEqual(index.group_count, 4)
        self.assertEqual(index.groups[1].address, 0x1800)
        self.assertEqual(index.groups[2].address, 0x2000)
        self.assertEqual(index.groups[3].symbols, ())
        self.assertEqual(
            index.stubs,
            {0: "caller_one", 1: "helper_one", 2: "target_two"},
        )
        self.assertEqual(
            {symbol.name for symbol in index.resident}, {"resident_caller"}
        )

        snapshots = decoder.parse_hot(SERIAL)
        self.assertEqual([item.stage for item in snapshots], ["SAMPLE", "FINAL"])
        decoded = decoder.decode(index, snapshots)

        first = decoded[0].entries[0]
        self.assertEqual(first.caller, "caller_one")
        self.assertEqual(first.caller_function_offset, 4)
        self.assertEqual(first.target, "target_two")
        self.assertEqual(first.lower_bound, 0xFC)

        resident = decoded[0].entries[1]
        self.assertEqual(resident.caller, "resident_caller")
        self.assertEqual(resident.caller_function_offset, 8)
        self.assertEqual(resident.target, "helper_one")

        final = decoded[1].entries[0]
        self.assertEqual(final.caller, "helper_one")
        self.assertEqual(final.caller_function_offset, 4)

    def test_output_is_stable_and_contains_space_saving_lower_bound(self):
        decoded = decoder.decode(decoder.parse_map(MAP), decoder.parse_hot(SERIAL))
        output = decoder._format(decoded)
        self.assertIn(
            "P2HOTDECODE:SNAP=00:STAGE=SAMPLE:N=02:T=0000000000000180",
            output,
        )
        self.assertIn("CALLER=caller_one+0x4", output)
        self.assertIn("TARGET=target_two", output)
        self.assertIn("LB=00000000000000FC", output)

    def test_hot_grammar_and_space_saving_invariants_fail_closed(self):
        cases = {}
        cases["bad width"] = SERIAL.replace(b"CG=00000001", b"CG=1", 1)
        cases["incomplete"] = b"P2PY:HOT:SAMPLE:N=01:T=0000000000000001\n"
        cases["capacity"] = b"P2PY:HOT:SAMPLE:N=09:T=0000000000000000\n"
        cases["stage"] = b"P2PY:HOT:BOGUS:N=00:T=0000000000000000\n"
        cases["target error"] = b"P2PY:HOT:SAMPLE:ERROR=-75\n"
        cases["zero count"] = hot_snapshot("SAMPLE", 0, (hot_entry(0, 1, 8, 2, 2, 0),))
        cases["error exceeds count"] = hot_snapshot(
            "SAMPLE", 1, (hot_entry(0, 1, 8, 2, 2, 1, 2),)
        )
        duplicate = hot_entry(0, 1, 8, 2, 2, 1)
        cases["duplicate key"] = hot_snapshot(
            "SAMPLE",
            2,
            (duplicate, duplicate.replace(b":R=00:", b":R=01:")),
        )
        cases["count sum"] = SERIAL.replace(
            b":T=0000000000000180", b":T=0000000000000181", 1
        )
        cases["duplicate rank"] = SERIAL.replace(b":R=01:", b":R=00:", 1)

        for name, evidence in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(decoder.DecodeError):
                    decoder.parse_hot(evidence)

    def test_uint64_saturated_total_exempts_only_exact_sum_check(self):
        evidence = hot_snapshot(
            "SAMPLE",
            decoder.UINT64_MAX,
            (hot_entry(0, 1, 8, 2, 2, 7, 3),),
        )
        snapshot = decoder.parse_hot(evidence)[0]
        self.assertEqual(snapshot.total, decoder.UINT64_MAX)

    def test_host_ranking_checks_every_tie_breaker(self):
        entries = (
            hot_entry(0, 0, 0x408, 1, 0, 9, 0),
            hot_entry(1, 0, 0x408, 1, 1, 8, 0),
            hot_entry(2, 0, 0x408, 1, 2, 8, 1),
            hot_entry(3, 0, 0x40C, 1, 0, 8, 1),
            hot_entry(4, 1, 4, 2, 2, 8, 1),
            hot_entry(5, 1, 8, 2, 2, 8, 1),
            hot_entry(6, 1, 8, 3, 2, 8, 1),
            hot_entry(7, 1, 8, 3, 3, 8, 1),
        )
        expected = decoder.parse_hot(hot_snapshot("SAMPLE", 65, entries))[0]
        for index in range(len(entries) - 1):
            reordered = list(entries)
            reordered[index], reordered[index + 1] = (
                reordered[index + 1].replace(
                    f":R={index + 1:02X}:".encode("ascii"),
                    f":R={index:02X}:".encode("ascii"),
                    1,
                ),
                reordered[index].replace(
                    f":R={index:02X}:".encode("ascii"),
                    f":R={index + 1:02X}:".encode("ascii"),
                    1,
                ),
            )
            with self.subTest(tie_breaker=index):
                actual = decoder.parse_hot(
                    hot_snapshot("SAMPLE", 65, reordered)
                )[0]
                self.assertEqual(actual, expected)

    def test_map_boundaries_counts_and_duplicates_fail_closed(self):
        cases = {
            "duplicate group": MAP
            + b"    1800     6000        4     4 .p2.overlay.group.00000001\n",
            "truncated stubs": MAP.replace(
                b"    1008     1008        4     1                 target_two\n", b""
            ),
            "stub boundary": MAP.replace(
                b"    1000     1000        c     4 .p2.overlay.stubs",
                b"    1000     1000        8     4 .p2.overlay.stubs",
            ),
            "group count": MAP.replace(
                b"__p2_overlay_group_count = 4",
                b"__p2_overlay_group_count = 5",
            ),
            "group count above Kconfig maximum": MAP.replace(
                b"__p2_overlay_group_count = 4",
                b"__p2_overlay_group_count = 1025",
            ),
            "body boundary": MAP.replace(
                b"    2000     4000       20     4 .p2.overlay.group.00000002",
                b"    2000     4000       10     4 .p2.overlay.group.00000002",
            ),
            "missing boundary": MAP.replace(
                b"    100c     100c        0     1         "
                b"__p2_overlay_stubs_end = ABSOLUTE ( . )\n",
                b"",
            ),
            "fixed overlap": MAP.replace(
                b"    1800     3000       30     4 .p2.overlay.group.00000001",
                b"    1ff0     3000       30     4 .p2.overlay.group.00000001",
            ),
            "no pageable group": MAP.replace(
                b"    2000     4000       20     4 .p2.overlay.group.00000002",
                b"    1700     4000       20     4 .p2.overlay.group.00000002",
            ).replace(
                b"    2000     5000        4     4 .p2.overlay.group.00000003",
                b"    1600     5000        4     4 .p2.overlay.group.00000003",
            ),
            "duplicate assignment": MAP
            + b"       0        0        0     1 P2_HUB_SIZE = 0x2040\n",
        }
        for name, map_data in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(decoder.DecodeError):
                    decoder.parse_map(map_data)

    def test_decode_rejects_unknown_empty_misaligned_and_unresolved_ids(self):
        index = decoder.parse_map(MAP)
        source = decoder.parse_hot(SERIAL)[0]
        first = source.entries[0]
        resident = source.entries[1]
        cases = {
            "unknown target group": replace(first, target_group=0xFFFFFFFF),
            "empty target group": replace(first, target_group=3),
            "unknown target stub": replace(first, target_stub=0xFFFFFFFF),
            "wrong target mapping": replace(first, target_group=1),
            "same group": replace(first, target_group=1, target_stub=0),
            "unknown caller group": replace(first, caller_group=0xFFFFFFFF),
            "empty caller group": replace(first, caller_group=3),
            "misaligned caller": replace(first, caller_offset=6),
            "unresolved caller": replace(first, caller_offset=0),
            "resident out of range": replace(resident, caller_offset=0x10000000),
        }
        for name, entry in cases.items():
            with self.subTest(name=name):
                snapshot = decoder.HotSnapshot("SAMPLE", entry.count, (entry,))
                with self.assertRaises(decoder.DecodeError):
                    decoder.decode(index, (snapshot,))

    def test_build_hil_and_serial_identity_binding(self):
        decoder.validate_evidence_binding(MAP, SERIAL, build_status(), hil_status())

        cases = {}
        bad = build_status()
        bad["status"] = "FAIL"
        cases["build not pass"] = (MAP, SERIAL, bad, hil_status())
        bad = build_status()
        bad["files"]["nuttx.map"]["sha256"] = "00" * 32
        cases["map hash"] = (MAP, SERIAL, bad, hil_status())
        bad_hil = hil_status()
        bad_hil["inputs"]["image_size"] += 1
        cases["artifact size"] = (MAP, SERIAL, build_status(), bad_hil)
        bad_hil = hil_status()
        bad_hil["inputs"]["resident_elf_sha256"] = "44" * 32
        cases["artifact hash"] = (MAP, SERIAL, build_status(), bad_hil)
        bad_hil = hil_status()
        bad_hil["serial_rx_bytes"] += 1
        cases["serial length"] = (MAP, SERIAL, build_status(), bad_hil)
        bad_serial = SERIAL.replace(b"SIZE=103", b"SIZE=104", 1)
        cases["upload size"] = (
            MAP,
            bad_serial,
            build_status(),
            hil_status(bad_serial),
        )
        bad_serial = SERIAL.replace(b"CRC=A1B2C3D4", b"CRC=00000000", 1)
        cases["upload crc"] = (
            MAP,
            bad_serial,
            build_status(),
            hil_status(bad_serial),
        )
        bad_serial = SERIAL.replace(
            b"P2PY:UPLOAD:PASS:SIZE=103:CRC=A1B2C3D4:RXDROPS=0\r\n", b""
        )
        cases["missing upload"] = (
            MAP,
            bad_serial,
            build_status(),
            hil_status(bad_serial),
        )

        for name, arguments in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(decoder.DecodeError):
                    decoder.validate_evidence_binding(*arguments)

    def _run_cli(self, json_output=False, mutate=None):
        map_data = MAP
        serial_data = SERIAL
        build = build_status(map_data)
        hil = hil_status(serial_data)
        if mutate is not None:
            mutate(build, hil)

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            map_path = root / "nuttx.map"
            serial_path = root / "serial.raw"
            build_path = root / "build-status.json"
            hil_path = root / "hil-status.json"
            map_path.write_bytes(map_data)
            serial_path.write_bytes(serial_data)
            build_path.write_text(json.dumps(build), encoding="utf-8")
            hil_path.write_text(json.dumps(hil), encoding="utf-8")
            command = [
                sys.executable,
                str(SCRIPT),
                "--map",
                str(map_path),
                "--serial-log",
                str(serial_path),
                "--build-status",
                str(build_path),
                "--hil-status",
                str(hil_path),
            ]
            if json_output:
                command.append("--json")
            return subprocess.run(command, capture_output=True, text=True)

    def test_cli_requires_bound_statuses_and_emits_text_and_json(self):
        text_result = self._run_cli()
        self.assertEqual(text_result.returncode, 0, text_result.stderr)
        self.assertIn("P2HOTDECODE:SNAP=00:STAGE=SAMPLE", text_result.stdout)

        json_result = self._run_cli(json_output=True)
        self.assertEqual(json_result.returncode, 0, json_result.stderr)
        decoded = json.loads(json_result.stdout)
        self.assertEqual(decoded[0]["entries"][0]["target"], "target_two")

        missing = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--map",
                "missing.map",
                "--serial-log",
                "missing.raw",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(missing.returncode, 2)
        self.assertIn("--build-status", missing.stderr)
        self.assertIn("--hil-status", missing.stderr)

    def test_cli_reports_binding_failure_without_output(self):
        def mutate(build, hil):
            build["files"]["nuttx.map"]["sha256"] = "00" * 32

        result = self._run_cli(mutate=mutate)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("passed map does not match build status", result.stderr)

    def test_json_status_duplicate_keys_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "status.json"
            path.write_text('{"format":"one","format":"two"}', encoding="utf-8")
            with self.assertRaisesRegex(decoder.DecodeError, "duplicate key"):
                decoder._read_json(path, "test status")


if __name__ == "__main__":
    unittest.main()
