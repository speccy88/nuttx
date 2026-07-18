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
        self.closes = 0
        self.write_timeout = 10.0
        self.write_timeouts = []

    @property
    def in_waiting(self):
        return len(self.incoming)

    def read(self, size):
        count = min(size, self.read_size, len(self.incoming))
        result = bytes(self.incoming[:count])
        del self.incoming[:count]
        return result

    def write(self, data):
        self.write_timeouts.append(self.write_timeout)
        count = min(7, len(data))
        self.outgoing.extend(data[:count])
        return count

    def flush(self):
        self.flushes += 1

    def close(self):
        self.closes += 1


class PythonHilProtocolTests(unittest.TestCase):
    def test_artifact_preflight_verifies_resident_fingerprint_before_hil(self):
        verified = types.SimpleNamespace(
            file_size=4096,
            manifest_sha256=bytes.fromhex("11" * 32),
            build_fingerprint=bytes.fromhex("22" * 32),
            overlay_load_address=0x64000,
            overlay_slot_size=0x18000,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            image = root / "nuttx.bin"
            resident = root / "nuttx"
            container = root / "nuttx.p2py"
            image.write_bytes(b"resident-raw")
            resident.write_bytes(b"resident-elf")
            container.write_bytes(bytes(4096))

            with mock.patch.object(
                hil.p2_python_container,
                "verify_container",
                return_value=verified,
            ):
                with mock.patch.object(
                    hil.p2_python_package, "verify_resident_elf"
                ) as verify_elf:
                    result = hil.validate_artifacts(image, resident, container)
            verify_elf.assert_called_once_with(resident, verified.build_fingerprint)
            self.assertEqual(result["container_fingerprint"], "22" * 32)

            with mock.patch.object(
                hil.p2_python_container,
                "verify_container",
                return_value=verified,
            ):
                with mock.patch.object(
                    hil.p2_python_package,
                    "verify_resident_elf",
                    side_effect=hil.p2_python_package.PackageError("mismatch"),
                ):
                    with self.assertRaisesRegex(
                        hil.PythonHilError, "does not match"
                    ):
                        hil.validate_artifacts(image, resident, container)

    def test_upload_preamble_is_fixed_little_endian_abi(self):
        raw = hil.upload_preamble(4096, 0x12345678)
        self.assertEqual(len(raw), 24)
        self.assertEqual(
            hil.UPLOAD_HEADER.unpack(raw),
            (hil.UPLOAD_MAGIC, 2, 24, 4096, 0x12345678, 0),
        )

    def test_upload_preamble_rejects_outside_backing_window(self):
        for size in (0, 191, hil.CONTAINER_CAPACITY + 1):
            with self.subTest(size=size):
                with self.assertRaises(hil.PythonHilError):
                    hil.upload_preamble(size, 0)

    def test_upload_frames_are_sequenced_and_individually_checked(self):
        payload = bytes((index * 29 + 7) & 0xFF for index in range(2500))
        frames = list(hil.upload_frames(io.BytesIO(payload), len(payload)))
        self.assertEqual(
            [committed for committed, _ in frames],
            [1024, 2048, 2500],
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
        good = (
            b"P2PY:UPLOAD:READY:PROTO=2:BASE=10300000:"
            b"MAX=13631488:FRAME=1024:BAUD=230400"
        )
        self.assertEqual(
            hil.parse_ready(good),
            (2, 0x10300000, 13 * 1024 * 1024, 1024, 230400),
        )
        for bad in (
            good.replace(b"PROTO=2", b"PROTO=1"),
            good.replace(b"10300000", b"10200000"),
            good.replace(b"13631488", b"13631487"),
            good.replace(b"FRAME=1024", b"FRAME=224"),
            good.replace(b"BAUD=230400", b"BAUD=115200"),
            good.replace(b":BAUD=230400", b""),
            b"prefix" + good,
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(hil.PythonHilError):
                    hil.parse_ready(bad)

    def test_upload_pass_contract_requires_zero_uart_rx_drops(self):
        expected = b"P2PY:UPLOAD:PASS:SIZE=4096:CRC=1234ABCD:RXDROPS=0"
        self.assertEqual(hil.upload_pass_marker(4096, 0x1234ABCD), expected)
        self.assertNotEqual(
            expected.replace(b"RXDROPS=0", b"RXDROPS=1"),
            hil.upload_pass_marker(4096, 0x1234ABCD),
        )

    def test_python_commands_are_bounded_and_echo_safe(self):
        hil.validate_test_commands()
        self.assertEqual(
            [test.name for test in hil.PYTHON_TESTS],
            [
                "arithmetic",
                "float_libm",
                "unicode",
                "stdlib",
                "zlib_sizes",
                "zlib_incompressible",
                "zlib_streaming",
                "zlib_checksums",
                "hardware_entropy",
                "runtime_paths",
                "user_site_contract",
                "ignore_environment",
                "isolated_mode",
                "allocation_gc",
                "filesystem",
                "filesystem_large",
                "exceptions",
                "tracemalloc_tls",
                "restart_state_seed",
                "restart_state_isolation",
                "deep_recursion",
                "threads_unsupported",
                "subinterpreters_unsupported",
                "final",
            ],
        )
        for test in hil.PYTHON_TESTS:
            self.assertLessEqual(len((test.command + "\r").encode("ascii")), 256)
            self.assertLessEqual(
                len((test.command + "\r").encode("ascii")),
                hil.MAX_UART_WRITE,
            )
            self.assertNotIn(test.marker, test.command)

        entropy = next(
            test for test in hil.PYTHON_TESTS
            if test.name == "hardware_entropy"
        )
        self.assertIn("os.urandom(256)", entropy.command)
        self.assertIn("secrets.token_bytes(256)", entropy.command)
        self.assertNotIn(hil.ENTROPY_FINGERPRINT_PREFIX, entropy.command)

        zlib_commands = {
            test.name: test.command
            for test in hil.PYTHON_TESTS
            if test.name.startswith("zlib_")
        }
        self.assertIn("b\"\"", zlib_commands["zlib_sizes"])
        self.assertIn("*12000", zlib_commands["zlib_sizes"])
        self.assertIn("randbytes(40000)", zlib_commands["zlib_incompressible"])
        self.assertIn("compressobj()", zlib_commands["zlib_streaming"])
        self.assertIn("decompressobj()", zlib_commands["zlib_streaming"])
        self.assertIn("adler32", zlib_commands["zlib_checksums"])
        self.assertIn("crc32", zlib_commands["zlib_checksums"])

        for marker in (
            hil.CONCURRENCY_HOLDER_MARKER,
            hil.CONCURRENCY_DONE_MARKER,
            hil.CONCURRENCY_SECOND_MARKER,
            hil.CONCURRENCY_POST_MARKER,
        ):
            self.assertNotIn(marker, hil.CONCURRENCY_HOLDER_COMMAND)
            self.assertNotIn(marker, hil.CONCURRENCY_SECOND_COMMAND)

        self.assertEqual(
            hil.RUNTIME_STAGES,
            (
                b"P2PY:TMPFS:READY:PATH=/tmp:HEAP=1048576",
                b"P2PY:ROMDISK:READY:MODE=BUFFERED:SECTOR=512",
                b"P2PY:ROMFS:MOUNTED",
                b"P2PY:CPYTHON:EARLY:START",
                b"P2PY:CPYTHON:EARLY:PASS",
                b"P2PY:CPYTHON:RUN",
            ),
        )

    def test_nsh_setup_fails_closed_on_missing_or_failed_commands(self):
        for output in (
            b"mkdir /tmp\r\nnsh: mkdir: command not found\r\nnsh> ",
            b"mount -t tmpfs /tmp\r\nnsh: mount: mount failed: 19\r\nnsh> ",
            b"ERROR: setup broke\r\nnsh> ",
        ):
            with self.subTest(output=output):
                with self.assertRaises(hil.PythonHilError):
                    hil.run_nsh_setup(
                        hil.SerialSession(FakeSerial(output)), b"setup", 1.0
                    )

        session = hil.SerialSession(FakeSerial(b"mkdir /tmp\r\nnsh> "))
        output = hil.run_nsh_setup(session, b"mkdir /tmp", 1.0)
        self.assertIn("mkdir /tmp", output)

    def test_concurrency_guard_rejects_second_interpreter_and_holder_finishes(self):
        stack = b"P2PY:WORKER:STACK:FREE=8192:SIZE=24576\r\n"
        incoming = (
            b"nsh> "
            + hil.CONCURRENCY_HOLDER_MARKER.encode("ascii")
            + b"\r\n"
            + hil.CONCURRENCY_BUSY_PREFIX.encode("ascii")
            + b"16\r\nnsh> "
            + hil.CONCURRENCY_DONE_MARKER.encode("ascii")
            + b"\r\n"
            + b"P2PY:WORKER:EXIT:CODE=0\r\n"
            + stack
            + hil.CONCURRENCY_POST_MARKER.encode("ascii")
            + b"\r\n"
            + b"P2PY:WORKER:EXIT:CODE=0\r\n"
            + stack
            + b"nsh> "
        )
        fake = FakeSerial(incoming)
        session = hil.SerialSession(fake)
        result = hil.run_concurrency_test(session, 1.0)

        self.assertEqual(result["holder_marker"], hil.CONCURRENCY_HOLDER_MARKER)
        self.assertEqual(result["busy_marker"], "P2PY:RUNTIME:BUSY:CODE=16")
        self.assertEqual(result["done_marker"], hil.CONCURRENCY_DONE_MARKER)
        self.assertEqual(result["post_marker"], hil.CONCURRENCY_POST_MARKER)
        self.assertEqual(len(result["stack_samples"]), 2)
        sent = bytes(fake.outgoing)
        self.assertIn(hil.CONCURRENCY_HOLDER_COMMAND.encode("ascii"), sent)
        self.assertIn(hil.CONCURRENCY_SECOND_COMMAND.encode("ascii"), sent)
        self.assertIn(hil.CONCURRENCY_POST_COMMAND.encode("ascii"), sent)

    def test_concurrency_guard_rejects_an_early_worker_exit_marker(self):
        incoming = b"nsh> P2PY:WORKER:EXIT:CODE=0\r\n"
        with self.assertRaisesRegex(
            hil.PythonHilError, "background Python holder failed"
        ):
            hil.run_concurrency_test(
                hil.SerialSession(FakeSerial(incoming)), 1.0
            )

    def test_restart_stress_requires_twenty_clean_finalize_cycles(self):
        incoming = b"".join(
            (
                "P2PYTEST:RESTART:{}:PASS\r\n"
                "P2PY:WORKER:EXIT:CODE=0\r\n"
                "P2PY:WORKER:STACK:FREE=8192:SIZE=24576\r\n"
                "nsh> "
            ).format(iteration).encode("ascii")
            for iteration in range(hil.RESTART_STRESS_COUNT)
        )
        fake = FakeSerial(incoming)
        result = hil.run_restart_stress(hil.SerialSession(fake), 1.0)
        self.assertEqual(result["count"], 20)
        self.assertEqual(len(result["durations_seconds"]), 20)
        self.assertEqual(len(result["stack_samples"]), 20)
        self.assertEqual(bytes(fake.outgoing).count(b"python -c "), 20)

    def test_worker_exit_must_be_exactly_zero_before_stack_evidence(self):
        good = FakeSerial(b"P2PY:WORKER:EXIT:CODE=0\r\n")
        self.assertEqual(
            hil.wait_worker_exit(hil.SerialSession(good), 1.0), 0
        )

        for line in (
            b"P2PY:WORKER:EXIT:CODE=1\r\n",
            b"P2PY:WORKER:EXIT:CODE=-1\r\n",
            b"P2PY:WORKER:EXIT:CODE=garbage\r\n",
            b"ERROR: worker failed\r\n",
        ):
            with self.subTest(line=line):
                with self.assertRaises(hil.PythonHilError):
                    hil.wait_worker_exit(
                        hil.SerialSession(FakeSerial(line)), 1.0
                    )

    def test_entropy_fingerprint_is_fixed_width_lowercase_hex(self):
        self.assertEqual(
            hil.parse_entropy_fingerprint(
                b"P2PYTEST:ENTROPY:FINGERPRINT:0123456789abcdef0123456789abcdef"
            ),
            "0123456789abcdef0123456789abcdef",
        )
        for line in (
            b"P2PYTEST:ENTROPY:FINGERPRINT:0123",
            b"P2PYTEST:ENTROPY:FINGERPRINT:0123456789ABCDEF0123456789ABCDEF",
            b"P2PYTEST:ENTROPY:FINGERPRINT:gggggggggggggggggggggggggggggggg",
        ):
            with self.subTest(line=line):
                with self.assertRaises(hil.PythonHilError):
                    hil.parse_entropy_fingerprint(line)

    def test_stack_telemetry_is_required_and_enforces_headroom(self):
        good = FakeSerial(b"P2PY:WORKER:STACK:FREE=4096:SIZE=24576\r\n")
        sample = hil.wait_stack_telemetry(hil.SerialSession(good), 1.0)
        self.assertEqual(sample, {"free": 4096, "size": 24576, "used": 20480})

        for line in (
            b"P2PY:WORKER:STACK:FREE=1024:SIZE=24576\r\n",
            b"P2PY:WORKER:STACK:FREE=25000:SIZE=24576\r\n",
            b"P2PY:WORKER:STACK:garbage\r\n",
        ):
            with self.subTest(line=line):
                with self.assertRaises(hil.PythonHilError):
                    hil.wait_stack_telemetry(
                        hil.SerialSession(FakeSerial(line)), 1.0
                    )

    def test_missing_python_builtin_fails_before_upload_timeout(self):
        incoming = (
            b"nsh> "
            b"nsh: python: command not found\r\nnsh> "
        )
        with tempfile.TemporaryDirectory() as temporary:
            container = pathlib.Path(temporary) / "python.p2py"
            container.write_bytes(bytes(4096))
            with self.assertRaisesRegex(hil.PythonHilError, "builtin is unavailable"):
                hil.run_python_tests(
                    hil.SerialSession(FakeSerial(incoming)),
                    container,
                    1.0,
                    1.0,
                    1.0,
                )

    def test_run_rejects_upload_pass_with_any_uart_rx_drop(self):
        payload = bytes(4096)
        crc32 = binascii.crc32(payload) & 0xFFFFFFFF
        incoming = (
            b"nsh> "
            b"P2PY:UPLOAD:READY:PROTO=2:BASE=10300000:"
            b"MAX=13631488:FRAME=1024:BAUD=230400\r\n"
            + "P2PY:UPLOAD:ACCEPT:SIZE=4096:CRC={:08X}\r\n".format(
                crc32
            ).encode("ascii")
            + "P2PY:UPLOAD:PASS:SIZE=4096:CRC={:08X}:RXDROPS=1\r\n".format(
                crc32
            ).encode("ascii")
        )
        with tempfile.TemporaryDirectory() as temporary:
            container = pathlib.Path(temporary) / "python.p2py"
            container.write_bytes(payload)
            with mock.patch.object(
                hil, "send_upload_frames", return_value={}
            ) as send:
                with self.assertRaisesRegex(
                    hil.PythonHilError, "PASS marker does not match"
                ):
                    hil.run_python_tests(
                        hil.SerialSession(FakeSerial(incoming)),
                        container,
                        1.0,
                        1.0,
                        1.0,
                    )
        self.assertFalse(send.call_args.kwargs["inject_faults"])

    def test_serial_session_handles_partial_reads_and_writes(self):
        fake = FakeSerial(b"noise\r\nP2PY:READY\r\nremaining")
        session = hil.SerialSession(fake)
        session.write(b"0123456789")
        self.assertEqual(bytes(fake.outgoing), b"0123456789")
        self.assertEqual(fake.flushes, 0)
        line = session.wait_line_prefix((b"P2PY:",), 1.0)
        self.assertEqual(line, b"P2PY:READY")
        self.assertEqual(session.read_exact(9, 1.0), b"remaining")

    def test_serial_session_enforces_write_deadline_without_flush(self):
        fake = FakeSerial(b"")
        session = hil.SerialSession(fake)
        with mock.patch.object(
            hil.time, "monotonic", side_effect=(0.0, 0.1, 0.2, 0.3)
        ):
            session.write(b"0123456789", deadline=1.0)
        self.assertEqual(bytes(fake.outgoing), b"0123456789")
        self.assertEqual(fake.flushes, 0)
        self.assertEqual(fake.write_timeout, 10.0)
        self.assertAlmostEqual(fake.write_timeouts[0], 1.0)
        self.assertAlmostEqual(fake.write_timeouts[1], 0.8)

        expired = FakeSerial(b"")
        expired_session = hil.SerialSession(expired)
        with mock.patch.object(
            hil.time, "monotonic", side_effect=(0.0, 1.1)
        ):
            with self.assertRaisesRegex(
                hil.PythonHilError, "exceeded its deadline"
            ):
                expired_session.write(b"x", deadline=1.0)
        self.assertEqual(bytes(expired_session.sent), b"x")
        self.assertEqual(expired.flushes, 0)
        self.assertEqual(expired.write_timeout, 10.0)

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

    def test_logical_frames_are_split_into_bounded_serial_writes(self):
        _committed, frame = next(
            iter(
                hil.upload_frames(
                    io.BytesIO(bytes(hil.UPLOAD_FRAME_SIZE)),
                    hil.UPLOAD_FRAME_SIZE,
                )
            )
        )
        self.assertEqual(
            len(frame), hil.UPLOAD_FRAME.size + hil.UPLOAD_FRAME_SIZE
        )
        self.assertGreater(len(frame), hil.MAX_UART_WRITE)

        class CaptureSession:
            def __init__(self):
                self.writes = []

            def write(self, data, deadline=None):
                self.writes.append(bytes(data))

        capture = CaptureSession()
        with mock.patch.object(hil.time, "sleep") as sleep:
            hil.send_logical_frame(capture, frame)
        self.assertEqual(b"".join(capture.writes), frame)
        self.assertTrue(capture.writes)
        self.assertTrue(
            all(0 < len(write) <= hil.MAX_UART_WRITE for write in capture.writes)
        )
        self.assertEqual(sleep.call_count, len(capture.writes) - 1)
        self.assertTrue(
            all(
                call.args == (hil.UPLOAD_CHUNK_PAUSE_SECONDS,)
                for call in sleep.call_args_list
            )
        )
        self.assertEqual(hil.UPLOAD_WIRE_CHUNK_SIZE, 224)
        self.assertLessEqual(hil.UPLOAD_WIRE_CHUNK_SIZE, hil.MAX_UART_WRITE)
        self.assertEqual(hil.UPLOAD_CHUNK_GAP_SECONDS, 0.010)
        self.assertAlmostEqual(
            hil.UPLOAD_CHUNK_WIRE_SECONDS, 2240 / 230400
        )
        self.assertAlmostEqual(
            hil.UPLOAD_CHUNK_PAUSE_SECONDS,
            hil.UPLOAD_CHUNK_WIRE_SECONDS + 0.010,
        )

        fake = FakeSerial(b"")
        session = hil.SerialSession(fake)
        with self.assertRaises(hil.PythonHilError):
            session.write(bytes(hil.MAX_UART_WRITE + 1))

        self.assertEqual(hil.UPLOAD_WINDOW_FRAMES, 1)

    def test_logical_frame_checks_deadline_before_each_chunk_and_gap(self):
        frame = bytes(hil.UPLOAD_WIRE_CHUNK_SIZE + 1)

        class CaptureSession:
            def __init__(self):
                self.writes = []

            def write(self, data, deadline=None):
                self.writes.append(bytes(data))

        gap_capture = CaptureSession()
        with mock.patch.object(
            hil.time, "monotonic", side_effect=(0.0, 0.006)
        ):
            with mock.patch.object(hil.time, "sleep") as sleep:
                with self.assertRaisesRegex(
                    hil.PythonHilError, "exceeded its deadline"
                ):
                    hil.send_logical_frame(gap_capture, frame, deadline=0.010)
        self.assertEqual(len(gap_capture.writes), 1)
        sleep.assert_not_called()

        chunk_capture = CaptureSession()
        with mock.patch.object(
            hil.time, "monotonic", side_effect=(0.0, 0.0, 0.031)
        ):
            with mock.patch.object(hil.time, "sleep") as sleep:
                with self.assertRaisesRegex(
                    hil.PythonHilError, "exceeded its deadline"
                ):
                    hil.send_logical_frame(chunk_capture, frame, deadline=0.030)
        self.assertEqual(len(chunk_capture.writes), 1)
        sleep.assert_called_once_with(hil.UPLOAD_CHUNK_PAUSE_SECONDS)

    def test_upload_is_strictly_stop_and_wait(self):
        payload = bytes((index * 17) & 0xFF for index in range(5000))

        class StopAndWaitSession:
            def __init__(self):
                self.current = bytearray()
                self.transmissions = []
                self.events = []

            def write(self, chunk, deadline=None):
                if len(chunk) > hil.MAX_UART_WRITE:
                    raise AssertionError("unbounded UART write")
                self.current.extend(chunk)
                self.events.append(("write", len(chunk)))

            def read_exact(self, response_size, _timeout):
                if response_size != hil.UPLOAD_ACK.size:
                    raise AssertionError("wrong response size")
                frame = bytes(self.current)
                self.current.clear()
                offset, size, _crc = hil.UPLOAD_FRAME.unpack(
                    frame[: hil.UPLOAD_FRAME.size]
                )
                if len(frame) != hil.UPLOAD_FRAME.size + size:
                    raise AssertionError("more than one logical frame in flight")
                self.transmissions.append(frame)
                self.events.append(("response", offset + size))
                return hil.UPLOAD_ACK.pack(
                    hil.UPLOAD_ACK_MAGIC, offset + size
                )

        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "payload"
            path.write_bytes(payload)
            session = StopAndWaitSession()
            with mock.patch.object(hil.time, "sleep"), \
                 contextlib.redirect_stdout(io.StringIO()):
                result = hil.send_upload_frames(
                    session, path, len(payload), 10.0, 1.0
                )

        self.assertFalse(session.current)
        self.assertEqual(len(session.transmissions), 5)
        self.assertEqual(result["frame_count"], 5)
        self.assertEqual(result["frame_transmissions"], 5)
        self.assertEqual(result["frame_retries"], 0)
        self.assertFalse(result["fault_injection_enabled"])
        self.assertEqual(result["injected_fault_count"], 0)
        self.assertEqual(result["injected_fault_kinds"], [])
        self.assertEqual(result["window_frames"], 1)
        self.assertEqual(result["window_count"], 5)
        self.assertEqual(result["wire_chunk_bytes"], 224)
        self.assertAlmostEqual(
            result["wire_chunk_seconds"], 2240 / 230400
        )
        self.assertEqual(result["inter_chunk_gap_seconds"], 0.010)
        self.assertAlmostEqual(
            result["inter_chunk_pause_seconds"],
            2240 / 230400 + 0.010,
        )
        self.assertEqual(
            [event for event, _value in session.events].count("response"),
            5,
        )

    def test_explicit_nack_retransmits_the_exact_logical_frame(self):
        payload = bytes((index * 31) & 0xFF for index in range(1500))

        class RetrySession:
            def __init__(self):
                self.current = bytearray()
                self.transmissions = []

            def write(self, chunk, deadline=None):
                self.current.extend(chunk)

            def read_exact(self, _size, _timeout):
                frame = bytes(self.current)
                self.current.clear()
                offset, size, _crc = hil.UPLOAD_FRAME.unpack(
                    frame[: hil.UPLOAD_FRAME.size]
                )
                self.transmissions.append(frame)
                if len(self.transmissions) == 1:
                    return hil.UPLOAD_ACK.pack(hil.UPLOAD_NACK_MAGIC, offset)
                return hil.UPLOAD_ACK.pack(hil.UPLOAD_ACK_MAGIC, offset + size)

        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "payload"
            path.write_bytes(payload)
            session = RetrySession()
            with mock.patch.object(hil.time, "sleep"), \
                 contextlib.redirect_stdout(io.StringIO()):
                result = hil.send_upload_frames(
                    session, path, len(payload), 10.0, 1.0
                )

        self.assertEqual(session.transmissions[0], session.transmissions[1])
        self.assertEqual(result["frame_count"], 2)
        self.assertEqual(result["frame_transmissions"], 3)
        self.assertEqual(result["frame_retries"], 1)

    def test_fault_qualification_nacks_then_exactly_retransmits_all_faults(self):
        payload = bytes(
            (index * 43 + 9) & 0xFF
            for index in range(2 * hil.UPLOAD_FRAME_SIZE + 173)
        )

        class CheckingTargetSession:
            def __init__(self, total_size):
                self.total_size = total_size
                self.committed = 0
                self.current = bytearray()
                self.chunks = []
                self.transmissions = []
                self.responses = []

            def write(self, chunk, deadline=None):
                self.assert_chunk_bound(chunk)
                self.current.extend(chunk)
                self.chunks.append(bytes(chunk))

            @staticmethod
            def assert_chunk_bound(chunk):
                if not 0 < len(chunk) <= hil.MAX_UART_WRITE:
                    raise AssertionError("unbounded UART write")

            def read_exact(self, response_size, _timeout):
                if response_size != hil.UPLOAD_ACK.size:
                    raise AssertionError("wrong response size")
                frame = bytes(self.current)
                self.current.clear()
                expected_size = min(
                    hil.UPLOAD_FRAME_SIZE, self.total_size - self.committed
                )
                if len(frame) != hil.UPLOAD_FRAME.size + expected_size:
                    raise AssertionError("target did not receive exact wire length")
                frame_offset, declared_size, declared_crc = hil.UPLOAD_FRAME.unpack(
                    frame[: hil.UPLOAD_FRAME.size]
                )
                frame_payload = frame[hil.UPLOAD_FRAME.size :]
                valid = (
                    frame_offset == self.committed
                    and declared_size == expected_size
                    and declared_crc
                    == (binascii.crc32(frame_payload) & 0xFFFFFFFF)
                )
                self.transmissions.append(frame)
                if valid:
                    self.committed += expected_size
                    response = hil.UPLOAD_ACK.pack(
                        hil.UPLOAD_ACK_MAGIC, self.committed
                    )
                else:
                    response = hil.UPLOAD_ACK.pack(
                        hil.UPLOAD_NACK_MAGIC, self.committed
                    )
                self.responses.append(hil.UPLOAD_ACK.unpack(response))
                return response

        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "payload"
            path.write_bytes(payload)
            target = CheckingTargetSession(len(payload))
            with mock.patch.object(hil.time, "sleep"), \
                 contextlib.redirect_stdout(io.StringIO()):
                result = hil.send_upload_frames(
                    target,
                    path,
                    len(payload),
                    10.0,
                    1.0,
                    inject_faults=True,
                )

        correct = [
            frame for _committed, frame in hil.upload_frames(
                io.BytesIO(payload), len(payload)
            )
        ]
        self.assertEqual(target.committed, len(payload))
        self.assertEqual(len(target.transmissions), 6)
        self.assertEqual(target.transmissions[1], correct[0])
        self.assertEqual(target.transmissions[3], correct[1])
        self.assertEqual(target.transmissions[5], correct[2])
        self.assertEqual(
            target.responses,
            [
                (hil.UPLOAD_NACK_MAGIC, 0),
                (hil.UPLOAD_ACK_MAGIC, hil.UPLOAD_FRAME_SIZE),
                (hil.UPLOAD_NACK_MAGIC, hil.UPLOAD_FRAME_SIZE),
                (hil.UPLOAD_ACK_MAGIC, 2 * hil.UPLOAD_FRAME_SIZE),
                (hil.UPLOAD_NACK_MAGIC, 2 * hil.UPLOAD_FRAME_SIZE),
                (hil.UPLOAD_ACK_MAGIC, len(payload)),
            ],
        )

        bad_crc = hil.UPLOAD_FRAME.unpack(
            target.transmissions[0][: hil.UPLOAD_FRAME.size]
        )
        good_first = hil.UPLOAD_FRAME.unpack(correct[0][: hil.UPLOAD_FRAME.size])
        self.assertEqual(bad_crc[:2], good_first[:2])
        self.assertEqual(bad_crc[2], good_first[2] ^ 1)
        self.assertEqual(
            target.transmissions[0][hil.UPLOAD_FRAME.size :],
            correct[0][hil.UPLOAD_FRAME.size :],
        )

        bad_offset = hil.UPLOAD_FRAME.unpack(
            target.transmissions[2][: hil.UPLOAD_FRAME.size]
        )
        good_second = hil.UPLOAD_FRAME.unpack(correct[1][: hil.UPLOAD_FRAME.size])
        self.assertEqual(bad_offset[0], good_second[0] + 1)
        self.assertEqual(bad_offset[1:], good_second[1:])
        self.assertEqual(
            target.transmissions[2][hil.UPLOAD_FRAME.size :],
            correct[1][hil.UPLOAD_FRAME.size :],
        )

        bad_size = hil.UPLOAD_FRAME.unpack(
            target.transmissions[4][: hil.UPLOAD_FRAME.size]
        )
        good_final = hil.UPLOAD_FRAME.unpack(correct[2][: hil.UPLOAD_FRAME.size])
        self.assertEqual(bad_size[0], good_final[0])
        self.assertEqual(bad_size[1], good_final[1] + 1)
        self.assertEqual(bad_size[2], good_final[2])
        self.assertEqual(len(target.transmissions[4]), len(correct[2]))
        self.assertEqual(
            target.transmissions[4][hil.UPLOAD_FRAME.size :],
            correct[2][hil.UPLOAD_FRAME.size :],
        )

        self.assertEqual(result["frame_count"], 3)
        self.assertEqual(result["frame_transmissions"], 6)
        self.assertEqual(result["frame_retries"], 3)
        self.assertTrue(result["fault_injection_enabled"])
        self.assertEqual(result["injected_fault_count"], 3)
        self.assertEqual(
            result["injected_fault_kinds"], list(hil.UPLOAD_FAULT_SEQUENCE)
        )
        self.assertEqual(
            [fault["frame_offset"] for fault in result["injected_faults"]],
            [0, hil.UPLOAD_FRAME_SIZE, 2 * hil.UPLOAD_FRAME_SIZE],
        )

    def test_deliberate_nack_consumes_the_shared_attempt_budget(self):
        payload = bytes(2 * hil.UPLOAD_FRAME_SIZE + 1)

        class RejectingTarget:
            def __init__(self):
                self.current = bytearray()
                self.transmissions = []

            def write(self, chunk, deadline=None):
                self.current.extend(chunk)

            def read_exact(self, _size, _timeout):
                self.transmissions.append(bytes(self.current))
                self.current.clear()
                return hil.UPLOAD_ACK.pack(hil.UPLOAD_NACK_MAGIC, 0)

        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "payload"
            path.write_bytes(payload)
            target = RejectingTarget()
            with mock.patch.object(hil.time, "sleep"):
                with self.assertRaisesRegex(
                    hil.PythonHilError, "after 3 retries"
                ):
                    hil.send_upload_frames(
                        target,
                        path,
                        len(payload),
                        10.0,
                        1.0,
                        inject_faults=True,
                    )

        correct_first = next(
            iter(hil.upload_frames(io.BytesIO(payload), len(payload)))
        )[1]
        self.assertEqual(len(target.transmissions), 4)
        self.assertNotEqual(target.transmissions[0], correct_first)
        self.assertEqual(target.transmissions[1:], [correct_first] * 3)

    def test_fault_injection_fails_closed_on_any_nonexact_nack(self):
        payload = bytes(2 * hil.UPLOAD_FRAME_SIZE + 1)
        cases = (
            (
                hil.UPLOAD_ACK.pack(
                    hil.UPLOAD_ACK_MAGIC, hil.UPLOAD_FRAME_SIZE
                ),
                "ACKed deliberately invalid",
            ),
            (
                hil.UPLOAD_ACK.pack(hil.UPLOAD_NACK_MAGIC, 1),
                "invalid upload NACK",
            ),
            (hil.UPLOAD_ACK.pack(b"NOPE", 0), "invalid upload response"),
        )

        for response, message in cases:
            with self.subTest(response=response):
                class ResponseTarget:
                    def __init__(self):
                        self.current = bytearray()
                        self.transmissions = []

                    def write(self, chunk, deadline=None):
                        self.current.extend(chunk)

                    def read_exact(self, _size, _timeout):
                        self.transmissions.append(bytes(self.current))
                        self.current.clear()
                        return response

                with tempfile.TemporaryDirectory() as temporary:
                    path = pathlib.Path(temporary) / "payload"
                    path.write_bytes(payload)
                    target = ResponseTarget()
                    with mock.patch.object(hil.time, "sleep"):
                        with self.assertRaisesRegex(hil.PythonHilError, message):
                            hil.send_upload_frames(
                                target,
                                path,
                                len(payload),
                                10.0,
                                1.0,
                                inject_faults=True,
                            )
                self.assertEqual(len(target.transmissions), 1)

    def test_fault_qualification_requires_a_distinct_short_final_frame(self):
        for size in (2 * hil.UPLOAD_FRAME_SIZE, 3 * hil.UPLOAD_FRAME_SIZE):
            with self.subTest(size=size):
                with tempfile.TemporaryDirectory() as temporary:
                    path = pathlib.Path(temporary) / "payload"
                    path.write_bytes(bytes(size))
                    with self.assertRaisesRegex(
                        hil.PythonHilError, "distinct short final frame"
                    ):
                        hil.send_upload_frames(
                            mock.Mock(),
                            path,
                            size,
                            10.0,
                            1.0,
                            inject_faults=True,
                        )

    def test_upload_rejects_mismatched_or_unknown_responses(self):
        payload = b"x" * 192
        cases = (
            (hil.UPLOAD_ACK_MAGIC, 191, "invalid upload ACK"),
            (hil.UPLOAD_NACK_MAGIC, 1, "invalid upload NACK"),
            (b"NOPE", 0, "invalid upload response"),
        )

        for magic, target_offset, message in cases:
            with self.subTest(magic=magic, target_offset=target_offset):
                class InvalidResponseSession:
                    def write(self, _chunk, deadline=None):
                        pass

                    def read_exact(self, _size, _timeout):
                        return hil.UPLOAD_ACK.pack(magic, target_offset)

                with tempfile.TemporaryDirectory() as temporary:
                    path = pathlib.Path(temporary) / "payload"
                    path.write_bytes(payload)
                    with mock.patch.object(hil.time, "sleep"):
                        with self.assertRaisesRegex(hil.PythonHilError, message):
                            hil.send_upload_frames(
                                InvalidResponseSession(),
                                path,
                                len(payload),
                                10.0,
                                1.0,
                            )

    def test_upload_response_timeout_is_terminal_without_retransmission(self):
        payload = b"t" * 1500

        class TimeoutSession:
            def __init__(self):
                self.current = bytearray()
                self.reads = 0

            def write(self, chunk, deadline=None):
                self.current.extend(chunk)

            def read_exact(self, _size, _timeout):
                self.reads += 1
                raise hil.PythonHilError("serial read timeout")

        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "payload"
            path.write_bytes(payload)
            session = TimeoutSession()
            with mock.patch.object(hil.time, "sleep"):
                with self.assertRaisesRegex(
                    hil.PythonHilError, "serial read timeout"
                ):
                    hil.send_upload_frames(
                        session, path, len(payload), 10.0, 1.0
                    )

        correct_first = next(
            iter(hil.upload_frames(io.BytesIO(payload), len(payload)))
        )[1]
        self.assertEqual(session.reads, 1)
        self.assertEqual(bytes(session.current), correct_first)

    def test_upload_nack_retry_budget_is_fail_closed(self):
        payload = b"z" * 192

        class RejectSession:
            def __init__(self):
                self.transmissions = 0

            def write(self, chunk, deadline=None):
                if chunk.startswith(hil.UPLOAD_FRAME.pack(0, len(payload), binascii.crc32(payload) & 0xffffffff)):
                    self.transmissions += 1

            def read_exact(self, _size, _timeout):
                return hil.UPLOAD_ACK.pack(hil.UPLOAD_NACK_MAGIC, 0)

        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "payload"
            path.write_bytes(payload)
            session = RejectSession()
            with mock.patch.object(hil.time, "sleep"):
                with self.assertRaisesRegex(
                    hil.PythonHilError, "after 3 retries"
                ):
                    hil.send_upload_frames(
                        session, path, len(payload), 10.0, 1.0
                    )

        self.assertEqual(session.transmissions, 4)

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
                    guard = FakeSerial(b"")
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
                                hil,
                                "open_serial",
                                side_effect=(guard, connection),
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
                    self.assertEqual(guard.closes, 1)
                    self.assertEqual(connection.closes, 1)

    def test_execute_keeps_guard_open_across_loader_and_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            args = types.SimpleNamespace(
                artifact_dir=root / "artifact",
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
            events = []
            run_test_arguments = []

            class TrackedSerial(FakeSerial):
                def __init__(self, name):
                    super().__init__(b"")
                    self.name = name

                def close(self):
                    events.append("close_{}".format(self.name))
                    super().close()

            guard = TrackedSerial("guard")
            connection = TrackedSerial("session")

            def open_serial(*_args):
                if not events:
                    events.append("open_guard")
                    return guard
                events.append("open_session")
                return connection

            def run_loader(*_args):
                events.append("loader")
                return types.SimpleNamespace(returncode=0, stdout=b"loaded")

            def run_tests(*_args):
                events.append("tests")
                run_test_arguments.append(_args)
                return {"completed_tests": ["mock"]}

            environment = {
                "P2_HIL": "1",
                "P2_ALLOW_RESET": "1",
                "P2_ALLOW_PSRAM_WRITE": "1",
            }
            with mock.patch.dict(hil.os.environ, environment, clear=False):
                with mock.patch.object(hil, "open_serial", side_effect=open_serial):
                    with mock.patch.object(hil, "run_loader", side_effect=run_loader):
                        with mock.patch.object(
                            hil, "run_python_tests", side_effect=run_tests
                        ):
                            hil.execute(args, {"validated": True})

            self.assertEqual(
                events,
                [
                    "open_guard",
                    "loader",
                    "open_session",
                    "tests",
                    "close_session",
                    "close_guard",
                ],
            )
            status = json.loads((args.artifact_dir / "status.json").read_text())
            self.assertEqual(
                status["serial_handoff_guard"],
                "nonreading-shared-descriptor",
            )
            self.assertEqual(len(run_test_arguments), 1)
            self.assertIs(run_test_arguments[0][-1], True)
            self.assertEqual(
                status["upload_fault_injection"],
                {
                    "enabled": True,
                    "kinds": list(hil.UPLOAD_FAULT_SEQUENCE),
                },
            )

    def test_cli_rejects_nonruntime_baud_before_preflight_or_hardware(self):
        arguments = (
            "--serial",
            "/dev/cu.board",
            "--baud",
            "115200",
            "--image",
            "/artifacts/nuttx.bin",
            "--resident-elf",
            "/artifacts/nuttx",
            "--container",
            "/artifacts/python.p2py",
            "--artifact-dir",
            "/artifacts/evidence",
        )
        with mock.patch.object(hil, "validate_artifacts") as validate:
            with mock.patch.object(hil, "open_serial") as open_serial:
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        hil.main(arguments)
        self.assertEqual(raised.exception.code, 2)
        validate.assert_not_called()
        open_serial.assert_not_called()

    def test_default_mode_is_dry_run_and_never_opens_serial(self):
        arguments = (
            "--serial",
            "/dev/cu.board",
            "--image",
            "/artifacts/nuttx.bin",
            "--resident-elf",
            "/artifacts/nuttx",
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
