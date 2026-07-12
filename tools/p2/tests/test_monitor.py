import contextlib
import datetime
import io
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

import monitor


class FakeSerialError(OSError):
    pass


class ManualClock:
    def __init__(self):
        self.value = 0.0
        self.epoch = datetime.datetime(2026, 7, 12, tzinfo=datetime.timezone.utc)

    def monotonic(self):
        return self.value

    def sleep(self, duration):
        self.value += max(0.0, duration)

    def utc_now(self):
        return self.epoch + datetime.timedelta(seconds=self.value)


class FakeSerial:
    def __init__(self, clock, events=(), writes=None):
        self.clock = clock
        self.events = list(events)
        self.writes = writes if writes is not None else []
        self.closed = False
        self.flushed = 0

    def read(self, size):
        del size
        self.clock.sleep(0.01)
        if not self.events:
            return b""
        event = self.events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def flush(self):
        self.flushed += 1

    def close(self):
        self.closed = True


class SerialFactory:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if not self.outcomes:
            raise FakeSerialError("no fake serial outcome remains")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class MonitorTests(unittest.TestCase):
    def make_config(self, directory, **overrides):
        values = dict(
            port="fake://p2",
            baud=230400,
            raw_log=pathlib.Path(directory) / "console.raw",
            normalized_log=pathlib.Path(directory) / "console.log",
            timeout=0.5,
            reconnect_interval=0.05,
        )
        values.update(overrides)
        return monitor.MonitorConfig(**values)

    def run_monitor(self, config, factory, clock):
        console = io.StringIO()
        diagnostics = io.StringIO()
        instance = monitor.SerialMonitor(
            config,
            factory,
            serial_exceptions=(FakeSerialError,),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            utc_now=clock.utc_now,
            output=console,
            diagnostics=diagnostics,
        )
        return instance.run(), console.getvalue(), diagnostics.getvalue()

    def test_expected_marker_logs_exact_raw_and_normalized_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = ManualClock()
            writes = []
            serial_port = FakeSerial(
                clock,
                [
                    b"P2HEL",
                    b"LO:ENTRY\r\nTARGET:",
                    b"READY\rnext\nlast line\n",
                ],
                writes,
            )
            factory = SerialFactory([serial_port])
            config = self.make_config(
                directory,
                expected_markers=("TARGET:READY",),
                sends=("help",),
                send_ending=b"\r",
            )

            result, console, diagnostics = self.run_monitor(config, factory, clock)

            self.assertEqual(result.exit_code, monitor.EXIT_OK)
            self.assertEqual(result.expected_found, ("TARGET:READY",))
            self.assertEqual(result.resets, 1)
            self.assertEqual(writes, [b"help\r"])
            self.assertTrue(serial_port.closed)
            self.assertEqual(
                config.raw_log.read_bytes(),
                b"P2HELLO:ENTRY\r\nTARGET:READY\rnext\nlast line\n",
            )
            normalized = config.normalized_log.read_text(encoding="utf-8")
            self.assertIn("P2HELLO:ENTRY\n", normalized)
            self.assertIn("TARGET:READY\n", normalized)
            self.assertIn("MONITOR:RESET count=1", normalized)
            self.assertIn("MONITOR:EXPECTED 'TARGET:READY'", normalized)
            self.assertIn("MONITOR:TX index=0 bytes=5 text='help'", diagnostics)
            self.assertIn("[2026-07-12T00:00:00.", console)

    def test_disconnect_reconnect_then_expected_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = ManualClock()
            first = FakeSerial(
                clock, [b"first connection\n", FakeSerialError("cable unplugged")]
            )
            second = FakeSerial(clock, [b"TARGET:", b"READY\n"])
            factory = SerialFactory([first, second])
            config = self.make_config(directory, expected_markers=("TARGET:READY",))

            result, _, diagnostics = self.run_monitor(config, factory, clock)

            self.assertEqual(result.exit_code, monitor.EXIT_OK)
            self.assertEqual(result.connections, 2)
            self.assertEqual(result.disconnects, 1)
            self.assertTrue(first.closed)
            self.assertTrue(second.closed)
            self.assertIn("MONITOR:DISCONNECTED cable unplugged", diagnostics)
            self.assertIn("MONITOR:RECONNECTED attempt=2", diagnostics)

    def test_marker_cannot_span_a_disconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = ManualClock()
            first = FakeSerial(clock, [b"PAN", FakeSerialError("disconnected")])
            second = FakeSerial(clock, [b"IC is split\nTARGET:READY\n"])
            config = self.make_config(directory, expected_markers=("TARGET:READY",))

            result, console, _ = self.run_monitor(
                config, SerialFactory([first, second]), clock
            )

            self.assertEqual(result.exit_code, monitor.EXIT_OK)
            self.assertIn("PAN\n", console)
            self.assertIn("IC is split\n", console)

    def test_panic_takes_precedence_over_success_in_same_read(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = ManualClock()
            serial_port = FakeSerial(clock, [b"TARGET:READY\nPANIC: trap\n"])
            config = self.make_config(directory, expected_markers=("TARGET:READY",))

            result, _, diagnostics = self.run_monitor(
                config, SerialFactory([serial_port]), clock
            )

            self.assertEqual(result.exit_code, monitor.EXIT_PANIC)
            self.assertIn("panic marker: PANIC", result.reason)
            self.assertIn("MONITOR:PANIC 'PANIC'", diagnostics)

    def test_expected_marker_timeout_lists_missing_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = ManualClock()
            serial_port = FakeSerial(clock, [b"still booting\n"])
            config = self.make_config(
                directory,
                timeout=0.08,
                expected_markers=("NEVER-SEEN",),
            )

            result, _, _ = self.run_monitor(config, SerialFactory([serial_port]), clock)

            self.assertEqual(result.exit_code, monitor.EXIT_EXPECT_TIMEOUT)
            self.assertIn("'NEVER-SEEN'", result.reason)

    def test_never_connected_has_distinct_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = ManualClock()
            factory = SerialFactory(
                [
                    FakeSerialError("missing device"),
                    FakeSerialError("missing device"),
                    FakeSerialError("missing device"),
                ]
            )
            config = self.make_config(directory, timeout=0.11)

            result, _, diagnostics = self.run_monitor(config, factory, clock)

            self.assertEqual(result.exit_code, monitor.EXIT_CONNECT_FAILED)
            self.assertEqual(result.connections, 0)
            # Identical connection failures are deliberately de-duplicated.
            self.assertEqual(diagnostics.count("MONITOR:CONNECT_FAILED"), 1)

    def test_second_boot_marker_is_an_unexpected_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = ManualClock()
            serial_port = FakeSerial(clock, [b"P2BOOT:ENTRY\n", b"P2BOOT:ENTRY\n"])
            config = self.make_config(directory, max_resets=1)

            result, _, diagnostics = self.run_monitor(
                config, SerialFactory([serial_port]), clock
            )

            self.assertEqual(result.exit_code, monitor.EXIT_UNEXPECTED_RESET)
            self.assertEqual(result.resets, 2)
            self.assertIn("MONITOR:RESET count=2", diagnostics)

    def test_board_lock_rejects_a_second_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = pathlib.Path(directory) / ".p2-board.lock"
            with monitor.BoardLock(lock_path):
                with self.assertRaises(monitor.LockBusyError):
                    with monitor.BoardLock(lock_path):
                        self.fail("second owner unexpectedly acquired the lock")

    def test_inherited_descriptor_carries_the_same_exclusive_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = pathlib.Path(directory) / ".p2-board.lock"
            lock_path.touch()
            descriptor = os.open(lock_path, os.O_RDWR)
            try:
                with monitor.InheritedBoardLock(descriptor, lock_path):
                    with self.assertRaises(monitor.LockBusyError):
                        with monitor.BoardLock(lock_path):
                            self.fail("inherited lock was not exclusive")
            finally:
                os.close(descriptor)

    def test_dry_run_does_not_create_logs_or_open_serial(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_log = pathlib.Path(directory) / "must-not-exist.raw"
            factory = mock.Mock(side_effect=AssertionError("serial was opened"))
            stderr = io.StringIO()
            with mock.patch.dict(
                os.environ, {"P2_HIL": "1"}
            ), contextlib.redirect_stderr(stderr):
                status = monitor.main(
                    ["--port", "fake://p2", "--raw-log", str(raw_log)],
                    serial_factory=factory,
                )

            self.assertEqual(status, monitor.EXIT_SAFETY)
            self.assertFalse(raw_log.exists())
            factory.assert_not_called()
            self.assertIn("DRY-RUN", stderr.getvalue())

    def test_cli_lock_contention_does_not_open_serial(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            lock_path = directory / ".p2-board.lock"
            raw_log = directory / "console.raw"
            normalized_log = directory / "console.log"
            factory = mock.Mock(side_effect=AssertionError("serial was opened"))
            stderr = io.StringIO()
            arguments = [
                "--execute",
                "--port",
                "fake://p2",
                "--lock-file",
                str(lock_path),
                "--raw-log",
                str(raw_log),
                "--normalized-log",
                str(normalized_log),
            ]

            with monitor.BoardLock(lock_path), mock.patch.dict(
                os.environ, {"P2_HIL": "1"}
            ), contextlib.redirect_stderr(stderr):
                status = monitor.main(arguments, serial_factory=factory)

            self.assertEqual(status, monitor.EXIT_LOCK_BUSY)
            self.assertFalse(raw_log.exists())
            self.assertFalse(normalized_log.exists())
            factory.assert_not_called()
            self.assertIn("LOCK BUSY", stderr.getvalue())

    def test_configuration_requires_timeout_for_expected_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(directory, timeout=0, expected_markers=("READY",))
            with self.assertRaises(monitor.ConfigurationError):
                config.validate()

    def test_cli_uses_the_shared_lock_environment_contract(self):
        with mock.patch.dict(os.environ, {"P2_LOCK_FILE": "/tmp/custom-p2.lock"}):
            args = monitor.build_parser().parse_args([])
            self.assertEqual(args.lock_file, "/tmp/custom-p2.lock")

        with mock.patch.dict(os.environ, {"P2_LOCK_FILE": ""}):
            args = monitor.build_parser().parse_args([])
            self.assertEqual(args.lock_file, "/tmp/nuttx-p2-hil.lock")


if __name__ == "__main__":
    unittest.main()
