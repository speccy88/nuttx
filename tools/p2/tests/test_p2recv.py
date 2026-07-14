#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import binascii
import contextlib
import io
import os
import pathlib
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

import p2recv
import p2recv_protocol as protocol


class FakeTargetSerial:
    def __init__(
        self,
        manifest,
        destination,
        force=False,
        fail_on_first_chunk=False,
        max_write=None,
    ):
        self.manifest = manifest
        self.destination = destination
        self.force = force
        self.fail_on_first_chunk = fail_on_first_chunk
        self.max_write = max_write
        self.tx = bytearray()
        self.rx = bytearray(b"old console bytes\r\nnsh> ")
        self.received = bytearray()
        self.sequence = 0
        self.command = None
        self.closed = False
        self.flushed = 0
        self.reset_count = 0

    def reset_input_buffer(self):
        self.rx.clear()
        self.reset_count += 1

    def write(self, data):
        data = bytes(data)
        accepted = (
            len(data)
            if self.max_write is None
            else min(len(data), self.max_write)
        )
        self.tx.extend(data[:accepted])
        self._consume_tx()
        return accepted

    def flush(self):
        self.flushed += 1

    def read(self, size):
        if not self.rx:
            return b""
        count = min(size, 7, len(self.rx))
        result = bytes(self.rx[:count])
        del self.rx[:count]
        return result

    def close(self):
        self.closed = True

    def _queue(self, text):
        self.rx.extend(text.encode("ascii") + b"\r\n")

    def _consume_tx(self):
        if self.command is None:
            end = self.tx.find(b"\r")
            if end < 0:
                return
            self.command = bytes(self.tx[:end])
            del self.tx[: end + 1]
            expected = protocol.make_command(
                self.manifest, self.destination, self.force
            )
            if self.command != expected:
                self._queue("P2RECV:ERROR:STAGE=ARGS:CODE=22")
                return
            self._queue("nsh> " + self.command.decode("ascii"))
            self._queue(protocol.ready_marker(self.manifest, self.destination))
            if self.manifest.size == 0:
                self._queue(protocol.ack_marker(self.manifest, self.destination))

        while len(self.tx) >= protocol.FRAME_HEADER.size:
            magic, sequence, length, checksum = protocol.FRAME_HEADER.unpack(
                self.tx[: protocol.FRAME_HEADER.size]
            )
            frame_size = protocol.FRAME_HEADER.size + length
            if len(self.tx) < frame_size:
                return
            payload = bytes(self.tx[protocol.FRAME_HEADER.size : frame_size])
            del self.tx[:frame_size]

            if self.fail_on_first_chunk and sequence == 0:
                self._queue("P2RECV:ERROR:STAGE=WRITE:CODE=28")
                return
            if (
                magic != protocol.FRAME_MAGIC
                or sequence != self.sequence
                or not 1 <= length <= protocol.CHUNK_MAX
                or (binascii.crc32(payload) & 0xFFFFFFFF) != checksum
            ):
                self._queue("P2RECV:ERROR:STAGE=HEADER:CODE=71")
                return

            self.received.extend(payload)
            self._queue(protocol.chunk_marker(sequence, len(self.received)))
            self.sequence += 1
            if len(self.received) == self.manifest.size:
                self._queue(protocol.ack_marker(self.manifest, self.destination))


class P2RecvProtocolTests(unittest.TestCase):
    def manifest(self, directory, data=b"Berry and LVGL over a bounded stream"):
        source = pathlib.Path(directory) / "module.elf"
        source.write_bytes(data)
        return protocol.inspect_file(source)

    def test_crc_and_little_endian_frame_match_target_contract(self):
        payload = b"123456789"
        frame = protocol.encode_frame(0x01020304, payload)
        magic, sequence, length, checksum = struct.unpack("<4sIII", frame[:16])
        self.assertEqual(magic, b"P2RF")
        self.assertEqual(sequence, 0x01020304)
        self.assertEqual(length, len(payload))
        self.assertEqual(checksum, 0xCBF43926)
        self.assertEqual(frame[16:], payload)

    def test_destination_is_confined_to_mounted_filesystems(self):
        self.assertEqual(
            protocol.validate_destination("/mnt/sd/berry/demo.be"),
            "/mnt/sd/berry/demo.be",
        )
        for value in (
            "/dev/mmcsd0",
            "mnt/sd/demo.be",
            "/mnt/sd/../flash/demo.be",
            "/mnt/sd/a b.be",
            "/mnt//demo.be",
            "/mnt/sd/",
        ):
            with self.subTest(value=value):
                with self.assertRaises(protocol.ProtocolValueError):
                    protocol.validate_destination(value)

    def test_sender_handles_fragmented_status_and_short_serial_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.manifest(directory, bytes(range(256)) + b"tail")
            destination = "/mnt/sd/berry/module.elf"
            serial = FakeTargetSerial(
                manifest, destination, force=True, max_write=11
            )
            output = io.StringIO()
            config = p2recv.TransferConfig(
                port="fake://p2", chunk_size=128, timeout=1.0
            )

            result = p2recv.FileSender(serial, config, output=output).send(
                manifest, destination, force=True
            )

            self.assertEqual(bytes(serial.received), manifest.path.read_bytes())
            self.assertEqual(result.bytes_sent, manifest.size)
            self.assertEqual(result.chunks_sent, 3)
            self.assertEqual(serial.reset_count, 1)
            self.assertIn("P2RECV:HOST:READY", output.getvalue())
            self.assertIn("P2RECV:HOST:ACK", output.getvalue())

    def test_real_serial_open_deasserts_control_lines_before_open(self):
        calls = []

        class DeferredSerial:
            def __init__(self, **kwargs):
                calls.append(("construct", kwargs.copy()))
                self.dtr = True
                self.rts = True
                self.port = None
                self.closed = False

            def open(self):
                calls.append(("open", self.port, self.dtr, self.rts))

            def close(self):
                self.closed = True

        config = p2recv.TransferConfig(port="/dev/cu.test")
        connection = p2recv._open_serial(
            DeferredSerial, config, preconfigure_control_lines=True
        )

        self.assertEqual(calls[0][1]["port"], None)
        self.assertEqual(calls[1], ("open", "/dev/cu.test", False, False))
        self.assertFalse(connection.closed)

    def test_console_sync_waits_for_prompt_before_sender_command(self):
        class BootingSerial:
            def __init__(self):
                self.rx = [b"P2BOOT:ENTRY\r\n", b"", b"nsh> "]
                self.tx = bytearray()
                self.flushed = 0

            def read(self, size):
                return self.rx.pop(0) if self.rx else b""

            def write(self, data):
                self.tx.extend(data)
                return len(data)

            def flush(self):
                self.flushed += 1

        serial = BootingSerial()
        output = io.StringIO()
        now = [0.0]

        def monotonic():
            now[0] += 0.1
            return now[0]

        p2recv.wait_for_console_prompt(
            serial,
            1.0,
            output=output,
            monotonic=monotonic,
        )

        self.assertEqual(serial.tx, b"\r")
        self.assertEqual(serial.flushed, 1)
        self.assertIn("P2RECV:HOST:CONSOLE=READY", output.getvalue())

    def test_console_sync_can_be_explicitly_disabled(self):
        class ForbiddenSerial:
            def read(self, size):
                raise AssertionError("disabled console sync attempted a read")

        p2recv.wait_for_console_prompt(ForbiddenSerial(), 0)

    def test_target_error_aborts_without_false_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.manifest(directory)
            destination = "/mnt/flash/berry/module.elf"
            serial = FakeTargetSerial(
                manifest, destination, fail_on_first_chunk=True
            )
            config = p2recv.TransferConfig(port="fake://p2", timeout=1.0)

            with self.assertRaisesRegex(p2recv.TargetError, "STAGE=WRITE"):
                p2recv.FileSender(serial, config).send(manifest, destination)

    def test_zero_length_file_commits_without_an_invalid_empty_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.manifest(directory, b"")
            destination = "/mnt/sd/empty.be"
            serial = FakeTargetSerial(manifest, destination)
            config = p2recv.TransferConfig(port="fake://p2", timeout=1.0)

            result = p2recv.FileSender(serial, config).send(manifest, destination)

            self.assertEqual(result.bytes_sent, 0)
            self.assertEqual(result.chunks_sent, 0)
            self.assertEqual(serial.received, b"")

    def test_cli_dry_run_never_constructs_a_serial_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.manifest(directory)
            output = io.StringIO()
            diagnostics = io.StringIO()

            def forbidden_factory(**kwargs):
                self.fail("dry run attempted serial open: {!r}".format(kwargs))

            result = p2recv.main(
                [str(manifest.path), "/mnt/sd/demo.be"],
                serial_factory=forbidden_factory,
                output=output,
                diagnostics=diagnostics,
            )

            self.assertEqual(result, p2recv.EXIT_OK)
            self.assertIn("P2RECV:PLAN", output.getvalue())
            self.assertIn("DRY-RUN", output.getvalue())
            self.assertEqual(diagnostics.getvalue(), "")

    def test_cli_execute_requires_hil_gate_before_serial_open(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.manifest(directory)
            output = io.StringIO()
            diagnostics = io.StringIO()

            def forbidden_factory(**kwargs):
                self.fail("HIL refusal attempted serial open: {!r}".format(kwargs))

            with mock.patch.dict(os.environ, {"P2_HIL": "0"}):
                result = p2recv.main(
                    [
                        str(manifest.path),
                        "/mnt/sd/demo.be",
                        "--execute",
                        "--port",
                        "fake://p2",
                    ],
                    serial_factory=forbidden_factory,
                    output=output,
                    diagnostics=diagnostics,
                )

            self.assertEqual(result, p2recv.EXIT_SAFETY)
            self.assertIn("P2_HIL=1", diagnostics.getvalue())

    def test_cli_execute_uses_board_lock_and_reports_verified_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.manifest(directory, b"a" * 600)
            destination = "/mnt/flash/berry/module.elf"
            serial = FakeTargetSerial(manifest, destination)
            calls = []

            def factory(**kwargs):
                calls.append(kwargs)
                return serial

            output = io.StringIO()
            diagnostics = io.StringIO()
            lock = pathlib.Path(directory) / "board.lock"
            with mock.patch.dict(os.environ, {"P2_HIL": "1"}):
                result = p2recv.main(
                    [
                        str(manifest.path),
                        destination,
                        "--execute",
                        "--port",
                        "fake://p2",
                        "--lock-file",
                        str(lock),
                    ],
                    serial_factory=factory,
                    serial_exceptions=(OSError,),
                    output=output,
                    diagnostics=diagnostics,
                )

            self.assertEqual(result, p2recv.EXIT_OK, diagnostics.getvalue())
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0]["exclusive"])
            self.assertTrue(serial.closed)
            self.assertEqual(bytes(serial.received), b"a" * 600)
            self.assertIn("P2RECV:HOST:CONSOLE=READY", output.getvalue())
            self.assertIn("P2RECV:PASS:BYTES=600:CHUNKS=3", output.getvalue())


class P2RecvTargetSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = pathlib.Path(__file__).resolve().parents[3]
        cls.directory = root.parent / "apps/system/p2recv"
        cls.source = (cls.directory / "p2recv_main.c").read_text(encoding="utf-8")

    def test_target_build_metadata_is_complete(self):
        for name in ("CMakeLists.txt", "Kconfig", "Make.defs", "Makefile"):
            self.assertTrue((self.directory / name).is_file(), name)

    def test_target_fails_closed_and_commits_only_after_verification(self):
        for required in (
            "O_WRONLY | O_CREAT | O_EXCL",
            "#define P2RECV_FILE_MODE          0755",
            "P2RECV_FILE_MODE);",
            "P2RECV:READY:",
            "P2RECV:CHUNK:",
            "P2RECV:ERROR:",
            "P2RECV:ACK:",
            "p2recv_crc32(payload, frame_length) != frame_crc",
            "(file_crc_state ^ UINT32_MAX) != expected_crc",
            "fsync(fd)",
            "rename(temporary, destination)",
            "unlink(temporary)",
        ):
            self.assertIn(required, self.source)

        self.assertLess(
            self.source.index("(file_crc_state ^ UINT32_MAX) != expected_crc"),
            self.source.index("rename(temporary, destination)"),
        )
        self.assertLess(
            self.source.index("rename(temporary, destination)"),
            self.source.index("P2RECV:ACK:BYTES="),
        )

    def test_target_creates_executable_smartfs_files(self):
        self.assertIn(
            "open(temporary, O_WRONLY | O_CREAT | O_EXCL,\n"
            "                P2RECV_FILE_MODE)",
            self.source,
        )
        self.assertNotIn(
            "open(temporary, O_WRONLY | O_CREAT | O_EXCL, 0644)",
            self.source,
        )

    def test_target_makes_console_binary_transparent_and_restores_before_commit(self):
        enter = "error = p2recv_enter_raw(&saved_termios);"
        restore = "error = p2recv_leave_raw(&saved_termios);"
        commit = "rename(temporary, destination)"

        for required in (
            "#include <termios.h>",
            "tcgetattr(STDIN_FILENO, saved)",
            "cfmakeraw(&raw)",
            "tcsetattr(STDIN_FILENO, TCSANOW, &raw)",
            "tcsetattr(STDIN_FILENO, TCSANOW, saved)",
            enter,
            restore,
            "if (raw_mode)",
            "int restore_error = p2recv_leave_raw(&saved_termios);",
        ):
            self.assertIn(required, self.source)

        self.assertLess(self.source.index(enter), self.source.index("P2RECV:READY:"))
        self.assertLess(self.source.index("P2RECV:READY:"), self.source.index(restore))
        self.assertLess(self.source.index(restore), self.source.index(commit))

    def test_target_temp_name_fits_default_smartfs_limit(self):
        self.assertIn('".p2r-%lx-%x"', self.source)
        self.assertIn('".p2r-ffffffff-f" (15 characters)', self.source)
        self.assertNotIn('".p2r-%08lx-%x.tmp"', self.source)


if __name__ == "__main__":
    unittest.main()
