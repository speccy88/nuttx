#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

import ast
import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

P2_TOOLS = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(P2_TOOLS))

import flashboot_protocol  # noqa: E402
import build_artifact  # noqa: E402
import flash_layout  # noqa: E402
import storage_protocol  # noqa: E402


SCRIPT_PATH = P2_TOOLS / "test-flashboot.py"
SPEC = importlib.util.spec_from_file_location("p2_test_flashboot", SCRIPT_PATH)
flashboot_script = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = flashboot_script
SPEC.loader.exec_module(flashboot_script)

SEQUENCE = "1234ABCD"
BOOT_CRC = "89ABCDEF"
FLASHBOOT_CRC = "76543210"


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
        elif name == "toolchain.lock":
            path.write_text(
                "nuttx_commit={}\nnuttx_apps_commit={}\n".format("1" * 40, "2" * 40),
                encoding="utf-8",
            )
        elif name in ("nuttx-source-status.txt", "apps-source-status.txt"):
            path.write_text("", encoding="utf-8")
        else:
            path.write_text(name + "\n", encoding="utf-8")
    files = {
        path.relative_to(root).as_posix(): {
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in root.rglob("*")
        if path.is_file()
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
        lines.append(flashboot_protocol.STARTUP_MOUNT_MARKER)
    lines.append("nsh> ")
    return "\r\n".join(lines)


def response(action, stage):
    checksum = storage_protocol.stream_checksum("flash", SEQUENCE)
    lines = [
        "P2STORAGE:BEGIN:COMMAND={}".format(action),
        "P2STORAGE:FLASH:{}:SEQUENCE={}:BYTES=1048576:FNV1A={}:PASS".format(
            stage, SEQUENCE, checksum
        ),
    ]
    if action == "flash-write":
        lines.append("P2STORAGE:READY:RESET=FLASH:SEQUENCE={}".format(SEQUENCE))
    lines.append("P2STORAGE:PASS:{}".format(action.upper()))
    return "\r\n".join(lines) + "\r\n"


def make_flash_artifact(root, port="/dev/fake-p2"):
    root.mkdir()
    status = {
        "status": "PASS",
        "protocol": "storage",
        "storage_action": "flash-write",
        "storage_sequence": SEQUENCE,
        "cycles_requested": 1,
        "cycles_passed": 1,
        "image_sha256": "a" * 64,
        "port": port,
        "started_utc": "2026-07-13T12:00:00.000Z",
        "ended_utc": "2026-07-13T12:01:00.000Z",
    }
    (root / "status.json").write_text(json.dumps(status), encoding="utf-8")
    cycle = root / "cycle-001"
    cycle.mkdir()
    (cycle / "status.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
    (cycle / "console.raw").write_bytes(
        (
            boot_text(flashboot_profile=False)
            + "\r\n"
            + response("flash-write", "WRITE")
        ).encode("utf-8")
    )
    return flashboot_protocol.load_flash_artifact(root)


def make_program_artifact(root, port="/dev/fake-p2"):
    root.mkdir()
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
    status = {
        "status": "PASS",
        "action": "flash-program",
        "exit_code": 0,
        "port": port,
        "image_size": len(image),
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "program_range": "[0x00000000,0x00000400)",
        "erase_range": "[0x00000000,0x00001000)",
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
    (root / "command.json").write_text(json.dumps(command), encoding="utf-8")
    (root / "command.txt").write_text(
        " ".join(command["argv"]) + "\n", encoding="utf-8"
    )
    (root / "loader.stdout").write_text("verified\n", encoding="utf-8")
    (root / "loader.stderr").write_text("", encoding="utf-8")
    (root / "layout.txt").write_text("validated\n", encoding="utf-8")
    return flashboot_protocol.load_program_artifact(root)


class FakeLock:
    enters = 0

    def __init__(self, path, **arguments):
        self.path = path
        self.arguments = arguments

    def __enter__(self):
        type(self).enters += 1
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None


class FakeSerial:
    def __init__(self, loader_cycle=None, crc_cycle=None, interrupt_cycle=None):
        self.is_open = True
        self._dtr = False
        self.dtr_transitions = []
        self.pending_reset = False
        self.reset_count = 0
        self.queue = []
        self.writes = []
        self.write_after_prompt = []
        self.flushes = 0
        self.close_count = 0
        self.prompt_delivered = False
        self.loader_cycle = loader_cycle
        self.crc_cycle = crc_cycle
        self.interrupt_cycle = interrupt_cycle
        self.steady_crc = FLASHBOOT_CRC

    @property
    def dtr(self):
        return self._dtr

    @dtr.setter
    def dtr(self, value):
        self._dtr = value
        self.dtr_transitions.append(value)
        if self.dtr_transitions[-3:] == [True, False, True]:
            self.pending_reset = True

    def reset_input_buffer(self):
        self.queue = []
        if not self.pending_reset:
            raise AssertionError("input flushed without a complete DTR pulse")
        self.pending_reset = False
        self.reset_count += 1
        crc = "76543211" if self.crc_cycle == self.reset_count else self.steady_crc
        prefix = "Prop_Ver G\r\n" if self.loader_cycle == self.reset_count else ""
        self.queue.append((prefix + boot_text(crc)).encode("utf-8"))
        self.prompt_delivered = False

    def read(self, size):
        if self.interrupt_cycle == self.reset_count:
            self.interrupt_cycle = None
            raise KeyboardInterrupt()
        if not self.queue:
            return b""
        value = self.queue.pop(0)
        if len(value) > size:
            self.queue.insert(0, value[size:])
            value = value[:size]
        if b"nsh> " in value:
            self.prompt_delivered = True
        return value

    def write(self, value):
        self.write_after_prompt.append(self.prompt_delivered)
        self.writes.append(value)
        if value != storage_protocol.command_bytes("flash-verify", SEQUENCE):
            raise AssertionError("unexpected target command")
        self.queue.append(
            ("nsh> \x1b[K\r\n" + response("flash-verify", "PERSISTENCE")).encode(
                "utf-8"
            )
        )
        return len(value)

    def flush(self):
        self.flushes += 1

    def close(self):
        self.close_count += 1
        self.is_open = False


class SerialFactory:
    def __init__(self, connection):
        self.connection = connection
        self.calls = []

    def __call__(self, **arguments):
        self.calls.append(arguments)
        return self.connection


class FlashBootRunnerTests(unittest.TestCase):
    def setUp(self):
        FakeLock.enters = 0

    def test_twenty_resets_share_one_connection_and_never_send_early(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = make_flash_artifact(root / "prior")
            output = root / "result"
            connection = FakeSerial()
            factory = SerialFactory(connection)
            config = flashboot_script.FlashBootConfig(
                port="/dev/fake-p2",
                artifact_dir=output,
                flash_artifact=source,
                program_artifact=make_program_artifact(root / "program"),
                board_lock=root / "board.lock",
            )
            runner = flashboot_script.FlashBootRunner(
                config,
                serial_factory=factory,
                lock_factory=FakeLock,
                sleep=lambda duration: None,
            )

            self.assertTrue(runner.run(), runner.last_reason)

            self.assertEqual(len(factory.calls), 1)
            self.assertEqual(connection.close_count, 1)
            self.assertEqual(connection.reset_count, 20)
            self.assertEqual(connection.dtr_transitions, [True, False, True] * 20)
            self.assertEqual(len(connection.writes), 20)
            self.assertEqual(connection.write_after_prompt, [True] * 20)
            self.assertEqual(FakeLock.enters, 1)

            status = json.loads((output / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "PASS")
            self.assertEqual(status["cycles_passed"], 20)
            self.assertEqual(status["serial_connections_opened"], 1)
            self.assertEqual(status["flash_verify_bytes"], 1048576)
            self.assertEqual(status["pre_program_boot_crc32"], BOOT_CRC)
            self.assertEqual(status["flashboot_crc32"], FLASHBOOT_CRC)
            evidence = status["prerequisite_evidence"]
            self.assertEqual(evidence["format"], "p2-flashboot-prerequisites-v1")
            self.assertGreaterEqual(len(evidence["files"]), 9)
            self.assertTrue(
                (output / "prerequisites/flash-write/cycle-001/console.raw").is_file()
            )
            self.assertTrue(
                (
                    output / "prerequisites/flash-program/inputs/flash-input.bin.json"
                ).is_file()
            )
            self.assertTrue(
                (output / "prerequisites/flash-program/command.json").is_file()
            )
            self.assertTrue(
                (output / "prerequisites/flash-program/inputs/loadp2").is_file()
            )
            self.assertTrue(
                (output / "prerequisites/flash-program/inputs/toolchain.lock").is_file()
            )
            for cycle in range(1, 21):
                cycle_dir = output / "cycle-{:03d}".format(cycle)
                self.assertTrue((cycle_dir / "console.raw").is_file())
                cycle_status = json.loads(
                    (cycle_dir / "status.json").read_text(encoding="utf-8")
                )
                self.assertEqual(cycle_status["status"], "PASS")
                self.assertEqual(cycle_status["pre_prompt_tx_bytes"], 0)

    def test_loader_signature_fails_immediately_and_preserves_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = make_flash_artifact(root / "prior")
            output = root / "result"
            connection = FakeSerial(loader_cycle=3)
            runner = flashboot_script.FlashBootRunner(
                flashboot_script.FlashBootConfig(
                    port="/dev/fake-p2",
                    artifact_dir=output,
                    flash_artifact=source,
                    program_artifact=make_program_artifact(root / "program"),
                    board_lock=root / "board.lock",
                ),
                serial_factory=SerialFactory(connection),
                lock_factory=FakeLock,
                sleep=lambda duration: None,
            )

            self.assertFalse(runner.run())

            status = json.loads((output / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "FAIL")
            self.assertEqual(status["cycles_passed"], 2)
            self.assertIn("loader Prop_Ver", status["failure_reason"])
            self.assertEqual(len(connection.writes), 2)
            self.assertTrue((output / "cycle-003" / "console.raw").is_file())
            failed = json.loads(
                (output / "cycle-003" / "status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(failed["status"], "FAIL")
            self.assertFalse((output / "cycle-004").exists())

    def test_crc_mismatch_never_reaches_the_transmit_point(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = make_flash_artifact(root / "prior")
            output = root / "result"
            connection = FakeSerial(crc_cycle=3)
            runner = flashboot_script.FlashBootRunner(
                flashboot_script.FlashBootConfig(
                    port="/dev/fake-p2",
                    artifact_dir=output,
                    flash_artifact=source,
                    program_artifact=make_program_artifact(root / "program"),
                    board_lock=root / "board.lock",
                ),
                serial_factory=SerialFactory(connection),
                lock_factory=FakeLock,
                sleep=lambda duration: None,
            )

            self.assertFalse(runner.run())
            self.assertEqual(len(connection.writes), 2)
            self.assertIn("boot CRC mismatch", runner.last_reason)

    def test_cycle_one_must_prove_programmed_crc_changed_before_tx(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = make_flash_artifact(root / "prior")
            output = root / "result"
            connection = FakeSerial()
            connection.steady_crc = BOOT_CRC
            runner = flashboot_script.FlashBootRunner(
                flashboot_script.FlashBootConfig(
                    port="/dev/fake-p2",
                    artifact_dir=output,
                    flash_artifact=source,
                    program_artifact=make_program_artifact(root / "program"),
                    board_lock=root / "board.lock",
                ),
                serial_factory=SerialFactory(connection),
                lock_factory=FakeLock,
                sleep=lambda duration: None,
            )

            self.assertFalse(runner.run())
            self.assertEqual(connection.writes, [])
            self.assertIn("did not change", runner.last_reason)

    def test_dry_run_validates_source_without_opening_or_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            make_flash_artifact(root / "prior", port="/dev/not-used")
            make_program_artifact(root / "program", port="/dev/not-used")
            output = root / "must-not-exist"

            def forbidden_factory(**arguments):
                raise AssertionError("dry-run opened serial")

            result = flashboot_script.main(
                [
                    "--port",
                    "/dev/not-used",
                    "--flash-artifact",
                    str(root / "prior"),
                    "--program-artifact",
                    str(root / "program"),
                    "--artifact-dir",
                    str(output),
                ],
                environment={},
                serial_factory=forbidden_factory,
                lock_factory=FakeLock,
            )

            self.assertEqual(result, 0)
            self.assertFalse(output.exists())
            self.assertEqual(FakeLock.enters, 0)

    def test_execute_requires_hil_and_reset_gates_before_opening_serial(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            make_flash_artifact(root / "prior", port="/dev/null")
            make_program_artifact(root / "program", port="/dev/null")
            output = root / "must-not-exist"

            def forbidden_factory(**arguments):
                raise AssertionError("disabled execute opened serial")

            result = flashboot_script.main(
                [
                    "--execute",
                    "--port",
                    "/dev/null",
                    "--flash-artifact",
                    str(root / "prior"),
                    "--program-artifact",
                    str(root / "program"),
                    "--artifact-dir",
                    str(output),
                ],
                environment={"P2_HIL": "0"},
                serial_factory=forbidden_factory,
                lock_factory=FakeLock,
            )

            self.assertEqual(result, flashboot_script.EXIT_SAFETY)
            self.assertFalse(output.exists())

            result = flashboot_script.main(
                [
                    "--execute",
                    "--port",
                    "/dev/null",
                    "--flash-artifact",
                    str(root / "prior"),
                    "--program-artifact",
                    str(root / "program"),
                    "--artifact-dir",
                    str(output),
                ],
                environment={"P2_HIL": "1", "P2_ALLOW_RESET": "0"},
                serial_factory=forbidden_factory,
                lock_factory=FakeLock,
            )

            self.assertEqual(result, flashboot_script.EXIT_SAFETY)
            self.assertFalse(output.exists())

    def test_runner_source_has_no_subprocess_import_or_call(self):
        tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
        imports = []
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name):
                    calls.append("{}.{}".format(node.func.value.id, node.func.attr))
        self.assertNotIn("subprocess", imports)
        self.assertFalse(any(call.startswith("subprocess.") for call in calls))

    def test_keyboard_interrupt_finalizes_cycle_and_returns_130(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            make_flash_artifact(root / "prior", port="/dev/null")
            make_program_artifact(root / "program", port="/dev/null")
            output = root / "result"
            connection = FakeSerial(interrupt_cycle=2)

            result = flashboot_script.main(
                [
                    "--execute",
                    "--port",
                    "/dev/null",
                    "--flash-artifact",
                    str(root / "prior"),
                    "--program-artifact",
                    str(root / "program"),
                    "--artifact-dir",
                    str(output),
                ],
                environment={"P2_HIL": "1", "P2_ALLOW_RESET": "1"},
                serial_factory=SerialFactory(connection),
                lock_factory=FakeLock,
            )

            self.assertEqual(result, flashboot_script.EXIT_INTERRUPTED)
            root_status = json.loads(
                (output / "status.json").read_text(encoding="utf-8")
            )
            cycle_status = json.loads(
                (output / "cycle-002/status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(root_status["status"], "FAIL")
            self.assertTrue(root_status["interrupted"])
            self.assertEqual(cycle_status["status"], "FAIL")
            self.assertTrue(cycle_status["interrupted"])


if __name__ == "__main__":
    unittest.main()
