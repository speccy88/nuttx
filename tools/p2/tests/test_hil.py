import datetime
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

import hil


class ManualClock:
    def __init__(self):
        self.value = 0.0
        self.epoch = datetime.datetime(2026, 7, 12, tzinfo=datetime.timezone.utc)

    def monotonic(self):
        return self.value

    def advance(self, duration):
        self.value += max(0.0, duration)

    def utc_now(self):
        return self.epoch + datetime.timedelta(seconds=self.value)


class FakeSession:
    def __init__(self, clock, events, returncode=None):
        self.clock = clock
        self.events = list(events)
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self.closed = False

    def read(self, timeout):
        if self.events:
            event = self.events.pop(0)
            if isinstance(event, bytes):
                self.clock.advance(0.001)
                return event
            if event == "eof":
                if self.returncode is None:
                    self.returncode = 0
                return None
            if isinstance(event, BaseException):
                raise event
        self.clock.advance(timeout)
        return b""

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
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake-loadp2", 0.2)
        return self.returncode

    def close(self):
        self.closed = True


class SessionFactory:
    def __init__(self, sessions):
        self.sessions = list(sessions)
        self.commands = []

    def __call__(self, command):
        self.commands.append(tuple(command))
        if not self.sessions:
            raise AssertionError("no fake loadp2 session remains")
        return self.sessions.pop(0)


class RecordingLock:
    def __init__(self):
        self.constructed = 0
        self.entered = 0
        self.exited = 0

    def factory(self, path, timeout=0.0, monotonic=None):
        del path, timeout, monotonic
        self.constructed += 1
        recorder = self

        class Context:
            def __enter__(self):
                recorder.entered += 1
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                del exc_type, exc_value, traceback
                recorder.exited += 1

        return Context()


GOOD_OUTPUT = (
    b"loader output\nP2HELLO:ENTRY\r\n"
    b"P2HELLO:DATA=OK\r\nP2HELLO:BSS=OK\r\n"
    b"P2HELLO:PTRA=0x00000100\r\n"
    b"P2HELLO:COUNTER=0x1234ABCD\r\n"
    b"P2HELLO:READY\r\nP2HELLO:ECHO=?\r\n"
)

GOOD_CONTEXT_OUTPUT = (
    b"loader output\nP2CTX:START\r\n"
    b"P2CTX:SWITCHES=1000000\r\nP2CTX:REGS=OK\r\n"
    b"P2CTX:STACKS=OK\r\nP2CTX:PASS\r\n"
)


class HilTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = pathlib.Path(self.temp.name)
        self.loadp2 = self.directory / "loadp2"
        self.loadp2.write_bytes(b"fake pinned loader\n")
        self.loadp2.chmod(0o755)
        self.image = self.directory / "hello.elf"
        self.image.write_bytes(b"\x7fELF" + b"image" * 20)
        self.lock = self.directory / "toolchain.lock"
        load_sha = hashlib.sha256(self.loadp2.read_bytes()).hexdigest()
        self.lock.write_text(
            "sha256={}  {}\n".format(load_sha, self.loadp2), encoding="utf-8"
        )
        self.clock = ManualClock()

    def tearDown(self):
        self.temp.cleanup()

    def env(self):
        return {
            "P2_HIL": "1",
            "P2_PORT": "/dev/fake-p2",
            "P2_RESET_METHOD": "loadp2",
            "P2_LOADER_BAUD": "2000000",
            "P2_CONSOLE_BAUD": "230400",
            "P2_LOCK_FILE": str(self.directory / "board.lock"),
            "P2_TOOLCHAIN_LOCK": str(self.lock),
            "LOADP2": str(self.loadp2),
        }

    def argv(self, name="run"):
        return [
            "--execute",
            "--image",
            str(self.image),
            "--artifact-dir",
            str(self.directory / name),
            "--timeout",
            "0.3",
        ]

    def invoke(self, argv, environment, factory, lock):
        return hil.main(
            argv,
            env=environment,
            process_factory=factory,
            monotonic=self.clock.monotonic,
            utc_now=self.clock.utc_now,
            lock_factory=lock.factory,
            owner_probe=lambda port: (),
            port_validator=lambda port: port == "/dev/fake-p2",
        )

    def test_execute_and_hil_environment_are_both_required_before_lock_or_process(self):
        factory = SessionFactory([])
        lock = RecordingLock()

        rc_no_execute = self.invoke([], self.env(), factory, lock)
        disabled = self.env()
        disabled["P2_HIL"] = "0"
        rc_no_hil = self.invoke(["--execute"], disabled, factory, lock)

        self.assertEqual(rc_no_execute, hil.EXIT_SAFETY)
        self.assertEqual(rc_no_hil, hil.EXIT_SAFETY)
        self.assertEqual(factory.commands, [])
        self.assertEqual(lock.entered, 0)

    def test_exact_ram_only_command_and_single_lock_span_repeated_cycles(self):
        sessions = [
            FakeSession(self.clock, [GOOD_OUTPUT]),
            FakeSession(self.clock, [GOOD_OUTPUT]),
        ]
        factory = SessionFactory(sessions)
        lock = RecordingLock()
        argv = self.argv("two-cycles") + ["--cycles", "2"]

        rc = self.invoke(argv, self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_OK)
        self.assertEqual((lock.constructed, lock.entered, lock.exited), (1, 1, 1))
        self.assertEqual(len(factory.commands), 2)
        expected = (
            str(self.loadp2.resolve()),
            "-p",
            "/dev/fake-p2",
            "-l",
            "2000000",
            "-b",
            "230400",
            "-ZERO",
            "-v",
            "-DTR",
            "-e",
            "pausems(500)send(?)",
            "-t",
            str(self.image.resolve()),
        )
        self.assertEqual(factory.commands, [expected, expected])
        self.assertNotIn("-PATCH", expected)
        self.assertNotIn("-FLASH", expected)
        self.assertTrue(all(session.terminated for session in sessions))
        overall = json.loads(
            (self.directory / "two-cycles" / "status.json").read_text()
        )
        self.assertEqual(overall["status"], "PASS")
        self.assertEqual(overall["cycles_passed"], 2)

    def test_context_protocol_requires_exact_markers_without_uart_script(self):
        session = FakeSession(self.clock, [GOOD_CONTEXT_OUTPUT])
        factory = SessionFactory([session])
        lock = RecordingLock()
        argv = self.argv("context") + ["--protocol", "context"]

        rc = self.invoke(argv, self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_OK)
        command = factory.commands[0]
        self.assertNotIn("-e", command)
        self.assertNotIn("send(?)", command)
        self.assertEqual(command[-2:], ("-t", str(self.image.resolve())))
        markers = json.loads(
            (self.directory / "context" / "cycle-001" / "markers.json").read_text()
        )
        self.assertTrue(markers["complete"])
        self.assertEqual(markers["reset_count"], 1)

    def test_missing_marker_fails_and_records_exact_missing_marker(self):
        output = GOOD_OUTPUT.replace(b"P2HELLO:ECHO=?\r\n", b"")
        session = FakeSession(self.clock, [output])
        factory = SessionFactory([session])
        lock = RecordingLock()

        rc = self.invoke(self.argv("missing"), self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        markers = json.loads(
            (self.directory / "missing" / "cycle-001" / "markers.json").read_text()
        )
        self.assertIn("P2HELLO:ECHO=?", markers["missing"])
        status = json.loads(
            (self.directory / "missing" / "cycle-001" / "status.json").read_text()
        )
        self.assertIn("bounded timeout", status["reason"])

    def test_panic_wins_even_when_success_markers_share_the_chunk(self):
        session = FakeSession(self.clock, [GOOD_OUTPUT + b"PANIC: trap\n"])
        factory = SessionFactory([session])
        lock = RecordingLock()

        rc = self.invoke(self.argv("panic"), self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        status = json.loads(
            (self.directory / "panic" / "cycle-001" / "status.json").read_text()
        )
        self.assertIn("panic/assert marker", status["reason"])

    def test_bounded_timeout_terminates_and_closes_loader(self):
        session = FakeSession(self.clock, [])
        factory = SessionFactory([session])
        lock = RecordingLock()

        rc = self.invoke(self.argv("timeout"), self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        self.assertTrue(session.terminated)
        self.assertTrue(session.closed)
        self.assertGreaterEqual(self.clock.value, 0.3)
        status = json.loads(
            (self.directory / "timeout" / "cycle-001" / "status.json").read_text()
        )
        self.assertIn("bounded timeout", status["reason"])

    def test_nonzero_loader_exit_is_a_failure(self):
        session = FakeSession(
            self.clock, [b"Could not open serial port\n", "eof"], returncode=7
        )
        factory = SessionFactory([session])
        lock = RecordingLock()

        rc = self.invoke(self.argv("loader-exit"), self.env(), factory, lock)

        self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
        status = json.loads(
            (self.directory / "loader-exit" / "cycle-001" / "status.json").read_text()
        )
        self.assertEqual(status["loader_returncode"], 7)
        self.assertIn("loadp2 exited with code 7", status["reason"])


if __name__ == "__main__":
    unittest.main()
