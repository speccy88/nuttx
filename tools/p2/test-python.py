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
import p2_python_package  # noqa: E402

UPLOAD_MAGIC = b"P2PYUPL\x00"
UPLOAD_PROTOCOL = 2
UPLOAD_HEADER = struct.Struct("<8sHHIII")
UPLOAD_FRAME = struct.Struct("<III")
UPLOAD_ACK = struct.Struct("<4sI")
UPLOAD_ACK_MAGIC = b"P2AK"
UPLOAD_NACK_MAGIC = b"P2NK"
UPLOAD_FRAME_SIZE = 1024
UPLOAD_FRAME_RETRIES = 3
UPLOAD_FAULT_BAD_CRC = "bad_crc"
UPLOAD_FAULT_BAD_OFFSET = "bad_offset"
UPLOAD_FAULT_BAD_FINAL_SIZE = "bad_final_size"
UPLOAD_FAULT_SEQUENCE = (
    UPLOAD_FAULT_BAD_CRC,
    UPLOAD_FAULT_BAD_OFFSET,
    UPLOAD_FAULT_BAD_FINAL_SIZE,
)
# Real P2 HIL showed that pipelining frames can overrun the software UART RX
# path while the target is synchronously writing PSRAM.  Protocol v2 therefore
# has exactly one logical frame in flight.  The larger frame is split into
# bounded host writes and is committed only after the target validates it.

UPLOAD_WINDOW_FRAMES = 1
UPLOAD_PROGRESS_INTERVAL = 1024 * 1024
MAX_UART_WRITE = 240
UPLOAD_WIRE_CHUNK_SIZE = 224
UPLOAD_CHUNK_GAP_SECONDS = 0.010
RUNTIME_BAUD = 230400
UART_BITS_PER_BYTE = 10
UPLOAD_CHUNK_WIRE_SECONDS = (
    UPLOAD_WIRE_CHUNK_SIZE * UART_BITS_PER_BYTE / RUNTIME_BAUD
)
UPLOAD_CHUNK_PAUSE_SECONDS = (
    UPLOAD_CHUNK_WIRE_SECONDS + UPLOAD_CHUNK_GAP_SECONDS
)
CONTAINER_BASE = 0x10300000
CONTAINER_CAPACITY = 13 * 1024 * 1024
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
        "float_libm",
        "P2PYTEST:FLOAT:PASS",
        "python -c 'import math;"
        "assert math.isclose(math.sin(math.pi/2),1.0,abs_tol=1e-12);"
        "assert math.sqrt(81.0)==9.0;"
        'print("P2PYTEST:"+"FLOAT:PASS")\'',
    ),
    PythonTest(
        "unicode",
        "P2PYTEST:UNICODE:PASS",
        "python -c 's=chr(0x3c0)+chr(0x1f680);b=s.encode(\"utf-8\");"
        "assert b.decode(\"utf-8\")==s and len(s)==2;"
        'print("P2PYTEST:"+"UNICODE:PASS")\'',
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
        "zlib_sizes",
        "P2PYTEST:ZLIB_SIZES:PASS",
        "python -c 'import zlib;xs=(b\"\",b\"x\",b\"abc\"*12000);"
        "assert all(zlib.decompress(zlib.compress(x))==x for x in xs);"
        'print("P2PYTEST:"+"ZLIB_SIZES:PASS")\'',
    ),
    PythonTest(
        "zlib_incompressible",
        "P2PYTEST:ZLIB_RANDOM:PASS",
        "python -c 'import random,zlib;x=random.Random(7).randbytes(40000);"
        "c=zlib.compress(x);assert len(c)>len(x) and zlib.decompress(c)==x;"
        'print("P2PYTEST:"+"ZLIB_RANDOM:PASS")\'',
    ),
    PythonTest(
        "zlib_streaming",
        "P2PYTEST:ZLIB_STREAM:PASS",
        "python -c 'import zlib as z;s=b\"a\"*40000;o=z.compressobj();"
        "c=o.compress(s[:1])+o.compress(s[1:])+o.flush();"
        "d=z.decompressobj();r=d.decompress(c[:2])+d.decompress(c[2:])+d.flush();"
        "assert r==s and d.eof;"
        'print("P2PY""TEST:ZLIB_STREAM:PASS")\'',
    ),
    PythonTest(
        "zlib_checksums",
        "P2PYTEST:ZLIB_CHECKSUM:PASS",
        "python -c 'import zlib;s=b\"123456789\";"
        "assert zlib.adler32(s)==0x091e01de and zlib.crc32(s)==0xcbf43926;"
        'print("P2PYTEST:"+"ZLIB_CHECKSUM:PASS")\'',
    ),
    PythonTest(
        "hardware_entropy",
        "P2PYTEST:ENTROPY:PASS",
        "python -c 'import os,secrets;a=os.urandom(256);b=secrets.token_bytes(256);"
        "assert len(a)==len(b)==256 and a!=b and any(a) and any(b);"
        'print("P2PYTEST:ENTROPY:"+"FINGERPRINT:"+a[:16].hex());'
        'print("P2PYTEST:"+"ENTROPY:PASS")\'',
    ),
    PythonTest(
        "runtime_paths",
        "P2PYTEST:PATHS:PASS",
        "python -c 'import sys;assert sys.prefix==sys.exec_prefix==\"/usr/local\";"
        "assert \"/usr/local/lib/python313.zip\" in sys.path;"
        'print("P2PYTEST:"+"PATHS:PASS")\'',
    ),
    PythonTest(
        "user_site_contract",
        "P2PYTEST:USER_SITE:PASS",
        "python -c 'import pathlib,site;"
        "assert str(pathlib.Path.home())==\"/tmp\";"
        "assert site.ENABLE_USER_SITE is False;"
        'print("P2PYTEST:"+"USER_SITE:PASS")\'',
    ),
    PythonTest(
        "ignore_environment",
        "P2PYTEST:IGNORE_ENV:PASS",
        "python -E -c 'import encodings,sys;assert sys.prefix==\"/usr/local\";"
        "assert \"/usr/local/lib/python313.zip\" in sys.path;"
        'print("P2PYTEST:"+"IGNORE_ENV:PASS")\'',
    ),
    PythonTest(
        "isolated_mode",
        "P2PYTEST:ISOLATED:PASS",
        "python -I -c 'import encodings,sys;assert sys.prefix==\"/usr/local\";"
        "assert \"/usr/local/lib/python313.zip\" in sys.path;"
        'print("P2PYTEST:"+"ISOLATED:PASS")\'',
    ),
    PythonTest(
        "allocation_gc",
        "P2PYTEST:ALLOC_GC:PASS",
        "python -c 'import gc;x=[bytearray([i&255])*8192 for i in range(1024)];"
        "assert len(x)==1024 and all(b[0]==b[4096]==b[-1]==(i&255) "
        "for i,b in enumerate(x));del x;"
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
        "filesystem_large",
        "P2PYTEST:FILESYSTEM_LARGE:PASS",
        'python -c \'p="/tmp/f";d=bytes(range(256))*3072;'
        'assert open(p,"wb").write(d)==786432;'
        'assert open(p,"rb").read()==d;import os;os.unlink(p);'
        'assert open(p,"wb").write(b"x")==1;os.unlink(p);'
        'print("P2PYTEST:"+"FILESYSTEM_LARGE:PASS")\'',
    ),
    PythonTest(
        "exceptions",
        "P2PYTEST:EXCEPTION:PASS",
        "python -c 'exec(\"try:\\n  1/0\\nexcept ZeroDivisionError:\\n  "
        'print(\\"P2PYTEST:\\"+\\"EXCEPTION:PASS\\")")\'',
    ),
    PythonTest(
        "tracemalloc_tls",
        "P2PYTEST:TRACEMALLOC:PASS",
        "python -c 'import tracemalloc;tracemalloc.start();x=bytearray(4096);"
        "assert tracemalloc.is_tracing() and tracemalloc.get_traced_memory()[0]>0;"
        'tracemalloc.stop();print("P2PYTEST:"+"TRACEMALLOC:PASS")\'',
    ),
    PythonTest(
        "restart_state_seed",
        "P2PYTEST:STATE_SEED:PASS",
        "python -c 'import builtins,sys;builtins._p2_leak=1;"
        "sys.modules[\"_p2_leak\"]=builtins;"
        "open(\"/tmp/p2hash\",\"w\").write(str(hash(\"p2-fixed\")));"
        'print("P2PYTEST:"+"STATE_SEED:PASS")\'',
    ),
    PythonTest(
        "restart_state_isolation",
        "P2PYTEST:STATE_ISOLATION:PASS",
        "python -c 'import builtins,sys,os;"
        "assert not hasattr(builtins,\"_p2_leak\");"
        "assert \"_p2_leak\" not in sys.modules;"
        "assert int(open(\"/tmp/p2hash\").read())!=hash(\"p2-fixed\");"
        "os.unlink(\"/tmp/p2hash\");"
        'print("P2PYTEST:"+"STATE_ISOLATION:PASS")\'',
    ),
    PythonTest(
        "deep_recursion",
        "P2PYTEST:DEEP:PASS",
        'python -c \'x=eval("["*100+"0"+"]"*100);'
        'assert x==eval("["*100+"0"+"]"*100);'
        "assert len(repr(x))==201;"
        'print("P2PYTEST:"+"DEEP:PASS")\'',
    ),
    PythonTest(
        "threads_unsupported",
        "P2PYTEST:NO_THREAD:PASS",
        "python -c 'import importlib.util;"
        "assert importlib.util.find_spec(\"_thread\") is None;"
        'print("P2PYTEST:"+"NO_THREAD:PASS")\'',
    ),
    PythonTest(
        "subinterpreters_unsupported",
        "P2PYTEST:NO_SUBINTERPRETERS:PASS",
        "python -c 'import importlib.util;"
        "assert importlib.util.find_spec(\"_interpreters\") is None;"
        'print("P2PYTEST:"+"NO_SUBINTERPRETERS:PASS")\'',
    ),
    PythonTest(
        "final",
        "P2PYTEST:ALL:PASS",
        'python -c \'print("P2PYTEST:"+"ALL:PASS")\'',
    ),
)

CONCURRENCY_HOLDER_MARKER = "P2PYTEST:CONCURRENCY:HOLDER"
CONCURRENCY_DONE_MARKER = "P2PYTEST:CONCURRENCY:DONE"
CONCURRENCY_SECOND_MARKER = "P2PYTEST:CONCURRENCY:SECOND_RAN"
CONCURRENCY_POST_MARKER = "P2PYTEST:CONCURRENCY:POST_PASS"
CONCURRENCY_BUSY_PREFIX = "P2PY:RUNTIME:BUSY:CODE="
WORKER_EXIT_PREFIX = "P2PY:WORKER:EXIT:CODE="
ENTROPY_FINGERPRINT_PREFIX = "P2PYTEST:ENTROPY:FINGERPRINT:"
CONCURRENCY_HOLDER_COMMAND = (
    "python -c 'import time;"
    "time.sleep(1);"
    'print("P2PYTEST:"+"CONCURRENCY:HOLDER");'
    "time.sleep(10);"
    'print("P2PYTEST:"+"CONCURRENCY:DONE")\' &'
)
CONCURRENCY_SECOND_COMMAND = 'python -c \'print("P2PYTEST:"+"CONCURRENCY:SECOND_RAN")\''
CONCURRENCY_POST_COMMAND = 'python -c \'print("P2PYTEST:"+"CONCURRENCY:POST_PASS")\''
RESTART_STRESS_COUNT = 20
STACK_MINIMUM_FREE = 2048

READY_RE = re.compile(
    rb"^P2PY:UPLOAD:READY:PROTO=(\d+):BASE=([0-9A-F]{8}):"
    rb"MAX=(\d+):FRAME=(\d+):BAUD=(\d+)$"
)
STACK_RE = re.compile(rb"^P2PY:WORKER:STACK:FREE=(\d+):SIZE=(\d+)$")
WORKER_EXIT_RE = re.compile(rb"^P2PY:WORKER:EXIT:CODE=(-?\d+)$")
ENTROPY_FINGERPRINT_RE = re.compile(
    rb"^P2PYTEST:ENTROPY:FINGERPRINT:([0-9a-f]{32})$"
)
RUNTIME_STAGES = (
    b"P2PY:TMPFS:READY:PATH=/tmp:HEAP=1048576",
    b"P2PY:ROMDISK:READY:MODE=BUFFERED:SECTOR=512",
    b"P2PY:ROMFS:MOUNTED",
    b"P2PY:CPYTHON:EARLY:START",
    b"P2PY:CPYTHON:EARLY:PASS",
    b"P2PY:CPYTHON:RUN",
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


def send_logical_frame(
    session: "SerialSession", frame: bytes, deadline: Optional[float] = None
) -> None:
    """Send one logical frame within the RX-ring and upload deadlines.

    ``SerialSession.write`` caps pyserial's write timeout to the remaining
    monotonic deadline and checks it across partial writes.  Every subsequent
    wire chunk and pacing pause rechecks the same overall deadline.
    """

    for offset in range(0, len(frame), UPLOAD_WIRE_CHUNK_SIZE):
        if deadline is not None and time.monotonic() >= deadline:
            raise PythonHilError("container upload exceeded its deadline")
        end = min(offset + UPLOAD_WIRE_CHUNK_SIZE, len(frame))
        session.write(frame[offset:end], deadline=deadline)
        if end < len(frame):
            # The P2 lower RX ring is 256 bytes and is normally drained every
            # 10 ms.  A 224-byte burst takes about 9.72 ms at 230400 8N1;
            # pairing it with a 10-ms quiet interval keeps every 20-ms span
            # below 256 bytes even if one timer service is missed.

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining < UPLOAD_CHUNK_PAUSE_SECONDS:
                    raise PythonHilError(
                        "container upload exceeded its deadline"
                    )
            # pyserial.flush() reaches an unbounded POSIX tcdrain().  Waiting
            # for one complete chunk's 8N1 wire time plus the proven quiet
            # gap preserves the on-wire pacing without an unbounded syscall.

            time.sleep(UPLOAD_CHUNK_PAUSE_SECONDS)


def faulted_upload_frame(frame: bytes, kind: str) -> bytes:
    """Return a byte-count-preserving deterministic invalid frame."""

    if len(frame) < UPLOAD_FRAME.size:
        raise PythonHilError("generated upload frame is truncated")
    frame_offset, payload_size, checksum = UPLOAD_FRAME.unpack(
        frame[: UPLOAD_FRAME.size]
    )
    payload = frame[UPLOAD_FRAME.size :]
    if len(payload) != payload_size:
        raise PythonHilError("generated upload frame has invalid payload size")

    if kind == UPLOAD_FAULT_BAD_CRC:
        checksum ^= 1
    elif kind == UPLOAD_FAULT_BAD_OFFSET:
        frame_offset += 1
    elif kind == UPLOAD_FAULT_BAD_FINAL_SIZE:
        if payload_size >= UPLOAD_FRAME_SIZE:
            raise PythonHilError(
                "declared-size fault requires a short final upload frame"
            )
        payload_size += 1
    else:
        raise PythonHilError("unknown upload fault injection kind: {}".format(kind))

    # Only header fields are substituted.  The expected number of payload
    # bytes stays on the wire so the target can drain the complete attempt,
    # issue P2NK(current_offset), and safely accept the exact retransmission.

    return UPLOAD_FRAME.pack(frame_offset, payload_size, checksum) + payload


def send_upload_frames(
    session: "SerialSession",
    container_path: pathlib.Path,
    size: int,
    upload_timeout: float,
    ack_timeout: float,
    inject_faults: bool = False,
) -> Mapping[str, object]:
    """Send logical frames one at a time with bounded explicit-NACK retry."""

    if inject_faults and (
        size <= 2 * UPLOAD_FRAME_SIZE or size % UPLOAD_FRAME_SIZE == 0
    ):
        raise PythonHilError(
            "upload fault qualification requires two full frames and a "
            "distinct short final frame"
        )

    started = time.monotonic()
    deadline = started + upload_timeout
    frame_count = 0
    frame_transmissions = 0
    frame_retries = 0
    injected_faults = []
    next_progress = UPLOAD_PROGRESS_INTERVAL

    with container_path.open("rb") as stream:
        for committed, frame in upload_frames(stream, size):
            frame_count += 1
            frame_offset, payload_size, _checksum = UPLOAD_FRAME.unpack(
                frame[: UPLOAD_FRAME.size]
            )
            if frame_offset + payload_size != committed:
                raise PythonHilError("generated upload frame has invalid bounds")

            retries = 0
            fault_kind = None
            if inject_faults:
                if frame_count == 1:
                    fault_kind = UPLOAD_FAULT_BAD_CRC
                elif frame_count == 2:
                    fault_kind = UPLOAD_FAULT_BAD_OFFSET
                elif committed == size and payload_size < UPLOAD_FRAME_SIZE:
                    fault_kind = UPLOAD_FAULT_BAD_FINAL_SIZE
            fault_sent = False
            while True:
                remaining = upload_timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise PythonHilError("container upload exceeded its deadline")

                injected_attempt = fault_kind is not None and not fault_sent
                outbound = (
                    faulted_upload_frame(frame, fault_kind)
                    if injected_attempt
                    else frame
                )
                if injected_attempt:
                    injected_faults.append(
                        {
                            "kind": fault_kind,
                            "frame_offset": frame_offset,
                            "transmission": frame_transmissions + 1,
                        }
                    )
                    fault_sent = True
                send_logical_frame(session, outbound, deadline)
                frame_transmissions += 1

                remaining = upload_timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise PythonHilError("container upload exceeded its deadline")
                raw_response = session.read_exact(
                    UPLOAD_ACK.size, min(ack_timeout, remaining)
                )
                magic, target_offset = UPLOAD_ACK.unpack(raw_response)

                if magic == UPLOAD_ACK_MAGIC:
                    if injected_attempt:
                        raise PythonHilError(
                            "target ACKed deliberately invalid upload frame: "
                            "kind={} raw={}".format(
                                fault_kind, raw_response.hex()
                            )
                        )
                    if target_offset != committed:
                        raise PythonHilError(
                            "target returned an invalid upload ACK: "
                            "expected={} raw={}".format(
                                committed, raw_response.hex()
                            )
                        )
                    break

                if magic == UPLOAD_NACK_MAGIC:
                    if target_offset != frame_offset:
                        raise PythonHilError(
                            "target returned an invalid upload NACK: "
                            "expected={} raw={}".format(
                                frame_offset, raw_response.hex()
                            )
                        )
                    if retries >= UPLOAD_FRAME_RETRIES:
                        raise PythonHilError(
                            "target rejected upload frame at offset {} after {} "
                            "retries".format(frame_offset, retries)
                        )
                    retries += 1
                    frame_retries += 1
                    continue

                raise PythonHilError(
                    "target returned an invalid upload response: raw={}".format(
                        raw_response.hex()
                    )
                )

            if committed >= next_progress or committed == size:
                print(
                    "P2PYHIL:UPLOAD:ACKED={}:TOTAL={}".format(committed, size),
                    flush=True,
                )
                while next_progress <= committed:
                    next_progress += UPLOAD_PROGRESS_INTERVAL

    injected_fault_kinds = [fault["kind"] for fault in injected_faults]
    if inject_faults and tuple(injected_fault_kinds) != UPLOAD_FAULT_SEQUENCE:
        raise PythonHilError("upload fault qualification sequence was incomplete")

    return {
        "frame_count": frame_count,
        "frame_transmissions": frame_transmissions,
        "frame_retries": frame_retries,
        "fault_injection_enabled": inject_faults,
        "injected_fault_count": len(injected_faults),
        "injected_fault_kinds": injected_fault_kinds,
        "injected_faults": injected_faults,
        "window_frames": UPLOAD_WINDOW_FRAMES,
        "window_count": frame_count,
        "wire_chunk_bytes": UPLOAD_WIRE_CHUNK_SIZE,
        "wire_chunk_seconds": UPLOAD_CHUNK_WIRE_SECONDS,
        "inter_chunk_gap_seconds": UPLOAD_CHUNK_GAP_SECONDS,
        "inter_chunk_pause_seconds": UPLOAD_CHUNK_PAUSE_SECONDS,
        "seconds": time.monotonic() - started,
    }


def parse_ready(line: bytes) -> Tuple[int, int, int, int, int]:
    match = READY_RE.fullmatch(line)
    if match is None:
        raise PythonHilError("target upload READY marker is malformed")
    protocol = int(match.group(1))
    base = int(match.group(2), 16)
    capacity = int(match.group(3))
    frame_size = int(match.group(4))
    baud = int(match.group(5))
    if (
        protocol != UPLOAD_PROTOCOL
        or base != CONTAINER_BASE
        or capacity != CONTAINER_CAPACITY
        or frame_size != UPLOAD_FRAME_SIZE
        or baud != RUNTIME_BAUD
    ):
        raise PythonHilError("target upload contract does not match this runner")
    return protocol, base, capacity, frame_size, baud


def upload_pass_marker(size: int, crc32: int) -> bytes:
    """Build the exact success marker, including zero UART RX drops."""

    return "P2PY:UPLOAD:PASS:SIZE={}:CRC={:08X}:RXDROPS=0".format(
        size, crc32
    ).encode("ascii")


def validate_test_commands() -> None:
    names = set()
    markers = set()
    for test in PYTHON_TESTS:
        encoded = (test.command + "\r").encode("ascii")
        if len(encoded) > LINE_MAX or len(encoded) > MAX_UART_WRITE:
            raise PythonHilError(
                "{} Python command exceeds the console write bound".format(
                    test.name
                )
            )
        if test.marker in test.command:
            raise PythonHilError(
                "{} marker would be satisfied by console echo".format(test.name)
            )
        if test.name in names or test.marker in markers:
            raise PythonHilError("Python test names and markers must be unique")
        names.add(test.name)
        markers.add(test.marker)

    for command in (
        CONCURRENCY_HOLDER_COMMAND,
        CONCURRENCY_SECOND_COMMAND,
        CONCURRENCY_POST_COMMAND,
    ):
        encoded = (command + "\r").encode("ascii")
        if len(encoded) > LINE_MAX or len(encoded) > MAX_UART_WRITE:
            raise PythonHilError(
                "concurrency command exceeds the console write bound"
            )
    for marker in (
        CONCURRENCY_HOLDER_MARKER,
        CONCURRENCY_DONE_MARKER,
        CONCURRENCY_SECOND_MARKER,
        CONCURRENCY_POST_MARKER,
    ):
        if marker in CONCURRENCY_HOLDER_COMMAND or marker in CONCURRENCY_SECOND_COMMAND:
            raise PythonHilError(
                "concurrency marker would be satisfied by console echo"
            )


class SerialSession:
    def __init__(self, connection: object):
        self.connection = connection
        self.pending = bytearray()
        self.received = bytearray()
        self.sent = bytearray()

    def write(
        self, data: bytes, deadline: Optional[float] = None
    ) -> None:
        if len(data) > MAX_UART_WRITE:
            raise PythonHilError("serial write exceeds the bounded UART window")
        original_write_timeout = None
        deadline_bounded = deadline is not None
        if deadline_bounded:
            if not hasattr(self.connection, "write_timeout"):
                raise PythonHilError(
                    "serial connection cannot enforce the upload deadline"
                )
            original_write_timeout = self.connection.write_timeout
        offset = 0
        try:
            while offset < len(data):
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise PythonHilError(
                            "container upload exceeded its deadline"
                        )
                    if original_write_timeout is None:
                        self.connection.write_timeout = remaining
                    else:
                        self.connection.write_timeout = min(
                            original_write_timeout, remaining
                        )
                try:
                    written = self.connection.write(data[offset:])
                except Exception as exc:
                    if (
                        deadline is not None
                        and time.monotonic() >= deadline
                    ):
                        raise PythonHilError(
                            "container upload exceeded its deadline"
                        ) from exc
                    raise
                if (
                    not isinstance(written, int)
                    or written <= 0
                    or written > len(data) - offset
                ):
                    raise PythonHilError("serial write made no progress")
                self.sent.extend(data[offset : offset + written])
                offset += written
                if deadline is not None and time.monotonic() >= deadline:
                    raise PythonHilError(
                        "container upload exceeded its deadline"
                    )
        finally:
            if deadline_bounded:
                self.connection.write_timeout = original_write_timeout

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
    image: pathlib.Path,
    resident_elf: pathlib.Path,
    container_path: pathlib.Path,
) -> Mapping[str, object]:
    if not image.is_file() or image.stat().st_size == 0:
        raise PythonHilError("resident NuttX image is missing or empty")
    try:
        container = p2_python_container.verify_container(container_path)
    except (OSError, p2_python_container.ContainerError) as exc:
        raise PythonHilError("invalid P2 Python container: {}".format(exc)) from exc
    if container.file_size > CONTAINER_CAPACITY:
        raise PythonHilError("container exceeds the fixed 13-MiB backing window")
    try:
        p2_python_package.verify_resident_elf(
            resident_elf, container.build_fingerprint
        )
    except (OSError, p2_python_package.PackageError) as exc:
        raise PythonHilError(
            "resident ELF does not match the P2 Python container: {}".format(exc)
        ) from exc
    validate_test_commands()
    return {
        "image": str(image),
        "image_size": image.stat().st_size,
        "image_sha256": sha256_file(image),
        "resident_elf": str(resident_elf),
        "resident_elf_sha256": sha256_file(resident_elf),
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
    """Open a shared UART descriptor while preserving inactive reset lines.

    On macOS, the last close of a tty with HUPCL can pulse the board reset
    line.  The HIL transaction deliberately keeps one non-reading descriptor
    open across loadp2's close and the test-session open.  Both descriptors
    therefore have to be shared and must keep DTR/RTS deasserted electrically
    (the USB adapter uses the asserted boolean state as the inactive level).
    """

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
        exclusive=False,
    )
    try:
        connection = serial.Serial(**arguments)
    except TypeError as exc:
        if "exclusive" not in str(exc):
            raise
        arguments.pop("exclusive")
        connection = serial.Serial(**arguments)

    try:
        connection.dtr = True
        connection.rts = True
        connection.port = port
        connection.open()
    except Exception:
        connection.close()
        raise
    return connection


def wait_stack_telemetry(
    session: SerialSession, test_timeout: float
) -> Mapping[str, int]:
    line = session.wait_line_prefix(
        (
            b"P2PY:WORKER:STACK:",
            b"Traceback ",
            b"ERROR:",
            b"P2PY:UPLOAD:FAIL:",
        ),
        test_timeout,
    )
    match = STACK_RE.fullmatch(line)
    if match is None:
        raise PythonHilError(
            "Python worker stack telemetry is missing or malformed: {}".format(
                line.decode("ascii", "replace")
            )
        )

    free = int(match.group(1))
    size = int(match.group(2))
    if size <= 0 or free > size:
        raise PythonHilError("Python worker stack telemetry is impossible")
    if free < STACK_MINIMUM_FREE:
        raise PythonHilError(
            "Python worker stack headroom {} is below {} bytes".format(
                free, STACK_MINIMUM_FREE
            )
        )
    return {"free": free, "size": size, "used": size - free}


def wait_worker_exit(session: SerialSession, test_timeout: float) -> int:
    line = session.wait_line_prefix(
        (
            WORKER_EXIT_PREFIX.encode("ascii"),
            b"Traceback ",
            b"ERROR:",
            b"P2PY:UPLOAD:FAIL:",
        ),
        test_timeout,
    )
    match = WORKER_EXIT_RE.fullmatch(line)
    if match is None:
        raise PythonHilError(
            "CPython worker exit status is missing or malformed: {}".format(
                line.decode("ascii", "replace")
            )
        )

    code = int(match.group(1))
    if code != 0:
        raise PythonHilError("CPython worker exited with status {}".format(code))
    return code


def parse_entropy_fingerprint(line: bytes) -> str:
    match = ENTROPY_FINGERPRINT_RE.fullmatch(line)
    if match is None:
        raise PythonHilError("hardware entropy fingerprint is malformed")
    return match.group(1).decode("ascii")


def run_nsh_setup(
    session: SerialSession, command: bytes, test_timeout: float
) -> str:
    session.write(command + b"\r")
    output = session.wait_token(b"nsh> ", test_timeout)
    for failure in (b"command not found", b" failed:", b"ERROR:"):
        if failure in output:
            raise PythonHilError(
                "NSH setup command failed: {}: {}".format(
                    command.decode("ascii"), output.decode("ascii", "replace")
                )
            )
    return output.decode("ascii", "replace")


def run_python_tests(
    session: SerialSession,
    container_path: pathlib.Path,
    boot_timeout: float,
    upload_timeout: float,
    test_timeout: float,
    inject_upload_faults: bool = False,
) -> Mapping[str, object]:
    session.write(b"\r")
    session.wait_token(b"nsh> ", boot_timeout)

    # The P2 CPython launcher owns /tmp setup.  Pre-mounting it here would
    # hide a board artifact that only works after manual shell preparation.
    shell_setup = []

    first = PYTHON_TESTS[0]
    session.write((first.command + "\r").encode("ascii"))
    ready = session.wait_line_prefix(
        (
            b"P2PY:UPLOAD:READY:",
            b"P2PY:UPLOAD:FAIL:",
            b"nsh: python:",
            b"ERROR:",
        ),
        test_timeout,
    )
    if ready.startswith(b"nsh: python:"):
        raise PythonHilError("Python builtin is unavailable: {}".format(
            ready.decode("ascii", "replace")
        ))
    if ready.startswith(b"ERROR:"):
        raise PythonHilError(ready.decode("ascii", "replace"))
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

    upload_transport = send_upload_frames(
        session,
        container_path,
        size,
        upload_timeout,
        test_timeout,
        inject_faults=inject_upload_faults,
    )

    upload = session.wait_line_prefix(
        (b"P2PY:UPLOAD:PASS:", b"P2PY:UPLOAD:FAIL:"), test_timeout * 3
    )
    if upload.startswith(b"P2PY:UPLOAD:FAIL:"):
        raise PythonHilError(upload.decode("ascii", "replace"))
    expected_upload = upload_pass_marker(size, crc32)
    if upload != expected_upload:
        raise PythonHilError("target upload PASS marker does not match artifact")
    runtime = session.wait_line_prefix(
        (b"P2PY:RUNTIME:READY:", b"P2PY:UPLOAD:FAIL:"), test_timeout
    )
    if not runtime.startswith(b"P2PY:RUNTIME:READY:"):
        raise PythonHilError(runtime.decode("ascii", "replace"))

    runtime_stages = []
    for expected in RUNTIME_STAGES:
        stage = session.wait_line_prefix(
            (expected, b"Traceback ", b"ERROR:", b"P2PY:UPLOAD:FAIL:"),
            test_timeout,
        )
        if stage != expected:
            raise PythonHilError(
                "CPython runtime stage failed before {}: {}".format(
                    expected.decode("ascii"), stage.decode("ascii", "replace")
                )
            )
        runtime_stages.append(stage.decode("ascii"))

    completed = []
    durations = {}
    stack_samples = []
    failure_prefixes = (
        b"Traceback ",
        b"ERROR:",
        b"P2PY:UPLOAD:FAIL:",
        WORKER_EXIT_PREFIX.encode("ascii"),
    )
    entropy_fingerprint = None
    for index, test in enumerate(PYTHON_TESTS):
        test_started = time.monotonic()
        if index > 0:
            session.write((test.command + "\r").encode("ascii"))
        if test.name == "hardware_entropy":
            fingerprint_line = session.wait_line_prefix(
                (
                    ENTROPY_FINGERPRINT_PREFIX.encode("ascii"),
                    test.marker.encode("ascii"),
                )
                + failure_prefixes,
                test_timeout,
            )
            entropy_fingerprint = parse_entropy_fingerprint(fingerprint_line)

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
        wait_worker_exit(session, test_timeout)
        stack = wait_stack_telemetry(session, test_timeout)
        stack_samples.append({"test": test.name, **stack})
        session.wait_token(b"nsh> ", test_timeout)
        durations[test.name] = time.monotonic() - test_started

    restart = run_restart_stress(session, test_timeout)
    completed.append("restart_stress_20")
    stack_samples.extend(restart["stack_samples"])

    concurrency = run_concurrency_test(session, test_timeout)
    completed.append("concurrency_guard")
    stack_samples.extend(concurrency["stack_samples"])

    return {
        "completed_tests": completed,
        "upload_size": size,
        "upload_crc32": "{:08X}".format(crc32),
        "ready_marker": ready.decode("ascii"),
        "upload_marker": upload.decode("ascii"),
        "runtime_marker": runtime.decode("ascii"),
        "runtime_stages": runtime_stages,
        "shell_setup": shell_setup,
        "upload_transport": upload_transport,
        "concurrency": concurrency,
        "restart_stress": restart,
        "stack_samples": stack_samples,
        "minimum_stack_free": min(sample["free"] for sample in stack_samples),
        "entropy_fingerprint": entropy_fingerprint,
        "test_durations_seconds": durations,
    }


def run_restart_stress(
    session: SerialSession, test_timeout: float
) -> Mapping[str, object]:
    failure_prefixes = (
        b"Traceback ",
        b"ERROR:",
        b"P2PY:UPLOAD:FAIL:",
        WORKER_EXIT_PREFIX.encode("ascii"),
    )
    durations = []
    stack_samples = []
    for iteration in range(RESTART_STRESS_COUNT):
        marker = "P2PYTEST:RESTART:{}:PASS".format(iteration)
        command = (
            "python -c 'import tracemalloc,zlib;tracemalloc.start();"
            "x=bytearray(1024);assert tracemalloc.get_traced_memory()[0]>0;"
            "s=b\"x\"*32769;assert zlib.decompress(zlib.compress(s))==s;"
            "tracemalloc.stop();"
            "print(\"P2PYTEST:RESTART:\"+str({})+\":PASS\")'".format(iteration)
        )
        encoded = (command + "\r").encode("ascii")
        if (
            len(encoded) > LINE_MAX
            or len(encoded) > MAX_UART_WRITE
            or marker in command
        ):
            raise PythonHilError("restart stress command violates the console ABI")
        started = time.monotonic()
        session.write(encoded)
        result = session.wait_line_prefix(
            (marker.encode("ascii"),) + failure_prefixes, test_timeout
        )
        if result != marker.encode("ascii"):
            raise PythonHilError(
                "restart stress iteration {} failed: {}".format(
                    iteration, result.decode("ascii", "replace")
                )
            )
        wait_worker_exit(session, test_timeout)
        stack = wait_stack_telemetry(session, test_timeout)
        stack["test"] = "restart_stress_{}".format(iteration)
        stack_samples.append(stack)
        session.wait_token(b"nsh> ", test_timeout)
        durations.append(time.monotonic() - started)

    return {
        "count": RESTART_STRESS_COUNT,
        "durations_seconds": durations,
        "maximum_seconds": max(durations),
        "stack_samples": stack_samples,
    }


def run_concurrency_test(
    session: SerialSession, test_timeout: float
) -> Mapping[str, object]:
    failure_prefixes = (
        b"Traceback ",
        b"ERROR:",
        b"P2PY:UPLOAD:FAIL:",
        WORKER_EXIT_PREFIX.encode("ascii"),
    )
    holder_marker = CONCURRENCY_HOLDER_MARKER.encode("ascii")
    done_marker = CONCURRENCY_DONE_MARKER.encode("ascii")
    second_marker = CONCURRENCY_SECOND_MARKER.encode("ascii")
    post_marker = CONCURRENCY_POST_MARKER.encode("ascii")
    busy_prefix = CONCURRENCY_BUSY_PREFIX.encode("ascii")

    session.write((CONCURRENCY_HOLDER_COMMAND + "\r").encode("ascii"))
    session.wait_token(b"nsh> ", test_timeout)
    holder = session.wait_line_prefix((holder_marker,) + failure_prefixes, test_timeout)
    if holder != holder_marker:
        raise PythonHilError("background Python holder failed to start")

    session.write((CONCURRENCY_SECOND_COMMAND + "\r").encode("ascii"))
    busy = session.wait_line_prefix(
        (busy_prefix, second_marker) + failure_prefixes, test_timeout
    )
    if busy == second_marker:
        raise PythonHilError("concurrent Python launch was incorrectly admitted")
    if not busy.startswith(busy_prefix):
        raise PythonHilError("concurrent Python launch did not fail with EBUSY")
    try:
        code = int(busy[len(busy_prefix) :])
    except ValueError as exc:
        raise PythonHilError("concurrent Python busy code is malformed") from exc
    if code <= 0:
        raise PythonHilError("concurrent Python busy code is invalid")

    session.wait_token(b"nsh> ", test_timeout)
    done = session.wait_line_prefix((done_marker,) + failure_prefixes, test_timeout)
    if done != done_marker:
        raise PythonHilError("background Python holder did not finish cleanly")

    wait_worker_exit(session, test_timeout)
    holder_stack = wait_stack_telemetry(session, test_timeout)
    session.write((CONCURRENCY_POST_COMMAND + "\r").encode("ascii"))
    post = session.wait_line_prefix((post_marker,) + failure_prefixes, test_timeout)
    if post != post_marker:
        raise PythonHilError("Python did not restart after concurrent contention")
    wait_worker_exit(session, test_timeout)
    post_stack = wait_stack_telemetry(session, test_timeout)
    session.wait_token(b"nsh> ", test_timeout)

    return {
        "holder_marker": holder.decode("ascii"),
        "busy_marker": busy.decode("ascii"),
        "done_marker": done.decode("ascii"),
        "post_marker": post.decode("ascii"),
        "stack_samples": [
            {"test": "concurrency_holder", **holder_stack},
            {"test": "concurrency_post", **post_stack},
        ],
    }


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--baud", type=int, default=RUNTIME_BAUD)
    parser.add_argument("--loader-baud", type=int, default=2000000)
    parser.add_argument("--loadp2", type=pathlib.Path)
    parser.add_argument("--image", required=True, type=pathlib.Path)
    parser.add_argument("--resident-elf", required=True, type=pathlib.Path)
    parser.add_argument("--container", required=True, type=pathlib.Path)
    parser.add_argument("--artifact-dir", required=True, type=pathlib.Path)
    parser.add_argument("--reset-method", choices=("dtr", "rts"), default="dtr")
    parser.add_argument("--load-timeout", type=float, default=60.0)
    parser.add_argument("--boot-timeout", type=float, default=90.0)
    parser.add_argument("--upload-timeout", type=float, default=1800.0)
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
    if args.baud != RUNTIME_BAUD:
        parser.error("--baud must be {} for the P2 runtime UART ABI".format(
            RUNTIME_BAUD
        ))
    args.image = args.image.expanduser().resolve()
    args.resident_elf = args.resident_elf.expanduser().resolve()
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
        "upload_fault_injection": {
            "enabled": True,
            "kinds": list(UPLOAD_FAULT_SEQUENCE),
        },
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
            guard = open_serial(args.serial, args.baud)
            status["serial_handoff_guard"] = "nonreading-shared-descriptor"
            connection = None
            try:
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
                        True,
                    )
                finally:
                    connection.close()
            finally:
                guard.close()

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
        inputs = validate_artifacts(
            args.image, args.resident_elf, args.container
        )
        plan = {
            "format": "p2-python-hil-plan-v1",
            "mode": "execute" if args.execute else "dry-run",
            "serial": args.serial,
            "baud": args.baud,
            "upload_fault_injection": {
                "enabled_on_execute": True,
                "kinds": list(UPLOAD_FAULT_SEQUENCE),
            },
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
