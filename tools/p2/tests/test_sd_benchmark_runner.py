# SPDX-License-Identifier: Apache-2.0

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

P2_TOOLS = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(P2_TOOLS))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import monitor  # noqa: E402
import sd_benchmark_protocol as benchmark  # noqa: E402
from test_sd_benchmark_protocol import SEQUENCE, complete_log  # noqa: E402

SCRIPT_PATH = P2_TOOLS / "test-sd-benchmark.py"
SPEC = importlib.util.spec_from_file_location("p2_test_sd_benchmark", SCRIPT_PATH)
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


class FakeSerial:
    def __init__(self, payload):
        self.payloads = [payload]
        self.writes = []
        self.closed = False
        self.flushes = 0

    def read(self, size):
        del size
        return self.payloads.pop(0) if self.payloads else b""

    def write(self, data):
        value = bytes(data)
        self.writes.append(value)
        return len(value)

    def flush(self):
        self.flushes += 1

    def close(self):
        self.closed = True


class SerialFactory:
    def __init__(self, serial_port):
        self.serial_port = serial_port
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.serial_port


class FakeLoadp2Session:
    def __init__(self, transcript, on_write=None):
        self.payloads = [b"loadp2: RAM download complete\r\nnsh> "]
        self.transcript = transcript
        self.on_write = on_write
        self.writes = []
        self.returncode = None
        self.closed = False
        self.terminated = False

    def read(self, timeout):
        del timeout
        return self.payloads.pop(0) if self.payloads else b""

    def poll(self):
        return self.returncode

    def write(self, data):
        value = bytes(data)
        self.writes.append(value)
        if self.on_write is not None:
            self.on_write()
        self.payloads.append(self.transcript)

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        del timeout
        return self.returncode

    def close(self):
        self.closed = True


class Loadp2Factory:
    def __init__(self, transcript, on_write=None):
        self.transcript = transcript
        self.on_write = on_write
        self.calls = []
        self.sessions = []

    def __call__(self, command):
        self.calls.append(tuple(command))
        session = FakeLoadp2Session(self.transcript, self.on_write)
        self.sessions.append(session)
        return session


class SdBenchmarkRunnerTests(unittest.TestCase):
    @staticmethod
    def make_bound_fixture(directory):
        build = directory / "build"
        build.mkdir()
        image = build / "nuttx"
        image.write_bytes(b"\x7fELFbound-image")
        generated_config = build / "config"
        config_lines = []
        for name, value in runner.BOUND_REQUIRED_CONFIG:
            if value == "n":
                config_lines.append("# {} is not set".format(name))
            else:
                config_lines.append("{}={}".format(name, value))
        generated_config.write_text("\n".join(config_lines) + "\n", encoding="utf-8")

        loadp2 = directory / "loadp2"
        loadp2.write_bytes(b"#!/bin/sh\nexit 0\n")
        loadp2.chmod(0o755)
        toolchain_lock = build / "toolchain.lock"
        toolchain_lock.write_text(
            "nuttx_commit={}\nnuttx_apps_commit={}\nsha256={}  {}\n".format(
                "1" * 40, "2" * 40, runner.sha256_file(loadp2), loadp2
            ),
            encoding="utf-8",
        )
        for name in runner.build_artifact.PASS_REQUIRED_FILES:
            path = build / name
            if path.exists():
                continue
            if name in ("nuttx-source-status.txt", "apps-source-status.txt"):
                path.write_text("", encoding="utf-8")
            elif name == "nuttx.bin":
                path.write_bytes(b"bound-binary")
            else:
                path.write_text("fixture {}\n".format(name), encoding="utf-8")

        files = {}
        for path in sorted(build.iterdir()):
            if path.name == "status.json":
                continue
            files[path.name] = {
                "size": path.stat().st_size,
                "sha256": runner.sha256_file(path),
            }
        (build / "status.json").write_text(
            json.dumps(
                {
                    "format": runner.build_artifact.FORMAT,
                    "status": "PASS",
                    "exit_code": 0,
                    "board": "p2-ec32mb",
                    "profile": "sdio-record",
                    "started_utc": "2026-07-17T00:00:00Z",
                    "ended_utc": "2026-07-17T00:01:00Z",
                    "nuttx_branch": "codex/test",
                    "nuttx_commit": "1" * 40,
                    "nuttx_commit_after": "1" * 40,
                    "apps_branch": "codex/test",
                    "apps_commit": "2" * 40,
                    "apps_commit_after": "2" * 40,
                    "nuttx_source_clean": True,
                    "apps_source_clean": True,
                    "source_clean": True,
                    "board_clock_hz": 360_000_000,
                    "binary_sha256": files["nuttx.bin"]["sha256"],
                    "elf_sha256": files["nuttx"]["sha256"],
                    "files": files,
                }
            ),
            encoding="utf-8",
        )
        env = {
            "P2_HIL": "1",
            "P2_ALLOW_RESET": "1",
            "P2_PORT": "/dev/fake-p2",
            "P2_RESET_METHOD": "loadp2",
            "P2_LOADER_BAUD": "2000000",
            "P2_CONSOLE_BAUD": "230400",
            "LOADP2": str(loadp2),
        }
        return build, generated_config, env

    def test_dry_run_never_opens_serial_or_creates_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = pathlib.Path(directory) / "must-not-exist"
            serial_factory = mock.Mock(side_effect=AssertionError("serial opened"))
            stderr = io.StringIO()
            with mock.patch.dict(
                os.environ, {"P2_HIL": "1"}
            ), contextlib.redirect_stderr(stderr):
                status = runner.main(
                    [
                        "--sequence",
                        SEQUENCE,
                        "--port",
                        "/dev/fake-p2",
                        "--artifact-dir",
                        str(artifact),
                    ],
                    serial_factory=serial_factory,
                )

            self.assertEqual(status, runner.EXIT_SAFETY)
            self.assertFalse(artifact.exists())
            serial_factory.assert_not_called()
            self.assertIn("DRY-RUN", stderr.getvalue())
            self.assertIn(
                "p2storage sd-benchmark-read 1234ABCD 268435456 7",
                stderr.getvalue(),
            )

    def test_execute_still_requires_hil_environment_before_artifact_or_serial(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = pathlib.Path(directory) / "must-not-exist"
            serial_factory = mock.Mock(side_effect=AssertionError("serial opened"))
            with mock.patch.dict(os.environ, {"P2_HIL": "0"}):
                status = runner.main(
                    [
                        "--execute",
                        "--sequence",
                        SEQUENCE,
                        "--port",
                        "/dev/fake-p2",
                        "--artifact-dir",
                        str(artifact),
                    ],
                    serial_factory=serial_factory,
                )

            self.assertEqual(status, runner.EXIT_SAFETY)
            self.assertFalse(artifact.exists())
            serial_factory.assert_not_called()

    def test_ram_load_is_dry_run_without_execute(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = pathlib.Path(directory) / "must-not-exist"
            process_factory = mock.Mock(side_effect=AssertionError("loader started"))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = runner.main(
                    [
                        "--ram-load",
                        "--sequence",
                        SEQUENCE,
                        "--artifact-dir",
                        str(artifact),
                    ],
                    process_factory=process_factory,
                )

            self.assertEqual(status, runner.EXIT_SAFETY)
            self.assertFalse(artifact.exists())
            process_factory.assert_not_called()
            self.assertIn("DRY-RUN", stderr.getvalue())
            self.assertIn("P2_ALLOW_RESET=1", stderr.getvalue())

    def test_ram_load_requires_explicit_reset_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            build, _generated_config, env = self.make_bound_fixture(directory)
            env["P2_ALLOW_RESET"] = "0"
            artifact = directory / "must-not-exist"
            process_factory = mock.Mock(side_effect=AssertionError("loader started"))
            status = runner.main(
                [
                    "--execute",
                    "--ram-load",
                    "--port",
                    env["P2_PORT"],
                    "--build-artifact",
                    str(build),
                    "--artifact-dir",
                    str(artifact),
                ],
                env=env,
                process_factory=process_factory,
                port_validator=lambda _port: True,
            )

            self.assertEqual(status, runner.EXIT_SAFETY)
            self.assertFalse(artifact.exists())
            process_factory.assert_not_called()

    def test_ram_load_requires_port_to_exactly_match_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            build, _generated_config, env = self.make_bound_fixture(directory)
            artifact = directory / "must-not-exist"
            process_factory = mock.Mock(side_effect=AssertionError("loader started"))
            status = runner.main(
                [
                    "--execute",
                    "--ram-load",
                    "--port",
                    "/dev/different-p2",
                    "--build-artifact",
                    str(build),
                    "--artifact-dir",
                    str(artifact),
                ],
                env=env,
                process_factory=process_factory,
                port_validator=lambda _port: True,
            )

            self.assertEqual(status, runner.EXIT_SAFETY)
            self.assertFalse(artifact.exists())
            process_factory.assert_not_called()

    def test_ram_load_requires_canonical_record_workload_before_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            build, _generated_config, env = self.make_bound_fixture(directory)
            artifact = directory / "must-not-exist"
            process_factory = mock.Mock(side_effect=AssertionError("loader started"))
            status = runner.main(
                [
                    "--execute",
                    "--ram-load",
                    "--bytes",
                    str(benchmark.MIN_BYTES),
                    "--port",
                    env["P2_PORT"],
                    "--build-artifact",
                    str(build),
                    "--artifact-dir",
                    str(artifact),
                ],
                env=env,
                process_factory=process_factory,
                port_validator=lambda _port: True,
            )

            self.assertEqual(status, runner.EXIT_SAFETY)
            self.assertFalse(artifact.exists())
            process_factory.assert_not_called()

    def test_ram_load_rejects_loader_not_pinned_by_toolchain_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            build, _generated_config, env = self.make_bound_fixture(directory)
            (build / "toolchain.lock").write_text(
                "loadp2_commit=not-a-pin\n", encoding="utf-8"
            )
            artifact = directory / "must-not-exist"
            process_factory = mock.Mock(side_effect=AssertionError("loader started"))
            status = runner.main(
                [
                    "--execute",
                    "--ram-load",
                    "--port",
                    env["P2_PORT"],
                    "--build-artifact",
                    str(build),
                    "--artifact-dir",
                    str(artifact),
                ],
                env=env,
                process_factory=process_factory,
                port_validator=lambda _port: True,
            )

            self.assertEqual(status, runner.EXIT_SAFETY)
            self.assertFalse(artifact.exists())
            process_factory.assert_not_called()

    def test_ram_load_binds_one_pinned_ram_only_terminal_session(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            build, generated_config, env = self.make_bound_fixture(directory)
            image = build / "nuttx"
            artifact = directory / "bound-proof"
            board_lock = directory / "board.lock"
            process_factory = Loadp2Factory(complete_log().encode("ascii"))
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = runner.main(
                    [
                        "--execute",
                        "--ram-load",
                        "--sequence",
                        SEQUENCE,
                        "--port",
                        env["P2_PORT"],
                        "--build-artifact",
                        str(build),
                        "--artifact-dir",
                        str(artifact),
                        "--lock-file",
                        str(board_lock),
                    ],
                    env=env,
                    process_factory=process_factory,
                    owner_probe=lambda _port: (),
                    port_validator=lambda _port: True,
                )

            self.assertEqual(status, runner.EXIT_OK, stderr.getvalue())
            self.assertEqual(len(process_factory.calls), 1)
            load_command = process_factory.calls[0]
            self.assertEqual(load_command.count("-DTR"), 1)
            self.assertEqual(load_command.count("-RTS"), 0)
            self.assertNotIn("-FLASH", load_command)
            self.assertEqual(load_command[-2:], ("-t", str(image.resolve())))
            session = process_factory.sessions[0]
            self.assertEqual(
                session.writes,
                [benchmark.command_bytes(SEQUENCE, benchmark.DEFAULT_BYTES, 7)],
            )
            self.assertTrue(session.terminated)
            self.assertTrue(session.closed)

            status_json = json.loads((artifact / "status.json").read_text())
            metadata = json.loads((artifact / "metadata.json").read_text())
            command_json = json.loads((artifact / "loadp2-command.json").read_text())
            self.assertEqual(status_json["status"], "PASS")
            self.assertTrue(status_json["proof_complete"])
            self.assertTrue(metadata["target_image_loaded_by_this_tool"])
            self.assertEqual(metadata["capture_mode"], "LIVE_RAM_LOADED_BOUND")
            self.assertEqual(metadata["image_sha256"], runner.sha256_file(image))
            self.assertEqual(
                metadata["config_sha256"], runner.sha256_file(generated_config)
            )
            self.assertEqual(command_json["argv"], list(load_command))
            self.assertTrue((artifact / "loadp2-transcript.raw").is_file())
            self.assertTrue((artifact / "bound-inputs/nuttx").is_file())
            self.assertTrue((artifact / "bound-inputs/.config").is_file())
            self.assertTrue((artifact / "bound-inputs/loadp2").is_file())
            self.assertTrue((artifact / "bound-inputs/build-status.json").is_file())
            self.assertIn("P2 SD PROOF PASS - IMAGE BOUND", stdout.getvalue())

    def test_ram_load_cross_checks_bound_build_and_driver_telemetry(self):
        alternate_clock = complete_log()
        alternate_clock = alternate_clock.replace(
            "SYSCLK_HZ=360000000", "SYSCLK_HZ=340000000"
        ).replace("FREQUENCY_HZ=360000000", "FREQUENCY_HZ=340000000")
        alternate_clock = alternate_clock.replace(
            "REQUESTED_BUS_CLOCK_HZ=120000000",
            "REQUESTED_BUS_CLOCK_HZ=170000000",
        ).replace("BUS_CLOCK_HZ=120000000", "BUS_CLOCK_HZ=170000000")
        alternate_clock = alternate_clock.replace(
            "ACTIVE_DIVISOR=3", "ACTIVE_DIVISOR=2"
        ).replace("RAW_CEILING_BPS=60000000", "RAW_CEILING_BPS=85000000")
        for usec in [6_400_000 - index * 200_000 for index in range(7)]:
            alternate_clock = alternate_clock.replace(
                "CYCLES={}:".format(usec * 360),
                "CYCLES={}:".format(usec * 340),
            )

        cases = (
            ("clock", alternate_clock, "sysclk_hz"),
            (
                "driver",
                complete_log().replace(
                    "DRIVER=P2-SDIO-STREAMER", "DRIVER=P2-SDIO-OTHER", 1
                ),
                "driver",
            ),
            (
                "build",
                complete_log().replace("BUILD=1111111111", "BUILD=3333333333", 1),
                "NuttX commit",
            ),
        )
        for name, transcript, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                directory = pathlib.Path(directory)
                build, _generated_config, env = self.make_bound_fixture(directory)
                artifact = directory / "mismatch-proof"
                status = runner.main(
                    [
                        "--execute",
                        "--ram-load",
                        "--sequence",
                        SEQUENCE,
                        "--port",
                        env["P2_PORT"],
                        "--build-artifact",
                        str(build),
                        "--artifact-dir",
                        str(artifact),
                        "--lock-file",
                        str(directory / "board.lock"),
                    ],
                    env=env,
                    process_factory=Loadp2Factory(transcript.encode("ascii")),
                    owner_probe=lambda _port: (),
                    port_validator=lambda _port: True,
                )

                self.assertEqual(status, runner.EXIT_PROOF_FAILED)
                evidence = json.loads((artifact / "result.json").read_text())
                self.assertFalse(evidence["proof_complete"])
                self.assertEqual(evidence["status"], "FAIL")
                self.assertIn(expected, " ".join(evidence["errors"]))

    def test_ram_load_refuses_proof_if_config_changes_during_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            build, generated_config, env = self.make_bound_fixture(directory)
            artifact = directory / "changed-input-proof"

            def mutate_config():
                with generated_config.open("a", encoding="utf-8") as output:
                    output.write("# changed during capture\n")

            process_factory = Loadp2Factory(
                complete_log().encode("ascii"), on_write=mutate_config
            )
            status = runner.main(
                [
                    "--execute",
                    "--ram-load",
                    "--sequence",
                    SEQUENCE,
                    "--port",
                    env["P2_PORT"],
                    "--build-artifact",
                    str(build),
                    "--artifact-dir",
                    str(artifact),
                    "--lock-file",
                    str(directory / "board.lock"),
                ],
                env=env,
                process_factory=process_factory,
                owner_probe=lambda _port: (),
                port_validator=lambda _port: True,
            )

            self.assertEqual(status, runner.EXIT_SAFETY)
            metadata = json.loads((artifact / "metadata.json").read_text())
            status_json = json.loads((artifact / "status.json").read_text())
            self.assertFalse(metadata["target_image_loaded_by_this_tool"])
            self.assertFalse(status_json["proof_complete"])
            self.assertEqual(status_json["status"], "FAIL")

    def test_offline_parse_infers_parameters_and_can_seal_an_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            source = directory / "captured.raw"
            source.write_text(complete_log(), encoding="utf-8")
            artifact = directory / "proof"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                status = runner.main(
                    [
                        "--parse-log",
                        str(source),
                        "--artifact-dir",
                        str(artifact),
                    ]
                )

            self.assertEqual(status, runner.EXIT_IMAGE_UNVERIFIED)
            result = json.loads(stdout.getvalue())
            self.assertTrue(result["measurement_complete"])
            self.assertFalse(result["proof_complete"])
            self.assertEqual(
                result["evidence_status"], "MEASUREMENT_PASS_IMAGE_UNVERIFIED"
            )
            self.assertEqual(result["status"], "MEASUREMENT_PASS_IMAGE_UNVERIFIED")
            self.assertEqual(result["sequence"], SEQUENCE)
            self.assertEqual(
                json.loads((artifact / "status.json").read_text())["status"],
                "MEASUREMENT_PASS_IMAGE_UNVERIFIED",
            )
            metadata = json.loads((artifact / "metadata.json").read_text())
            self.assertEqual(metadata["capture_mode"], "OFFLINE_PARSE")
            self.assertFalse(metadata["target_image_loaded_by_this_tool"])
            self.assertIn("sha256", metadata["console_raw"])
            self.assertTrue((artifact / "inputs/sd_benchmark_protocol.py").is_file())

    def test_live_serial_capture_sends_only_read_command_and_writes_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            artifact = directory / "live-proof"
            lock = directory / "board.lock"
            serial_port = FakeSerial(complete_log().encode("ascii"))
            serial_factory = SerialFactory(serial_port)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.dict(
                os.environ, {"P2_HIL": "1"}
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = runner.main(
                    [
                        "--execute",
                        "--sequence",
                        SEQUENCE,
                        "--port",
                        "/dev/fake-p2",
                        "--artifact-dir",
                        str(artifact),
                        "--lock-file",
                        str(lock),
                        "--quiet",
                    ],
                    serial_factory=serial_factory,
                    serial_exceptions=(OSError,),
                )

            self.assertEqual(status, runner.EXIT_IMAGE_UNVERIFIED, stderr.getvalue())
            self.assertEqual(
                serial_port.writes,
                [benchmark.command_bytes(SEQUENCE, benchmark.DEFAULT_BYTES, 7)],
            )
            self.assertTrue(serial_port.closed)
            self.assertEqual(len(serial_factory.calls), 1)
            status_json = json.loads((artifact / "status.json").read_text())
            metadata = json.loads((artifact / "metadata.json").read_text())
            self.assertEqual(
                status_json["status"],
                "MEASUREMENT_PASS_IMAGE_UNVERIFIED",
            )
            self.assertTrue(status_json["measurement_complete"])
            self.assertFalse(status_json["proof_complete"])
            self.assertEqual(metadata["monitor_exit_code"], monitor.EXIT_OK)
            self.assertEqual(metadata["capture_mode"], "LIVE_SERIAL_READ_ONLY")
            self.assertFalse(metadata["target_image_loaded_by_this_tool"])
            self.assertIn("UNVERIFIED", metadata["target_image_binding"])
            self.assertIn(
                "P2 SD MEASUREMENT PASS - IMAGE UNVERIFIED",
                stdout.getvalue(),
            )

    def test_target_claim_below_threshold_returns_proof_failure(self):
        byte_count = 80 * 1024 * 1024
        exact_usec = byte_count // 40
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            source = directory / "slow.raw"
            source.write_text(
                complete_log(
                    byte_count=byte_count,
                    usecs=[exact_usec] + [1_500_000] * 6,
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                status = runner.main(["--parse-log", str(source)])

            self.assertEqual(status, runner.EXIT_PROOF_FAILED)
            self.assertFalse(json.loads(stdout.getvalue())["complete"])


if __name__ == "__main__":
    unittest.main()
