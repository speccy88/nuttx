#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import binascii
import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
import struct
import sys
import tempfile
import types
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).parents[1] / "test-python.py"
SPEC = importlib.util.spec_from_file_location("p2_python_hil", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
hil = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = hil
SPEC.loader.exec_module(hil)


class FakeSerial:
    def __init__(self, incoming, read_size=3):
        self.incoming = bytearray(incoming)
        self.outgoing = bytearray()
        self.read_size = read_size
        self.flushes = 0

    @property
    def in_waiting(self):
        return len(self.incoming)

    def read(self, size):
        count = min(size, self.read_size, len(self.incoming))
        result = bytes(self.incoming[:count])
        del self.incoming[:count]
        return result

    def write(self, data):
        count = min(7, len(data))
        self.outgoing.extend(data[:count])
        return count

    def flush(self):
        self.flushes += 1

    def close(self):
        pass


class PythonHilProtocolTests(unittest.TestCase):
    def test_upload_preamble_is_fixed_little_endian_abi(self):
        raw = hil.upload_preamble(4096, 0x12345678)
        self.assertEqual(len(raw), 24)
        self.assertEqual(
            hil.UPLOAD_HEADER.unpack(raw),
            (hil.UPLOAD_MAGIC, 1, 24, 4096, 0x12345678, 0),
        )

    def test_upload_preamble_rejects_outside_backing_window(self):
        for size in (0, 191, hil.CONTAINER_CAPACITY + 1):
            with self.subTest(size=size):
                with self.assertRaises(hil.PythonHilError):
                    hil.upload_preamble(size, 0)

    def test_upload_frames_are_sequenced_and_individually_checked(self):
        payload = bytes((index * 29 + 7) & 0xFF for index in range(1300))
        frames = list(hil.upload_frames(io.BytesIO(payload), len(payload)))
        self.assertEqual(
            [committed for committed, _ in frames],
            [*range(128, 1281, 128), 1300],
        )
        rebuilt = bytearray()
        expected_offset = 0
        for committed, raw in frames:
            offset, size, checksum = hil.UPLOAD_FRAME.unpack(
                raw[: hil.UPLOAD_FRAME.size]
            )
            data = raw[hil.UPLOAD_FRAME.size :]
            self.assertEqual(offset, expected_offset)
            self.assertEqual(size, len(data))
            self.assertEqual(checksum, binascii.crc32(data) & 0xFFFFFFFF)
            self.assertEqual(committed, offset + size)
            rebuilt.extend(data)
            expected_offset = committed
        self.assertEqual(bytes(rebuilt), payload)

    def test_upload_frames_reject_source_size_changes(self):
        with self.assertRaises(hil.PythonHilError):
            list(hil.upload_frames(io.BytesIO(b"short"), 100))
        with self.assertRaises(hil.PythonHilError):
            list(hil.upload_frames(io.BytesIO(b"too-long"), 3))

    def test_ready_contract_requires_fixed_base_capacity_and_frame(self):
        good = b"P2PY:UPLOAD:READY:PROTO=1:BASE=10200000:" b"MAX=10485760:FRAME=128"
        self.assertEqual(hil.parse_ready(good), (1, 0x10200000, 10 * 1024 * 1024, 128))
        for bad in (
            good.replace(b"PROTO=1", b"PROTO=2"),
            good.replace(b"10200000", b"10100000"),
            good.replace(b"10485760", b"10485759"),
            good.replace(b"FRAME=128", b"FRAME=1024"),
            b"prefix" + good,
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(hil.PythonHilError):
                    hil.parse_ready(bad)

    def test_python_commands_are_bounded_and_echo_safe(self):
        hil.validate_test_commands()
        self.assertEqual(
            [test.name for test in hil.PYTHON_TESTS],
            [
                "arithmetic",
                "stdlib",
                "allocation_gc",
                "filesystem",
                "exceptions",
                "final",
            ],
        )
        for test in hil.PYTHON_TESTS:
            self.assertLessEqual(len((test.command + "\r").encode("ascii")), 256)
            self.assertNotIn(test.marker, test.command)

    def test_serial_session_handles_partial_reads_and_writes(self):
        fake = FakeSerial(b"noise\r\nP2PY:READY\r\nremaining")
        session = hil.SerialSession(fake)
        session.write(b"0123456789")
        self.assertEqual(bytes(fake.outgoing), b"0123456789")
        self.assertEqual(fake.flushes, 1)
        line = session.wait_line_prefix((b"P2PY:",), 1.0)
        self.assertEqual(line, b"P2PY:READY")
        self.assertEqual(session.read_exact(9, 1.0), b"remaining")

    def test_loadp2_releases_serial_before_binary_upload(self):
        args = types.SimpleNamespace(
            loadp2=pathlib.Path("/pinned/loadp2"),
            serial="/dev/cu.board",
            loader_baud=2000000,
            baud=230400,
            reset_method="dtr",
            image=pathlib.Path("/artifacts/nuttx.bin"),
        )
        command = hil.loader_command(args)
        self.assertNotIn("-t", command)
        self.assertEqual(command[-1], "/artifacts/nuttx.bin")
        self.assertEqual(command[command.index("-p") + 1], "/dev/cu.board")
        completed = subprocess.CompletedProcess(command, 0, stdout=b"loaded")
        with mock.patch.object(hil.subprocess, "run", return_value=completed) as run:
            self.assertIs(hil.run_loader(command, 1.0), completed)
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertNotIn("input", run.call_args.kwargs)

    def test_every_serial_write_fits_the_256_byte_rx_ring(self):
        largest_frame = max(
            len(frame)
            for _committed, frame in hil.upload_frames(io.BytesIO(bytes(1000)), 1000)
        )
        self.assertEqual(largest_frame, hil.UPLOAD_FRAME.size + 128)
        self.assertLessEqual(largest_frame, hil.MAX_UART_WRITE)
        fake = FakeSerial(b"")
        session = hil.SerialSession(fake)
        with self.assertRaises(hil.PythonHilError):
            session.write(bytes(hil.MAX_UART_WRITE + 1))

    def test_execute_persists_partial_evidence_for_every_failure_class(self):
        failures = (
            hil.PythonHilError("protocol failed"),
            OSError("serial disconnected"),
            RuntimeError("unexpected failure"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for index, failure in enumerate(failures):
                with self.subTest(failure=type(failure).__name__):
                    artifact = root / "artifact-{}".format(index)
                    args = types.SimpleNamespace(
                        artifact_dir=artifact,
                        lock_file=root / "board.lock",
                        loadp2=pathlib.Path(sys.executable),
                        serial="/dev/null",
                        baud=230400,
                        loader_baud=2000000,
                        reset_method="dtr",
                        image=root / "nuttx.bin",
                        container=root / "python.p2py",
                        load_timeout=1.0,
                        boot_timeout=1.0,
                        upload_timeout=1.0,
                        test_timeout=1.0,
                    )
                    connection = FakeSerial(b"")

                    def fail(session, *_args):
                        session.received.extend(b"partial-rx")
                        session.sent.extend(b"partial-tx")
                        raise failure

                    environment = {
                        "P2_HIL": "1",
                        "P2_ALLOW_RESET": "1",
                        "P2_ALLOW_PSRAM_WRITE": "1",
                    }
                    loaded = types.SimpleNamespace(
                        returncode=0, stdout=b"partial-loader"
                    )
                    with mock.patch.dict(hil.os.environ, environment, clear=False):
                        with mock.patch.object(hil, "run_loader", return_value=loaded):
                            with mock.patch.object(
                                hil, "open_serial", return_value=connection
                            ):
                                with mock.patch.object(
                                    hil, "run_python_tests", side_effect=fail
                                ):
                                    with self.assertRaises(type(failure)):
                                        hil.execute(args, {"validated": True})

                    status = json.loads((artifact / "status.json").read_text())
                    self.assertEqual(status["status"], "FAIL")
                    self.assertEqual(status["failure_type"], type(failure).__name__)
                    self.assertIn(str(failure), status["reason"])
                    self.assertTrue(status["ended_utc"])
                    self.assertEqual(
                        (artifact / "loader.log").read_bytes(), b"partial-loader"
                    )
                    self.assertEqual(
                        (artifact / "serial.raw").read_bytes(), b"partial-rx"
                    )
                    self.assertEqual(
                        (artifact / "serial-tx.raw").read_bytes(), b"partial-tx"
                    )

    def test_default_mode_is_dry_run_and_never_opens_serial(self):
        arguments = (
            "--serial",
            "/dev/cu.board",
            "--image",
            "/artifacts/nuttx.bin",
            "--container",
            "/artifacts/python.p2py",
            "--artifact-dir",
            "/artifacts/evidence",
        )
        output = io.StringIO()
        with mock.patch.object(
            hil, "validate_artifacts", return_value={"validated": True}
        ):
            with mock.patch.object(hil, "execute") as execute:
                with mock.patch.object(hil, "open_serial") as open_serial:
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(hil.main(arguments), 0)
        execute.assert_not_called()
        open_serial.assert_not_called()
        self.assertIn("DRY-RUN", output.getvalue())


if __name__ == "__main__":
    unittest.main()
