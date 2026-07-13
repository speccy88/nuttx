#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

import flashboot_protocol as flashboot
import build_artifact
import flash_layout
import storage_protocol as storage


SEQUENCE = "1234ABCD"
BOOT_CRC = "89ABCDEF"


def make_build_artifact(root, image):
    root.mkdir()
    for name in build_artifact.PASS_REQUIRED_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name == "nuttx.bin":
            path.write_bytes(image)
        elif name == "nuttx":
            path.write_bytes(b"ELF flashboot fixture")
        elif name == "config":
            path.write_text("CONFIG_P2_SYSCLK_HZ=180000000\n", encoding="utf-8")
        elif name in ("nuttx-source-status.txt", "apps-source-status.txt"):
            path.write_text("", encoding="utf-8")
        else:
            path.write_text(name + "\n", encoding="utf-8")
    files = {
        path.relative_to(root).as_posix(): {
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in root.rglob("*") if path.is_file()
    }
    status = {
        "format": build_artifact.FORMAT,
        "status": "PASS",
        "exit_code": 0,
        "board": "p2-ec32mb",
        "profile": "flashboot",
        "started_utc": "2026-07-13T11:58:00.000Z",
        "ended_utc": "2026-07-13T11:59:00.000Z",
        "build_command": "tools/p2/build.sh flashboot",
        "nuttx_branch": "codex/test",
        "nuttx_commit": "1" * 40,
        "nuttx_commit_after": "1" * 40,
        "apps_path": "/tmp/apps",
        "apps_branch": "codex/test",
        "apps_commit": "2" * 40,
        "apps_commit_after": "2" * 40,
        "nuttx_source_clean": True,
        "apps_source_clean": True,
        "source_clean": True,
        "nuttx_source_clean": True,
        "apps_source_clean": True,
        "p2llvm_root": "/tmp/p2llvm",
        "compiler": "fixture clang",
        "jobs": 1,
        "board_clock_hz": 180000000,
        "binary_sha256": files["nuttx.bin"]["sha256"],
        "elf_sha256": files["nuttx"]["sha256"],
        "files": files,
    }
    (root / "status.json").write_text(json.dumps(status), encoding="utf-8")
    return build_artifact.load(root, require_clean=True)


def boot_text(crc=BOOT_CRC, flashboot_profile=True):
    lines = [
            "P2BOOT:ENTRY",
            "P2BOOT:DATA=OK",
            "P2BOOT:BSS=OK",
            "P2BOOT:NX_START",
            "P2STORAGE:W25=PRIVATE JEDEC=EF7018",
            "P2STORAGE:W25_FREQUENCY PROBE=400000 ACTIVE=2000000",
            "P2STORAGE:W25_GEOMETRY BLOCK=256 ERASE=4096 "
            "ERASEBLOCKS=4096 BYTES=16777216",
            "P2STORAGE:W25_LAYOUT BOOT=0x00000000+0x00080000 "
            "DATA=0x00080000+0x00F80000 FIRSTBLOCK=2048 NBLOCKS=63488",
            "P2STORAGE:W25_BOOT_CRC32={}".format(crc),
            "P2STORAGE:SMARTFS=/dev/smart0 AUTOFORMAT=NO",
            "P2STORAGE:MMCSD_FREQUENCY ID=400000 TRANSFER=2000000",
            "P2STORAGE:MMCSD=/dev/mmcsd0",
        ]
    if flashboot_profile:
        lines.append(flashboot.STARTUP_MOUNT_MARKER)
    lines.append("nsh> ")
    return "\r\n".join(lines)


def storage_response(action, stage, sequence=SEQUENCE, checksum=None):
    if checksum is None:
        checksum = storage.stream_checksum("flash", sequence)
    lines = [
        "P2STORAGE:BEGIN:COMMAND={}".format(action),
        "P2STORAGE:FLASH:{}:SEQUENCE={}:BYTES=1048576:FNV1A={}:PASS".format(
            stage, sequence, checksum
        ),
    ]
    if action == "flash-write":
        lines.append("P2STORAGE:READY:RESET=FLASH:SEQUENCE={}".format(sequence))
    lines.append("P2STORAGE:PASS:{}".format(action.upper()))
    return "\r\n".join(lines) + "\r\n"


def make_flash_artifact(root, cycles=1, crc=BOOT_CRC, response=None,
                        port="/dev/cu.fake-p2"):
    status = {
        "status": "PASS",
        "protocol": "storage",
        "storage_action": "flash-write",
        "storage_sequence": SEQUENCE,
        "cycles_requested": cycles,
        "cycles_passed": cycles,
        "image_sha256": "a" * 64,
        "port": port,
        "started_utc": "2026-07-13T12:00:00.000Z",
        "ended_utc": "2026-07-13T12:01:00.000Z",
    }
    (root / "status.json").write_text(
        json.dumps(status), encoding="utf-8"
    )
    for cycle in range(1, cycles + 1):
        cycle_dir = root / "cycle-{:03d}".format(cycle)
        cycle_dir.mkdir()
        (cycle_dir / "status.json").write_text(
            json.dumps({"status": "PASS"}), encoding="utf-8"
        )
        console = boot_text(crc=crc, flashboot_profile=False) + "\r\n" + (
            response
            if response is not None
            else storage_response("flash-write", "WRITE")
        )
        (cycle_dir / "console.raw").write_bytes(console.encode("utf-8"))
    return root


def make_program_artifact(root, erase_end=0x1000):
    inputs = root / "inputs"
    inputs.mkdir()
    image = b"P2 flashboot img"
    image_path = inputs / "flash-input.bin"
    image_path.write_bytes(image)
    manifest_path = inputs / "flash-input.bin.json"
    manifest_path.write_text(
        json.dumps(flash_layout.image_manifest(image)), encoding="utf-8"
    )
    build = make_build_artifact(inputs / "build", image)
    loader_source = pathlib.Path("/opt/p2/bin/loadp2")
    loader_copy = inputs / "loadp2"
    loader_copy.write_bytes(b"fixture sealed loadp2\n")
    loader_copy.chmod(0o755)
    loader_sha256 = hashlib.sha256(loader_copy.read_bytes()).hexdigest()
    (inputs / "toolchain.lock").write_text(
        "sha256={}  {}\n".format(loader_sha256, loader_source),
        encoding="utf-8",
    )
    loader_baud = 2000000
    port = "/dev/cu.fake-p2"
    status = {
        "status": "PASS",
        "action": "flash-program",
        "exit_code": 0,
        "port": port,
        "image_size": len(image),
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "program_range": "[0x00000000,0x00000400)",
        "erase_range": "[0x00000000,0x{:08x})".format(erase_end),
        "boot_partition_range": "[0x00000000,0x00080000)",
        "flash_write_gate": True,
        "flash_erase_gate": True,
        "shared_sd_write_gate": True,
        "reset_gate": True,
        "manifest_file": "inputs/flash-input.bin.json",
        "manifest_format": flash_layout.FLASH_INPUT_FORMAT,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "started_utc": "2026-07-13T12:02:00.000Z",
        "ended_utc": "2026-07-13T12:03:00.000Z",
        "build_artifact": str(build.path),
        "build_artifact_copy": "inputs/build",
        "build_status_sha256": build.status_sha256,
        "build_profile": build.profile,
        "build_nuttx_commit": build.nuttx_commit,
        "build_apps_commit": build.apps_commit,
        "board_clock_hz": build.board_clock_hz,
        "program_settle_seconds": 5,
        "loadp2": str(loader_source),
        "loadp2_sha256": loader_sha256,
        "loadp2_copy": "inputs/loadp2",
        "loader_baud": loader_baud,
        "loader_command_file": "command.json",
    }
    (root / "status.json").write_text(json.dumps(status), encoding="utf-8")
    command = {
        "loader_baud": loader_baud,
        "argv": [
            str(loader_copy.resolve()),
            "-p",
            port,
            "-l",
            str(loader_baud),
            "-DTR",
            "-SINGLE",
            "-FLASH",
            "-v",
            str(image_path.resolve()),
        ],
    }
    (root / "command.json").write_text(
        json.dumps(command), encoding="utf-8"
    )
    (root / "command.txt").write_text(
        " ".join(command["argv"]) + "\n", encoding="utf-8"
    )
    for name in ("loader.stdout", "loader.stderr", "layout.txt"):
        (root / name).write_text("", encoding="utf-8")
    return root


class FlashBootProtocolTests(unittest.TestCase):
    def test_program_artifact_pins_image_and_boot_partition_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = make_program_artifact(pathlib.Path(temporary))
            result = flashboot.load_program_artifact(root)
            self.assertEqual(result.erase_end, 0x1000)
            self.assertEqual(result.program_end, 0x400)

        with tempfile.TemporaryDirectory() as temporary:
            root = make_program_artifact(
                pathlib.Path(temporary), erase_end=0x81000
            )
            with self.assertRaisesRegex(
                flashboot.ProgramArtifactError, "crosses the data partition"
            ):
                flashboot.load_program_artifact(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = make_program_artifact(pathlib.Path(temporary))
            (root / "inputs/flash-input.bin").write_bytes(b"tampered")
            with self.assertRaisesRegex(
                flashboot.ProgramArtifactError, "size does not match"
            ):
                flashboot.load_program_artifact(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = make_program_artifact(pathlib.Path(temporary))
            command_path = root / "command.json"
            command = json.loads(command_path.read_text(encoding="utf-8"))
            command["argv"][2] = "/dev/cu.wrong-p2"
            command_path.write_text(json.dumps(command), encoding="utf-8")
            with self.assertRaisesRegex(
                flashboot.ProgramArtifactError, "exact sealed command"
            ):
                flashboot.load_program_artifact(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = make_program_artifact(pathlib.Path(temporary))
            (root / "inputs/loadp2").write_bytes(b"tampered loader")
            with self.assertRaisesRegex(
                flashboot.ProgramArtifactError, "loadp2 SHA-256"
            ):
                flashboot.load_program_artifact(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = make_program_artifact(pathlib.Path(temporary))
            (root / "inputs/toolchain.lock").write_text(
                "not pinned\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                flashboot.ProgramArtifactError, "does not pin loadp2"
            ):
                flashboot.load_program_artifact(root)

    def test_prior_pass_artifact_establishes_nonce_crc_and_exact_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = make_flash_artifact(pathlib.Path(temporary))

            result = flashboot.load_flash_artifact(root)

            self.assertEqual(result.sequence, SEQUENCE)
            self.assertEqual(result.boot_crc32, BOOT_CRC)
            self.assertEqual(result.image_sha256, "a" * 64)
            self.assertEqual(len(result.status_sha256), 64)

    def test_prior_artifact_must_be_complete_and_checksum_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = make_flash_artifact(
                pathlib.Path(temporary),
                response=storage_response(
                    "flash-write", "WRITE", checksum="00000000"
                ),
            )
            with self.assertRaisesRegex(
                flashboot.FlashArtifactError, "one-MiB write proof"
            ):
                flashboot.load_flash_artifact(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = make_flash_artifact(pathlib.Path(temporary))
            status_path = root / "status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["status"] = "FAIL"
            status_path.write_text(json.dumps(status), encoding="utf-8")
            with self.assertRaisesRegex(flashboot.FlashArtifactError, "PASS"):
                flashboot.load_flash_artifact(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = make_flash_artifact(pathlib.Path(temporary))
            console_path = root / "cycle-001" / "console.raw"
            text = console_path.read_text(encoding="utf-8").replace(
                "P2STORAGE:W25_FREQUENCY PROBE=400000 ACTIVE=2000000\n",
                "",
            )
            console_path.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(
                flashboot.FlashArtifactError, "storage-profile boot contract"
            ):
                flashboot.load_flash_artifact(root)

    def test_boot_requires_exact_order_and_crc_equality(self):
        valid = flashboot.parse_boot(boot_text(), BOOT_CRC)
        self.assertTrue(valid["complete"], valid)
        self.assertEqual(valid["boot_crc32"], BOOT_CRC)

        mismatch = flashboot.parse_boot(boot_text("89ABCDE0"), BOOT_CRC)
        self.assertFalse(mismatch["complete"])
        self.assertIn("boot CRC mismatch", mismatch["errors"][0])

        duplicate = flashboot.parse_boot(
            boot_text().replace("P2BOOT:DATA=OK", "P2BOOT:DATA=OK\r\nP2BOOT:DATA=OK"),
            BOOT_CRC,
        )
        self.assertIn("P2BOOT:DATA=OK", duplicate["duplicates"])

        out_of_order = flashboot.parse_boot(
            boot_text().replace(
                "P2BOOT:DATA=OK\r\nP2BOOT:BSS=OK",
                "P2BOOT:BSS=OK\r\nP2BOOT:DATA=OK",
            ),
            BOOT_CRC,
        )
        self.assertIn("out of order", out_of_order["errors"][0])

        no_mount = flashboot.parse_boot(
            boot_text().replace(flashboot.STARTUP_MOUNT_MARKER + "\r\n", ""),
            BOOT_CRC,
        )
        self.assertFalse(no_mount["complete"])
        self.assertIn(flashboot.STARTUP_MOUNT_MARKER, no_mount["missing"])

    def test_error_and_loader_signatures_are_strictly_rejected(self):
        for injected, kind in (
            ("ERROR: bad flash\r\n", "fatal: error"),
            ("Prop_Ver G\r\n", "loader: loader Prop_Ver response"),
            ("loadp2: Loading 100 bytes\r\n", "loader: loader name"),
            (
                "P2 version G found on serial port /dev/cu.fake\r\n",
                "loader: loader P2 version",
            ),
            ("\x00", "serial: NUL byte"),
        ):
            with self.subTest(injected=injected):
                result = flashboot.parse_boot(injected + boot_text(), BOOT_CRC)
                self.assertFalse(result["complete"])
                self.assertEqual(result["rejection"]["kind"], kind)

    def test_verify_requires_exact_one_mib_host_fnv_and_final_prompt(self):
        text = "nsh> \x1b[K\r\n" + storage_response(
            "flash-verify", "PERSISTENCE"
        )
        result = flashboot.parse_verify_response(text, SEQUENCE)
        self.assertTrue(result["complete"], result)
        self.assertEqual(result["expected_bytes"], 1048576)
        self.assertEqual(
            result["expected_fnv1a"], storage.stream_checksum("flash", SEQUENCE)
        )

        partial = text[: text.index("FNV1A=") + 2]
        partial_result = flashboot.parse_verify_response(partial, SEQUENCE)
        self.assertFalse(partial_result["complete"])
        self.assertNotIn(
            "flash persistence record is malformed", partial_result["errors"]
        )

        malformed_result = flashboot.parse_verify_response(
            partial + "BROKEN\r\n", SEQUENCE
        )
        self.assertIn(
            "flash persistence record is malformed", malformed_result["errors"]
        )

        for changed in (
            text.replace("BYTES=1048576", "BYTES=1048575"),
            text.replace(result["expected_fnv1a"], "00000000"),
            text.replace("P2STORAGE:PASS:FLASH-VERIFY", ""),
            "P2BOOT:ENTRY\r\n" + text,
        ):
            with self.subTest(changed=changed[-80:]):
                self.assertFalse(
                    flashboot.parse_verify_response(changed, SEQUENCE)["complete"]
                )


if __name__ == "__main__":
    unittest.main()
