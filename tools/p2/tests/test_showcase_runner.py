#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

import datetime
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


P2_TOOLS = pathlib.Path(__file__).resolve().parents[1]
ROOT = P2_TOOLS.parents[1]
sys.path.insert(0, str(P2_TOOLS))

import build_artifact  # noqa: E402
import smartpins_protocol  # noqa: E402


SCRIPT = P2_TOOLS / "test-showcase.py"
SPEC = importlib.util.spec_from_file_location("p2_test_showcase", SCRIPT)
showcase = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = showcase
SPEC.loader.exec_module(showcase)


class ManualClock:
    def __init__(self):
        self.value = 0.0
        self.epoch = datetime.datetime(2026, 7, 13, tzinfo=datetime.timezone.utc)

    def monotonic(self):
        return self.value

    def sleep(self, duration):
        self.value += max(0.0, duration)

    def utc_now(self):
        return self.epoch + datetime.timedelta(seconds=self.value)


class FakeSession:
    def __init__(self, clock, events, timeline=None):
        self.clock = clock
        self.events = list(events)
        self.returncode = None
        self.writes = []
        self.terminated = False
        self.killed = False
        self.closed = False
        self.timeline = [] if timeline is None else timeline

    def read(self, timeout):
        if self.events:
            self.clock.sleep(0.001)
            return self.events.pop(0)
        self.clock.sleep(timeout)
        return b""

    def poll(self):
        return self.returncode

    def write(self, data):
        self.writes.append(bytes(data))

    def terminate(self):
        self.timeline.append("session-terminate")
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        del timeout
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake-loadp2", 0.5)
        return self.returncode

    def close(self):
        self.timeline.append("session-close")
        self.closed = True


class SessionFactory:
    def __init__(self, session):
        self.session = session
        self.commands = []

    def __call__(self, command):
        self.commands.append(tuple(command))
        return self.session


class RecordingLock:
    def __init__(self, timeline=None):
        self.entered = 0
        self.exited = 0
        self.timeline = [] if timeline is None else timeline

    def factory(self, path, timeout=0.0, monotonic=None):
        del path, timeout, monotonic
        parent = self

        class Lock:
            def __enter__(self):
                parent.timeline.append("lock-enter")
                parent.entered += 1
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                del exc_type, exc_value, traceback
                parent.timeline.append("lock-exit")
                parent.exited += 1

        return Lock()


def smartpin_output(stage):
    lines = [
        "P2SMART:BEGIN",
        smartpins_protocol.WIRING_MARKER,
        "P2SMART:CAPS=" + ",".join(showcase.EXPECTED_SMARTPIN_CAPS),
    ]
    fixed = smartpins_protocol.STAGE_FIXED_MARKERS[stage]
    lines.append(fixed[0])
    if stage == "GPIO":
        lines.extend(
            "P2SMART:GPIO:SAMPLE={}:TX={}:RX={}".format(index, value, value)
            for index, value in enumerate(smartpins_protocol.GPIO_PATTERN)
        )
    elif stage == "DAC_ADC":
        lines.extend(
            (
                "P2SMART:DAC_ADC:SAMPLE=0:DAC=1000:ADC=1100",
                "P2SMART:DAC_ADC:SAMPLE=1:DAC=2000:ADC=2100",
                "P2SMART:DAC_ADC:SAMPLE=2:DAC=3000:ADC=3100",
            )
        )
    lines.extend(fixed[1:])
    lines.append("P2SMART:PASS")
    return ("\r\n" + "\r\n".join(lines) + "\r\n").encode("ascii")


def boot_output(board="p2-ec32mb"):
    lines = [
        "loadp2 fixture",
        "P2BOOT:ENTRY",
        "P2BOOT:DATA=OK",
        "P2BOOT:BSS=OK",
        "P2BOOT:NX_START",
        "P2I2C:BUS_RECOVERY=PASS:SDA=24:SCL=25:PULSES=0",
        "P2I2C:BUS=PASS:DEV=/dev/i2c0:SDA=24:SCL=25:OPEN_DRAIN=YES",
        "P2I2C:BMP180=PASS:DEV=/dev/press0:ADDR=0x77:ID=0x55",
        "P2FLASHBOOT:SMARTFS=/dev/smart0@/mnt/flash:MOUNTED:"
        "AUTOFORMAT=NO:DESTRUCTIVE_HANDLERS=ABSENT",
        "P2SHOWCASE:READY:BOARD={}:RUN=p2help".format(board),
        "nsh> ",
    ]
    return "\r\n".join(lines).encode("ascii")


def events_through_ctrl_c(include_ctrl_prompt=True, board="p2-ec32mb"):
    module = (
        "Module: P2-EC32MB Rev B; LEDs P38/P39; PSRAM 32 MiB"
        if board == "p2-ec32mb"
        else "Module: P2-EC Rev D; LEDs P56/P57; no onboard PSRAM"
    )
    events = [
        boot_output(board),
        (
            "\r\nP2SHOWCASE:BOARD={}:PROFILE=showcase\r\n"
            "{}\r\n"
            "  /dev/userleds  two active-high buffered LEDs (LED switch ON)\r\n"
            "P2SHOWCASE:PASS\r\n"
        ).format(board, module).encode("ascii"),
        b"\r\nnsh> ",
        (
            "\r\nleds_main: Starting the led_daemon\r\n"
            "led_daemon (pid# 17): Running\r\n"
            "led_daemon: Opening /dev/userleds\r\n"
            "led_daemon: Supported LEDs 0x03\r\n"
            "led_daemon: LED set 0x01\r\n"
        ).encode("ascii"),
        b"\r\nnsh> ",
        b"\r\nnsh> \x1b[KSIGTERM received\r\nled_daemon: Terminated.\r\n",
        b"\r\nnsh> ",
    ]
    if board == "p2-ec":
        events.append(b"\r\nadc0\r\ndac0\r\ngpio0\r\nuserleds\r\nnsh> ")
    events.extend([
        ("\r\nNuttX 12.9.0 p2 {}\r\n".format(board)).encode("ascii"),
        b"\r\nnsh> ",
        b"\r\nP2SHOWCASE:HISTORY=PASS\r\n",
        b"\r\nnsh> ",
        b"\r\nP2SHOWCASE:HISTORY=PASS\r\n",
        b"\r\nnsh> ",
        b"\r\nsleep 30\r\n",
    ])
    if include_ctrl_prompt:
        events.append(b"^C\r\nnsh> ")
    return events


def complete_events(board="p2-ec32mb"):
    events = events_through_ctrl_c(board=board)
    for stage in ("GPIO", "EDGE", "UART", "DAC_ADC"):
        events.extend((smartpin_output(stage), b"\r\nnsh> "))
    events.extend(
        (
            (
                "\r\npwm_main: starting output with frequency: 1000 "
                "channel: -1 duty: 00007fff\r\n"
            ).encode("ascii"),
            b"^C\r\nnsh> ",
            (
                "\r\npwm_main: starting output with frequency: 1000 "
                "channel: -1 duty: 00007fff\r\n"
                "pwm_main: stopping output\r\n"
            ).encode("ascii"),
            b"\r\nnsh> ",
            smartpin_output("SPI"),
            b"\r\nnsh> ",
            (
                "\r\nP2I2C:START:BUS=/dev/i2c0:SDA=24:SCL=25:"
                "ADDR=0x77:FREQ=100000\r\n"
                "P2I2C:ID=0x55:REGISTER=0xD0:TRANSFER=WRITE_RESTART_READ\r\n"
                "P2I2C:READINGS=32:MIN=100000:MAX=100100:FNV1A=1234ABCD\r\n"
                "P2I2C:PASS\r\n"
            ).encode("ascii"),
            b"\r\nnsh> ",
            (
                "\r\nP2STORAGE:BEGIN:COMMAND=probe\r\n"
                "P2STORAGE:PROBE:FLASH:DEV=/dev/smart0:AVAILABLE=1:WRITE=1:"
                "SECTORS=63488:SECTORSIZE=256:PASS\r\n"
                "P2STORAGE:PROBE:SD:DEV=/dev/mmcsd0:AVAILABLE=1:WRITE=1:"
                "SECTORS=1000000:SECTORSIZE=512:PASS\r\n"
                "P2STORAGE:PASS:PROBE\r\n"
            ).encode("ascii"),
            b"\r\nnsh> ",
        )
    )
    return events


class ShowcaseRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = pathlib.Path(self.temp.name)
        self.loadp2 = self.directory / "loadp2"
        self.loadp2.write_bytes(b"fixture pinned loadp2\n")
        self.loadp2.chmod(0o755)
        self.build = self.make_build_artifact(self.directory / "build")
        self.clock = ManualClock()

    def tearDown(self):
        self.temp.cleanup()

    def make_build_artifact(
        self, root, profile="showcase", clean=True, board="p2-ec32mb"
    ):
        root.mkdir()
        config = (
            ROOT
            / "boards/p2/p2x8c4m64p"
            / board
            / "configs/showcase/defconfig"
        ).read_bytes()
        for name in build_artifact.PASS_REQUIRED_FILES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if name == "nuttx":
                path.write_bytes(b"\x7fELF" + b"exact showcase ELF" * 8)
            elif name == "nuttx.bin":
                path.write_bytes(b"exact raw image" * 12)
            elif name == "config":
                path.write_bytes(config)
            elif name == "toolchain.lock":
                digest = hashlib.sha256(self.loadp2.read_bytes()).hexdigest()
                path.write_text(
                    "nuttx_commit={}\n"
                    "nuttx_apps_commit={}\n"
                    "sha256={}  {}\n".format("1" * 40, "2" * 40, digest, self.loadp2),
                    encoding="utf-8",
                )
            elif name in ("nuttx-source-status.txt", "apps-source-status.txt"):
                path.write_text("" if clean else "dirty\n", encoding="utf-8")
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
            "board": board,
            "profile": profile,
            "started_utc": "2026-07-13T12:00:00.000Z",
            "ended_utc": "2026-07-13T12:01:00.000Z",
            "build_command": "tools/p2/build.sh showcase --board {}".format(board),
            "nuttx_branch": "codex/showcase",
            "nuttx_commit": "1" * 40,
            "nuttx_commit_after": "1" * 40,
            "apps_path": "/tmp/apps",
            "apps_branch": "codex/showcase",
            "apps_commit": "2" * 40,
            "apps_commit_after": "2" * 40,
            "nuttx_source_clean": clean,
            "apps_source_clean": clean,
            "source_clean": clean,
            "p2llvm_root": "/tmp/p2llvm",
            "compiler": "fixture clang",
            "jobs": 1,
            "board_clock_hz": 180000000,
            "binary_sha256": files["nuttx.bin"]["sha256"],
            "elf_sha256": files["nuttx"]["sha256"],
            "files": files,
        }
        (root / "status.json").write_text(json.dumps(status), encoding="utf-8")
        return root

    def env(self):
        return {
            "P2_HIL": "1",
            "P2_ALLOW_RESET": "1",
            "P2_ALLOW_LOOPBACK_TESTS": "1",
            "P2_PORT": "/dev/fake-p2",
            "P2_RESET_METHOD": "loadp2",
            "P2_LOCK_FILE": str(self.directory / "board.lock"),
            "LOADP2": str(self.loadp2),
        }

    def invoke(self, name, events, build=None):
        artifact = self.directory / name
        timeline = []
        session = FakeSession(self.clock, events, timeline)
        factory = SessionFactory(session)
        lock = RecordingLock(timeline)
        rc = showcase.main(
            [
                "--execute",
                "--build-artifact",
                str(self.build if build is None else build),
                "--artifact-dir",
                str(artifact),
                "--stage-timeout",
                "2",
                "--boot-timeout",
                "2",
                "--interrupt-timeout",
                "1",
            ],
            env=self.env(),
            process_factory=factory,
            monotonic=self.clock.monotonic,
            utc_now=self.clock.utc_now,
            sleep=self.clock.sleep,
            lock_factory=lock.factory,
            owner_probe=lambda port: (),
            port_validator=lambda port: port == "/dev/fake-p2",
        )
        return rc, artifact, session, factory, lock

    def test_full_exact_image_session_passes_and_seals_evidence(self):
        rc, artifact, session, factory, lock = self.invoke("pass", complete_events())

        self.assertEqual(rc, showcase.EXIT_OK)
        self.assertEqual(lock.entered, 1)
        self.assertEqual(lock.exited, 1)
        self.assertEqual(len(factory.commands), 1)
        command = factory.commands[0]
        self.assertEqual(command[-2], "-t")
        self.assertEqual(
            pathlib.Path(command[-1]).resolve(),
            (artifact / "inputs/build/nuttx").resolve(),
        )
        self.assertNotIn("-FLASH", command)
        self.assertNotIn("-PATCH", command)
        self.assertTrue(session.terminated)
        self.assertTrue(session.closed)
        self.assertLess(
            session.timeline.index("session-terminate"),
            session.timeline.index("lock-exit"),
        )
        self.assertIn(b"unam\t -a\r", session.writes)
        self.assertIn(b"\x1b[A\r", session.writes)
        self.assertEqual(session.writes.count(b"\x03"), 2)
        self.assertIn(b"p2smartpins edge\r", session.writes)
        self.assertIn(b"pwm -f 1000 -d 50 -t 30\r", session.writes)
        self.assertIn(b"pwm -f 1000 -d 50 -t 1\r", session.writes)
        self.assertIn(b"p2storage probe\r", session.writes)
        self.assertFalse(
            any(
                token in write
                for write in session.writes
                for token in (b"format", b"write", b"delete", b"alternate")
            )
        )

        status = json.loads((artifact / "status.json").read_text())
        self.assertEqual(status["status"], "PASS")
        self.assertEqual(status["serial_processes_started"], 1)
        self.assertEqual(
            status["build"]["elf_sha256"], build_artifact.load(self.build).elf_sha256
        )
        self.assertEqual(
            status["build"]["raw_binary_sha256"],
            build_artifact.load(self.build).binary_sha256,
        )
        self.assertEqual(status["storage_actions"], ["probe"])
        self.assertEqual(status["destructive_storage_actions"], [])
        self.assertTrue((artifact / "console.raw").is_file())
        self.assertTrue((artifact / "console.normalized.log").is_file())
        self.assertTrue((artifact / "commands.jsonl").is_file())
        self.assertTrue((artifact / "inputs/build/status.json").is_file())
        stage_status = {stage["name"]: stage["status"] for stage in status["stages"]}
        self.assertEqual(stage_status["Ctrl-C interrupt and prompt return"], "PASS")
        self.assertEqual(stage_status["p2smartpins edge"], "PASS")
        self.assertEqual(stage_status["external PWM Ctrl-C and prompt return"], "PASS")
        self.assertEqual(
            stage_status["/dev/pwm0 RC-safe open/start/stop smoke"], "PASS"
        )
        self.assertTrue(
            any("p2smartpins pwm" in item["reason"] for item in status["omissions"])
        )

    def test_revd_session_requires_runtime_psram_absence(self):
        revd = self.make_build_artifact(self.directory / "revd", board="p2-ec")
        rc, artifact, session, factory, lock = self.invoke(
            "revd-pass", complete_events("p2-ec"), build=revd
        )

        self.assertEqual(rc, showcase.EXIT_OK)
        self.assertEqual(lock.entered, 1)
        self.assertEqual(len(factory.commands), 1)
        self.assertIn(b"ls /dev\r", session.writes)
        status = json.loads((artifact / "status.json").read_text())
        self.assertEqual(status["status"], "PASS")
        self.assertEqual(status["board"], "p2-ec")
        stages = {stage["name"]: stage for stage in status["stages"]}
        self.assertFalse(
            stages["Rev D no-PSRAM runtime contract"]["psram_device_present"]
        )
        self.assertTrue(
            any(
                item["stage"] == "p2psram" and "NOT APPLICABLE" in item["reason"]
                for item in status["omissions"]
            )
        )

    def test_ctrl_c_must_return_a_new_prompt_before_deadline(self):
        rc, artifact, session, factory, lock = self.invoke(
            "ctrl-c-fail", events_through_ctrl_c(include_ctrl_prompt=False)
        )

        self.assertEqual(rc, showcase.EXIT_HIL_FAILURE)
        self.assertEqual(len(factory.commands), 1)
        self.assertTrue(session.terminated)
        status = json.loads((artifact / "status.json").read_text())
        self.assertEqual(status["status"], "FAIL")
        self.assertIn("timed out waiting for prompt after Ctrl-C", status["reason"])
        stage = status["stages"][-1]
        self.assertEqual(stage["name"], "Ctrl-C interrupt and prompt return")
        self.assertEqual(stage["status"], "FAIL")

    def test_external_foreground_app_ctrl_c_also_requires_prompt(self):
        events = events_through_ctrl_c()
        for smart_stage in ("GPIO", "EDGE", "UART", "DAC_ADC"):
            events.extend((smartpin_output(smart_stage), b"\r\nnsh> "))
        events.append(
            b"\r\npwm_main: starting output with frequency: 1000 "
            b"channel: -1 duty: 00007fff\r\n"
        )
        rc, artifact, session, factory, lock = self.invoke(
            "external-ctrl-c-fail", events
        )

        self.assertEqual(rc, showcase.EXIT_HIL_FAILURE)
        self.assertEqual(session.writes.count(b"\x03"), 2)
        status = json.loads((artifact / "status.json").read_text())
        self.assertEqual(status["status"], "FAIL")
        self.assertIn(
            "timed out waiting for prompt after external-app Ctrl-C",
            status["reason"],
        )
        self.assertEqual(
            status["stages"][-1]["name"],
            "external PWM Ctrl-C and prompt return",
        )

    def test_requires_execute_hil_reset_and_loopback_gates(self):
        base = ["--build-artifact", str(self.build), "--port", "/dev/fake-p2"]
        for missing in (
            "execute",
            "P2_HIL",
            "P2_ALLOW_RESET",
            "P2_ALLOW_LOOPBACK_TESTS",
        ):
            with self.subTest(missing=missing):
                argv = list(base)
                env = self.env()
                if missing != "execute":
                    argv.insert(0, "--execute")
                    env.pop(missing)
                rc = showcase.main(
                    argv,
                    env=env,
                    port_validator=lambda port: True,
                )
                self.assertEqual(rc, showcase.EXIT_CONFIGURATION)

    def test_optional_psram_has_independent_volatile_write_gate(self):
        argv = [
            "--execute",
            "--include-psram",
            "--build-artifact",
            str(self.build),
            "--port",
            "/dev/fake-p2",
        ]
        rc = showcase.main(argv, env=self.env(), port_validator=lambda port: True)
        self.assertEqual(rc, showcase.EXIT_CONFIGURATION)

    def test_rejects_nonshowcase_dirty_or_smp_build_inputs_before_hardware(self):
        wrong_profile = self.make_build_artifact(
            self.directory / "wrong-profile", profile="nsh"
        )
        dirty = self.make_build_artifact(self.directory / "dirty", clean=False)
        for name, artifact in (("profile", wrong_profile), ("dirty", dirty)):
            with self.subTest(name=name):
                rc = showcase.main(
                    [
                        "--execute",
                        "--build-artifact",
                        str(artifact),
                        "--port",
                        "/dev/fake-p2",
                    ],
                    env=self.env(),
                    process_factory=lambda command: self.fail(
                        "hardware process started for invalid artifact"
                    ),
                    port_validator=lambda port: True,
                )
                self.assertEqual(rc, showcase.EXIT_CONFIGURATION)

        smp_config = self.directory / "smp.config"
        smp_config.write_bytes((self.build / "config").read_bytes() + b"CONFIG_SMP=y\n")
        with self.assertRaisesRegex(showcase.ShowcaseError, "CONFIG_SMP"):
            showcase.validate_showcase_config(smp_config)

    def test_selected_analog_parser_rejects_nonmonotonic_adc(self):
        good = smartpin_output("DAC_ADC").decode("ascii")
        self.assertTrue(showcase.parse_smartpin_command(good, "DAC_ADC")["complete"])
        bad = good.replace("ADC=3100", "ADC=2000")
        result = showcase.parse_smartpin_command(bad, "DAC_ADC")
        self.assertFalse(result["complete"])
        self.assertIn(
            "DAC_ADC ADC samples are not strictly increasing", result["errors"]
        )

    def test_strict_command_capture_retains_final_marker_newline(self):
        sequence = "A55A0713"
        text = "P2PSRAM:PASS:SEQUENCE={}\r\nnsh> ".format(sequence)
        final_pattern = dict(showcase.psram_protocol.marker_patterns(sequence))[
            "P2PSRAM final pass"
        ]
        match = final_pattern.search(text)
        self.assertIsNotNone(match)

        segment = showcase._capture_segment(text, 0, match.end())
        self.assertTrue(segment.endswith("\n"))
        self.assertIsNotNone(final_pattern.search(segment))


if __name__ == "__main__":
    unittest.main()
