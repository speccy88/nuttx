# SPDX-License-Identifier: Apache-2.0

import contextlib
import datetime
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

P2_TOOLS = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(P2_TOOLS))

import clock_protocol
import hil


SCRIPT_PATH = P2_TOOLS / "test-clock.py"
SPEC = importlib.util.spec_from_file_location("p2_test_clock", SCRIPT_PATH)
clock_script = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = clock_script
SPEC.loader.exec_module(clock_script)

READY = b"P2CLOCK:READY:SYSCLK=180000000:XTAL=20000000:COUNTER_BITS=32\r\n"


class ManualClock:
    def __init__(self):
        self.value = 1000.0
        self.epoch = datetime.datetime(2026, 7, 13, tzinfo=datetime.timezone.utc)

    def monotonic(self):
        return self.value

    def advance(self, duration):
        self.value += max(0.0, duration)

    def utc_now(self):
        return self.epoch + datetime.timedelta(seconds=self.value - 1000.0)


class InteractiveClockSession:
    def __init__(
        self,
        manual_clock,
        response_latency=0.001,
        bad_sequence=False,
        extra_after_done=False,
        reset_after_samples=None,
    ):
        self.clock = manual_clock
        self.response_latency = response_latency
        self.bad_sequence = bad_sequence
        self.extra_after_done = extra_after_done
        self.reset_after_samples = reset_after_samples
        self.queue = [READY]
        self.writes = []
        self.sample_count = 0
        self.pending_sample = False
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.closed = False
        self.maximum_outstanding = 0

    def read(self, timeout):
        if self.queue:
            data = self.queue.pop(0)
            self.clock.advance(self.response_latency)
            if data.startswith(b"P2CLOCK:SAMPLE"):
                self.pending_sample = False
            return data
        self.clock.advance(timeout)
        return b""

    def write(self, data):
        value = bytes(data)
        self.writes.append(value)
        if value == b"S\r":
            if self.pending_sample:
                raise AssertionError("a second S was sent before its response")
            self.pending_sample = True
            self.maximum_outstanding = max(self.maximum_outstanding, 1)
            if self.sample_count == self.reset_after_samples:
                self.queue.append(b"\xff\x00P2BOOT:ENTRY\r\n")
                return
            sequence = self.sample_count + (1 if self.bad_sequence else 0)
            capture = self.clock.value + self.response_latency / 2.0
            counter = int(capture * clock_protocol.EXPECTED_SYSCLK_HZ)
            counter %= clock_protocol.COUNTER_MODULUS
            self.queue.append(
                (
                    "P2CLOCK:SAMPLE:SEQ={:08X}:COUNTER={:08X}\r\n".format(
                        sequence, counter
                    )
                ).encode("ascii")
            )
            self.sample_count += 1
        elif value == b"Q\r":
            if self.pending_sample:
                raise AssertionError("Q was sent while S was outstanding")
            self.queue.append(
                "P2CLOCK:DONE:SAMPLES={:08X}\r\n".format(
                    self.sample_count
                ).encode("ascii")
            )
            if self.extra_after_done:
                self.queue.append(
                    b"P2CLOCK:SAMPLE:SEQ=FFFFFFFF:COUNTER=00000000\r\n"
                )
        else:
            raise AssertionError("unexpected command {!r}".format(value))

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        del timeout
        return self.returncode

    def close(self):
        self.closed = True


class SessionFactory:
    def __init__(self, session):
        self.session = session
        self.commands = []

    def __call__(self, command):
        self.commands.append(tuple(command))
        return self.session


class RecordingLock:
    def __init__(self):
        self.entered = 0
        self.exited = 0

    def factory(self, path, timeout=0.0, monotonic=None):
        del path, timeout, monotonic
        recorder = self

        class Context:
            def __enter__(self):
                recorder.entered += 1
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                del exc_type, exc_value, traceback
                recorder.exited += 1

        return Context()


class ClockRunnerTests(unittest.TestCase):
    def make_config(self, root):
        root = pathlib.Path(root)
        image = root / "nuttx"
        image.write_bytes(b"P2 clock fixture image")
        loadp2 = root / "loadp2"
        loadp2.write_bytes(b"fixture loader")
        loadp2.chmod(0o755)
        toolchain_lock = root / "toolchain.lock"
        toolchain_lock.write_text("fixture\n", encoding="utf-8")
        generated = root / ".config"
        generated.write_text(
            "\n".join(
                "{}={}".format(name, value)
                for name, value in clock_script.CLOCK_REQUIRED_CONFIG
            )
            + "\n",
            encoding="utf-8",
        )
        return clock_script.ClockRunConfig(
            port="/dev/fake-p2",
            image=image,
            loadp2=loadp2,
            toolchain_lock=toolchain_lock,
            generated_config=generated,
            artifact_dir=root / "artifact",
            board_lock=root / "board.lock",
            loader_baud=2_000_000,
            console_baud=230_400,
            reset_flag="-DTR",
            lock_timeout=0.0,
            image_sha256=clock_script.sha256_file(image),
            loadp2_sha256=clock_script.sha256_file(loadp2),
            config_sha256=clock_script.sha256_file(generated),
        )

    def run_fixture(self, root, session, manual_clock, **overrides):
        lock = RecordingLock()
        runner = clock_script.ClockRunner(
            self.make_config(root),
            process_factory=SessionFactory(session),
            monotonic=manual_clock.monotonic,
            utc_now=manual_clock.utc_now,
            lock_factory=lock.factory,
            owner_probe=lambda _port: (),
            short_duration=overrides.pop("short_duration", 1.0),
            final_duration=overrides.pop("final_duration", 3.0),
            sample_interval=overrides.pop("sample_interval", 0.5),
            sample_response_timeout=overrides.pop(
                "sample_response_timeout", 1.0
            ),
            **overrides,
        )
        return runner, lock

    def test_one_outstanding_command_and_both_calibrations_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            clock = ManualClock()
            session = InteractiveClockSession(clock)
            runner, lock = self.run_fixture(temporary, session, clock)
            with contextlib.redirect_stdout(io.StringIO()):
                passed = runner.run()

            self.assertTrue(passed)
            self.assertEqual(lock.entered, 1)
            self.assertEqual(lock.exited, 1)
            self.assertEqual(session.maximum_outstanding, 1)
            self.assertEqual(session.writes[-1], b"Q\r")
            self.assertEqual(session.writes[:-1], [b"S\r"] * session.sample_count)
            self.assertTrue(session.terminated)
            self.assertTrue(session.closed)

            artifact = pathlib.Path(temporary) / "artifact"
            records = [
                json.loads(line)
                for line in (artifact / "samples.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(len(records), session.sample_count)
            self.assertGreaterEqual(len(records), 8)
            self.assertEqual(records[0]["sequence"], 0)
            self.assertIsNone(records[0]["counter_delta_ticks"])
            self.assertEqual(records[1]["sequence"], 1)
            self.assertGreater(records[1]["counter_delta_ticks"], 0)
            self.assertIn("send_utc", records[0])
            self.assertIn("receive_utc", records[0])

            short = json.loads(
                (artifact / "calibration-10s.json").read_text(encoding="utf-8")
            )
            final = json.loads(
                (artifact / "calibration-600s.json").read_text(encoding="utf-8")
            )
            status = json.loads(
                (artifact / "status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(short["status"], "PASS")
            self.assertEqual(final["status"], "PASS")
            self.assertGreaterEqual(short["elapsed_lower_bound_seconds"], 1.0)
            self.assertGreaterEqual(final["elapsed_lower_bound_seconds"], 3.0)
            self.assertEqual(status["status"], "PASS")
            self.assertTrue(status["one_outstanding_sample_command"])
            self.assertEqual(status["done_sample_count"], len(records))
            self.assertTrue(status["intentionally_terminated"])
            self.assertIn("inputs/clock_protocol.py", status["preserved_input_sha256"])

    def test_conservative_gap_over_five_seconds_fails_and_is_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            clock = ManualClock()
            session = InteractiveClockSession(clock, response_latency=3.0)
            runner, _lock = self.run_fixture(
                temporary,
                session,
                clock,
                sample_response_timeout=4.0,
            )
            self.assertFalse(runner.run())
            status = json.loads(
                (pathlib.Path(temporary) / "artifact" / "status.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(status["status"], "FAIL")
            self.assertIn("sample gap", status["failure_reason"])
            self.assertNotIn(b"Q\r", session.writes)

    def test_bad_sequence_is_rejected_before_second_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            clock = ManualClock()
            session = InteractiveClockSession(clock, bad_sequence=True)
            runner, _lock = self.run_fixture(temporary, session, clock)
            self.assertFalse(runner.run())
            self.assertEqual(session.writes, [b"S\r"])
            status = json.loads(
                (pathlib.Path(temporary) / "artifact" / "status.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("expected 00000000", status["failure_reason"])

    def test_extra_target_marker_after_done_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            clock = ManualClock()
            session = InteractiveClockSession(clock, extra_after_done=True)
            runner, _lock = self.run_fixture(temporary, session, clock)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(runner.run())
            status = json.loads(
                (pathlib.Path(temporary) / "artifact" / "status.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(status["status"], "FAIL")
            self.assertIn("followed DONE", status["failure_reason"])

    def test_target_reset_after_ready_is_rejected_immediately(self):
        with tempfile.TemporaryDirectory() as temporary:
            clock = ManualClock()
            session = InteractiveClockSession(clock, reset_after_samples=2)
            runner, _lock = self.run_fixture(temporary, session, clock)
            self.assertFalse(runner.run())
            status = json.loads(
                (pathlib.Path(temporary) / "artifact" / "status.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(status["status"], "FAIL")
            self.assertIn("unexpected target reset", status["failure_reason"])

    def test_clock_profile_validation_is_exact(self):
        good = dict(clock_script.CLOCK_REQUIRED_CONFIG)
        defconfig = hil.read_kconfig(
            clock_script.REPO_ROOT
            / "boards"
            / "p2"
            / "p2x8c4m64p"
            / "p2-ec32mb"
            / "configs"
            / "clock"
            / "defconfig"
        )
        self.assertEqual(good, defconfig)
        clock_script.validate_clock_config(good)
        for name in good:
            with self.subTest(name=name):
                bad = dict(good)
                bad[name] = "wrong"
                with self.assertRaisesRegex(
                    clock_script.SafetyError, "locked clock profile"
                ):
                    clock_script.validate_clock_config(bad)

    def test_load_command_is_ram_only_and_uses_one_reset(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = self.make_config(temporary)
            command = clock_script.build_command(config)
            self.assertIn("-t", command)
            self.assertNotIn("-FLASH", command)
            self.assertNotIn("-PATCH", command)
            self.assertEqual(command.count("-DTR") + command.count("-RTS"), 1)
            self.assertEqual(command[-1], str(config.image))

    def test_dry_run_does_not_build_or_open_a_process(self):
        calls = []
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = clock_script.main(
                [],
                env={},
                build_runner=lambda: calls.append("build"),
                process_factory=lambda _command: calls.append("process"),
            )
        self.assertEqual(result, clock_script.EXIT_SAFETY)
        self.assertEqual(calls, [])
        self.assertIn("no build, serial open, reset, or load", stderr.getvalue())

    def test_hil_build_dispatch_selects_clock_profile(self):
        completed = mock.Mock(returncode=0)
        with mock.patch.object(hil.subprocess, "run", return_value=completed) as run:
            self.assertEqual(hil.default_build_runner("clock"), 0)
        command = run.call_args.args[0]
        self.assertEqual(command[-1], "clock")


if __name__ == "__main__":
    unittest.main()
