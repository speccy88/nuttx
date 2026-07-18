#!/usr/bin/env python3
"""RAM-load NuttX, upload its P2 Python container, and run HIL checks.

The default is a read-only dry run.  Serial access, reset, RAM loading, and
reserved-PSRAM writes require ``--execute`` plus the standard P2 HIL safety
environment.  A successful run preserves machine-readable evidence for the
exact resident image, container, loader command, upload, and Python checks.
"""

# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import binascii
import dataclasses
import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import re
import stat
import struct
import subprocess
import sys
import time
from typing import BinaryIO, Iterable, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import p2_python_container  # noqa: E402

UPLOAD_MAGIC = b"P2PYUPL\x00"
UPLOAD_PROTOCOL = 1
UPLOAD_HEADER = struct.Struct("<8sHHIII")
UPLOAD_FRAME = struct.Struct("<III")
UPLOAD_ACK = struct.Struct("<4sI")
UPLOAD_ACK_MAGIC = b"P2AK"
UPLOAD_FRAME_SIZE = 128
MAX_UART_WRITE = 240
CONTAINER_BASE = 0x10200000
CONTAINER_CAPACITY = 10 * 1024 * 1024
LINE_MAX = 256
DEFAULT_LOCK = pathlib.Path("/tmp/nuttx-p2-python-hil.lock")


class PythonHilError(RuntimeError):
    """A fail-closed configuration, transport, or target failure."""


@dataclasses.dataclass(frozen=True)
class PythonTest:
    name: str
    marker: str
    command: str


PYTHON_TESTS: Tuple[PythonTest, ...] = (
    PythonTest(
        "arithmetic",
        "P2PYTEST:ARITH:PASS",
        "python -c 'assert (6*7,2**10)==(42,1024);" 'print("P2PYTEST:"+"ARITH:PASS")\'',
    ),
    PythonTest(
        "stdlib",
        "P2PYTEST:STDLIB:PASS",
        "python -c 'import json,collections;"
        'assert json.loads("[1,2,3]")[2]==3;'
        "assert collections.deque([1,2]).pop()==2;"
        'print("P2PYTEST:"+"STDLIB:PASS")\'',
    ),
    PythonTest(
        "allocation_gc",
        "P2PYTEST:ALLOC_GC:PASS",
        "python -c 'import gc;x=[bytearray([i&255])*8192 for i in range(1024)];"
        "assert len(x)==1024 and x[513][0]==1;del x;"
        'assert gc.collect()>=0;print("P2PYTEST:"+"ALLOC_GC:PASS")\'',
    ),
    PythonTest(
        "filesystem",
        "P2PYTEST:FILESYSTEM:PASS",
        'python -c \'import os;p="/tmp/p2py.txt";'
        'open(p,"w").write("p2-python");'
        'assert open(p).read()=="p2-python";os.unlink(p);'
        'print("P2PYTEST:"+"FILESYSTEM:PASS")\'',
    ),
    PythonTest(
        "exceptions",
        "P2PYTEST:EXCEPTION:PASS",
        "python -c 'exec(\"try:\\n  1/0\\nexcept ZeroDivisionError:\\n  "
        'print(\\"P2PYTEST:\\"+\\"EXCEPTION:PASS\\")")\'',
    ),
    PythonTest(
        "final",
        "P2PYTEST:ALL:PASS",
        'python -c \'print("P2PYTEST:"+"ALL:PASS")\'',
    ),
)

READY_RE = re.compile(
    rb"^P2PY:UPLOAD:READY:PROTO=(\d+):BASE=([0-9A-F]{8}):" rb"MAX=(\d+):FRAME=(\d+)$"
)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def upload_preamble(size: int, crc32: int) -> bytes:
    if not 192 <= size <= CONTAINER_CAPACITY:
        raise PythonHilError("container size is outside the board backing window")
    return UPLOAD_HEADER.pack(
        UPLOAD_MAGIC,
        UPLOAD_PROTOCOL,
        UPLOAD_HEADER.size,
        size,
        crc32,
        0,
    )


def upload_frames(stream: BinaryIO, size: int) -> Iterable[Tuple[int, bytes]]:
    offset = 0
    while offset < size:
        payload = stream.read(min(UPLOAD_FRAME_SIZE, size - offset))
        if len(payload) != min(UPLOAD_FRAME_SIZE, size - offset):
            raise PythonHilError("container changed or became truncated during upload")
        checksum = binascii.crc32(payload) & 0xFFFFFFFF
        yield offset + len(payload), UPLOAD_FRAME.pack(
            offset, len(payload), checksum
        ) + payload
        offset += len(payload)
    if stream.read(1):
        raise PythonHilError("container grew during upload")


def parse_ready(line: bytes) -> Tuple[int, int, int, int]:
    match = READY_RE.fullmatch(line)
    if match is None:
        raise PythonHilError("target upload READY marker is malformed")
    protocol = int(match.group(1))
    base = int(match.group(2), 16)
    capacity = int(match.group(3))
    frame_size = int(match.group(4))
    if (
        protocol != UPLOAD_PROTOCOL
        or base != CONTAINER_BASE
        or capacity != CONTAINER_CAPACITY
        or frame_size != UPLOAD_FRAME_SIZE
    ):
        raise PythonHilError("target upload contract does not match this runner")
    return protocol, base, capacity, frame_size


def validate_test_commands() -> None:
    names = set()
    markers = set()
    for test in PYTHON_TESTS:
        encoded = (test.command + "\r").encode("ascii")
        if len(encoded) > LINE_MAX:
            raise PythonHilError(
                "{} Python command exceeds CONFIG_LINE_MAX".format(test.name)
            )
        if test.marker in test.command:
            raise PythonHilError(
                "{} marker would be satisfied by console echo".format(test.name)
            )
        if test.name in names or test.marker in markers:
            raise PythonHilError("Python test names and markers must be unique")
        names.add(test.name)
        markers.add(test.marker)


class SerialSession:
    def __init__(self, connection: object):
        self.connection = connection
        self.pending = bytearray()
        self.received = bytearray()
        self.sent = bytearray()

    def write(self, data: bytes) -> None:
        if len(data) > MAX_UART_WRITE:
            raise PythonHilError("serial write exceeds the bounded UART window")
        offset = 0
        while offset < len(data):
            written = self.connection.write(data[offset:])
            if not isinstance(written, int) or written <= 0:
                raise PythonHilError("serial write made no progress")
            offset += written
        self.connection.flush()
        self.sent.extend(data)

    def _receive(self, deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise PythonHilError("serial receive timeout")
        waiting = getattr(self.connection, "in_waiting", 0)
        data = self.connection.read(max(1, min(int(waiting or 1), 4096)))
        if data:
            if not isinstance(data, bytes):
                raise PythonHilError("serial read returned non-bytes")
            self.pending.extend(data)
            self.received.extend(data)

    def read_exact(self, size: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while len(self.pending) < size:
            self._receive(deadline)
        result = bytes(self.pending[:size])
        del self.pending[:size]
        return result

    def wait_token(self, token: bytes, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            index = self.pending.find(token)
            if index >= 0:
                end = index + len(token)
                result = bytes(self.pending[:end])
                del self.pending[:end]
                return result
            self._receive(deadline)

    def wait_line_prefix(self, prefixes: Sequence[bytes], timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            newline = self.pending.find(b"\n")
            if newline >= 0:
                line = bytes(self.pending[:newline]).rstrip(b"\r")
                del self.pending[: newline + 1]
                if any(line.startswith(prefix) for prefix in prefixes):
                    return line
                continue
            self._receive(deadline)


def validate_artifacts(
    image: pathlib.Path, container_path: pathlib.Path
) -> Mapping[str, object]:
    if not image.is_file() or image.stat().st_size == 0:
        raise PythonHilError("resident NuttX image is missing or empty")
    try:
        container = p2_python_container.verify_container(container_path)
    except (OSError, p2_python_container.ContainerError) as exc:
        raise PythonHilError("invalid P2 Python container: {}".format(exc)) from exc
    if container.file_size > CONTAINER_CAPACITY:
        raise PythonHilError("container exceeds the fixed 10-MiB backing window")
    validate_test_commands()
    return {
        "image": str(image),
        "image_size": image.stat().st_size,
        "image_sha256": sha256_file(image),
        "container": str(container_path),
        "container_size": container.file_size,
        "container_sha256": sha256_file(container_path),
        "container_crc32": "{:08X}".format(
            binascii.crc32(container_path.read_bytes()) & 0xFFFFFFFF
        ),
        "container_manifest_sha256": container.manifest_sha256.hex(),
        "container_fingerprint": container.build_fingerprint.hex(),
        "overlay_load_address": "0x{:08X}".format(container.overlay_load_address),
        "overlay_slot_size": container.overlay_slot_size,
    }


def loader_command(args: argparse.Namespace) -> Tuple[str, ...]:
    reset = "-DTR" if args.reset_method == "dtr" else "-RTS"
    command = (
        str(args.loadp2),
        "-p",
        args.serial,
        "-l",
        str(args.loader_baud),
        "-b",
        str(args.baud),
        "-ZERO",
        "-v",
        reset,
        str(args.image),
    )
    if "-FLASH" in command or "-PATCH" in command:
        raise PythonHilError("persistent loadp2 operations are forbidden")
    return command


def run_loader(command: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
    """RAM-load and start NuttX, then release the serial device completely."""

    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        error = PythonHilError("loadp2 RAM load timed out: {}".format(exc))
        error.loader_output = exc.output or b""
        raise error from exc
    except OSError as exc:
        raise PythonHilError("loadp2 RAM load failed: {}".format(exc)) from exc


def open_serial(port: str, baud: int):
    """Open raw UART without asserting DTR after loadp2 releases the port."""

    try:
        import serial
    except ImportError as exc:
        raise PythonHilError("pyserial is required for Python HIL") from exc

    arguments = dict(
        port=None,
        baudrate=baud,
        timeout=0.1,
        write_timeout=10.0,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
        exclusive=True,
    )
    try:
        connection = serial.Serial(**arguments)
    except TypeError as exc:
        if "exclusive" not in str(exc):
            raise
        arguments.pop("exclusive")
        connection = serial.Serial(**arguments)

    try:
        connection.dtr = False
        connection.rts = False
        connection.port = port
        connection.open()
    except Exception:
        connection.close()
        raise
    return connection


def run_python_tests(
    session: SerialSession,
    container_path: pathlib.Path,
    boot_timeout: float,
    upload_timeout: float,
    test_timeout: float,
) -> Mapping[str, object]:
    session.write(b"\r")
    session.wait_token(b"nsh> ", boot_timeout)

    for command in (b"mkdir /tmp\r", b"mount -t tmpfs /tmp\r"):
        session.write(command)
        session.wait_token(b"nsh> ", test_timeout)

    first = PYTHON_TESTS[0]
    session.write((first.command + "\r").encode("ascii"))
    ready = session.wait_line_prefix(
        (b"P2PY:UPLOAD:READY:", b"P2PY:UPLOAD:FAIL:"), test_timeout
    )
    if ready.startswith(b"P2PY:UPLOAD:FAIL:"):
        raise PythonHilError(ready.decode("ascii", "replace"))
    parse_ready(ready)

    size = container_path.stat().st_size
    crc32 = 0
    with container_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            crc32 = binascii.crc32(block, crc32)
    crc32 &= 0xFFFFFFFF
    session.write(upload_preamble(size, crc32))
    accept = session.wait_line_prefix(
        (b"P2PY:UPLOAD:ACCEPT:", b"P2PY:UPLOAD:FAIL:"), test_timeout
    )
    if accept.startswith(b"P2PY:UPLOAD:FAIL:"):
        raise PythonHilError(accept.decode("ascii", "replace"))
    expected_accept = "P2PY:UPLOAD:ACCEPT:SIZE={}:CRC={:08X}".format(
        size, crc32
    ).encode("ascii")
    if accept != expected_accept:
        raise PythonHilError("target upload ACCEPT marker does not match artifact")

    started = time.monotonic()
    with container_path.open("rb") as stream:
        for committed, frame in upload_frames(stream, size):
            if time.monotonic() - started >= upload_timeout:
                raise PythonHilError("container upload exceeded its deadline")
            session.write(frame)
            raw_ack = session.read_exact(UPLOAD_ACK.size, test_timeout)
            magic, target_committed = UPLOAD_ACK.unpack(raw_ack)
            if magic != UPLOAD_ACK_MAGIC or target_committed != committed:
                raise PythonHilError("target returned an invalid upload ACK")

    upload = session.wait_line_prefix(
        (b"P2PY:UPLOAD:PASS:", b"P2PY:UPLOAD:FAIL:"), test_timeout * 3
    )
    if upload.startswith(b"P2PY:UPLOAD:FAIL:"):
        raise PythonHilError(upload.decode("ascii", "replace"))
    expected_upload = "P2PY:UPLOAD:PASS:SIZE={}:CRC={:08X}".format(size, crc32).encode(
        "ascii"
    )
    if upload != expected_upload:
        raise PythonHilError("target upload PASS marker does not match artifact")
    runtime = session.wait_line_prefix(
        (b"P2PY:RUNTIME:READY:", b"P2PY:UPLOAD:FAIL:"), test_timeout
    )
    if not runtime.startswith(b"P2PY:RUNTIME:READY:"):
        raise PythonHilError(runtime.decode("ascii", "replace"))

    completed = []
    failure_prefixes = (b"Traceback ", b"ERROR:", b"P2PY:UPLOAD:FAIL:")
    for index, test in enumerate(PYTHON_TESTS):
        if index > 0:
            session.write((test.command + "\r").encode("ascii"))
        result = session.wait_line_prefix(
            (test.marker.encode("ascii"),) + failure_prefixes, test_timeout
        )
        if result != test.marker.encode("ascii"):
            raise PythonHilError(
                "{} Python test failed: {}".format(
                    test.name, result.decode("ascii", "replace")
                )
            )
        completed.append(test.name)
        session.wait_token(b"nsh> ", test_timeout)

    return {
        "completed_tests": completed,
        "upload_size": size,
        "upload_crc32": "{:08X}".format(crc32),
        "ready_marker": ready.decode("ascii"),
        "upload_marker": upload.decode("ascii"),
        "runtime_marker": runtime.decode("ascii"),
    }


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--baud", type=int, default=230400)
    parser.add_argument("--loader-baud", type=int, default=2000000)
    parser.add_argument("--loadp2", type=pathlib.Path)
    parser.add_argument("--image", required=True, type=pathlib.Path)
    parser.add_argument("--container", required=True, type=pathlib.Path)
    parser.add_argument("--artifact-dir", required=True, type=pathlib.Path)
    parser.add_argument("--reset-method", choices=("dtr", "rts"), default="dtr")
    parser.add_argument("--load-timeout", type=float, default=60.0)
    parser.add_argument("--boot-timeout", type=float, default=90.0)
    parser.add_argument("--upload-timeout", type=float, default=900.0)
    parser.add_argument("--test-timeout", type=float, default=120.0)
    parser.add_argument("--lock-file", type=pathlib.Path, default=DEFAULT_LOCK)
    args = parser.parse_args(argv)
    for name in (
        "baud",
        "loader_baud",
        "load_timeout",
        "boot_timeout",
        "upload_timeout",
        "test_timeout",
    ):
        if getattr(args, name) <= 0:
            parser.error("--{} must be positive".format(name.replace("_", "-")))
    args.image = args.image.expanduser().resolve()
    args.container = args.container.expanduser().resolve()
    args.artifact_dir = args.artifact_dir.expanduser().resolve()
    args.lock_file = args.lock_file.expanduser().resolve()
    if args.loadp2 is not None:
        args.loadp2 = args.loadp2.expanduser().resolve()
    return args


def execute(args: argparse.Namespace, inputs: Mapping[str, object]) -> int:
    if args.artifact_dir.exists():
        raise PythonHilError("artifact directory already exists")
    args.artifact_dir.mkdir(parents=True)

    status = {
        "format": "p2-python-hil-v1",
        "status": "RUNNING",
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "inputs": dict(inputs),
        "serial": args.serial,
        "baud": args.baud,
        "tests": [dataclasses.asdict(test) for test in PYTHON_TESTS],
    }
    status_path = args.artifact_dir / "status.json"
    loader_output = b""
    session: Optional[SerialSession] = None
    try:
        for variable in ("P2_HIL", "P2_ALLOW_RESET", "P2_ALLOW_PSRAM_WRITE"):
            if os.environ.get(variable) != "1":
                raise PythonHilError("{}=1 is required with --execute".format(variable))
        if (
            args.loadp2 is None
            or not args.loadp2.is_file()
            or not os.access(args.loadp2, os.X_OK)
        ):
            raise PythonHilError("--loadp2 must name the executable pinned loader")
        try:
            mode = os.stat(args.serial).st_mode
        except OSError as exc:
            raise PythonHilError(
                "serial device is unavailable: {}".format(exc)
            ) from exc
        if not stat.S_ISCHR(mode):
            raise PythonHilError("--serial must name a character device")

        args.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with args.lock_file.open("a+b") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise PythonHilError("P2 Python HIL lock is busy") from exc

            command = loader_command(args)
            status["loader_command"] = list(command)
            status_path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
            try:
                loaded = run_loader(command, args.load_timeout)
            except BaseException as exc:
                loader_output = getattr(exc, "loader_output", b"")
                raise
            loader_output = loaded.stdout
            status["loader_exit_code"] = loaded.returncode
            if loaded.returncode != 0:
                raise PythonHilError(
                    "loadp2 exited with status {}".format(loaded.returncode)
                )

            connection = open_serial(args.serial, args.baud)
            try:
                session = SerialSession(connection)
                result = run_python_tests(
                    session,
                    args.container,
                    args.boot_timeout,
                    args.upload_timeout,
                    args.test_timeout,
                )
            finally:
                connection.close()

        status.update(result)
        status["status"] = "PASS"
    except BaseException as exc:
        status["status"] = "FAIL"
        status["failure_type"] = type(exc).__name__
        status["reason"] = str(exc) or repr(exc)
        raise
    finally:
        serial_rx = bytes(session.received) if session is not None else b""
        serial_tx = bytes(session.sent) if session is not None else b""
        (args.artifact_dir / "loader.log").write_bytes(loader_output)
        (args.artifact_dir / "serial.raw").write_bytes(serial_rx)
        (args.artifact_dir / "serial-tx.raw").write_bytes(serial_tx)
        status["serial_rx_bytes"] = len(serial_rx)
        status["serial_tx_bytes"] = len(serial_tx)
        status["ended_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        status_path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")

    print("P2PYHIL:PASS:ARTIFACT={}".format(args.artifact_dir))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = parse_args(argv)
        inputs = validate_artifacts(args.image, args.container)
        plan = {
            "format": "p2-python-hil-plan-v1",
            "mode": "execute" if args.execute else "dry-run",
            "serial": args.serial,
            "baud": args.baud,
            "artifact_dir": str(args.artifact_dir),
            "inputs": inputs,
            "tests": [test.name for test in PYTHON_TESTS],
        }
        if not args.execute:
            print(json.dumps(plan, indent=2, sort_keys=True))
            print("DRY-RUN: no serial open, reset, RAM load, or PSRAM write occurred")
            return 0
        return execute(args, inputs)
    except PythonHilError as exc:
        print("P2PYHIL:FAIL:{}".format(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("P2PYHIL:FAIL:interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(
            "P2PYHIL:FAIL:{}:{}".format(type(exc).__name__, exc),
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
