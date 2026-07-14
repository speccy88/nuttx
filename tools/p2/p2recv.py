#!/usr/bin/env python3
"""Provision a file through the NuttX P2 serial console.

The command is a dry run unless ``--execute`` is supplied and ``P2_HIL=1``
is present.  The receiver writes a temporary file, verifies length and CRC-32,
and only then renames it into place on the mounted target filesystem.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import pathlib
import sys
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence, TextIO

import monitor
import p2recv_protocol as protocol


EXIT_OK = 0
EXIT_SAFETY = 2
EXIT_PROTOCOL = 3
EXIT_SERIAL = 8
EXIT_LOCK_BUSY = 9
EXIT_INTERRUPTED = 130


class TransferError(RuntimeError):
    """The serial transfer did not complete and commit."""


class TargetError(TransferError):
    """The target explicitly rejected or failed the transfer."""


@dataclass(frozen=True)
class TransferConfig:
    port: str
    baud: int = 230400
    chunk_size: int = protocol.CHUNK_MAX
    timeout: float = 10.0
    read_timeout: float = 0.1
    write_timeout: float = 2.0
    command_pace: float = 0.002
    console_sync_timeout: float = 45.0

    def validate(self) -> None:
        if not self.port:
            raise protocol.ProtocolValueError("a serial port is required")
        if self.baud <= 0:
            raise protocol.ProtocolValueError("baud must be greater than zero")
        if not 1 <= self.chunk_size <= protocol.CHUNK_MAX:
            raise protocol.ProtocolValueError(
                "chunk size must be between 1 and {}".format(protocol.CHUNK_MAX)
            )
        if self.timeout <= 0:
            raise protocol.ProtocolValueError(
                "status timeout must be greater than zero"
            )
        if self.read_timeout <= 0 or self.write_timeout <= 0:
            raise protocol.ProtocolValueError(
                "serial timeouts must be greater than zero"
            )
        if self.command_pace < 0:
            raise protocol.ProtocolValueError(
                "command pace must not be negative"
            )
        if self.console_sync_timeout < 0:
            raise protocol.ProtocolValueError(
                "console sync timeout must not be negative"
            )


@dataclass(frozen=True)
class TransferResult:
    bytes_sent: int
    chunks_sent: int
    crc32: int


class StatusReader:
    """Extract P2RECV status lines from a byte-oriented serial stream."""

    def __init__(
        self,
        connection,
        timeout: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.connection = connection
        self.timeout = timeout
        self.monotonic = monotonic
        self.buffer = bytearray()

    def expect(self, expected: str) -> None:
        deadline = self.monotonic() + self.timeout
        while True:
            status = self._next_status(deadline)
            if status.startswith("P2RECV:ERROR:"):
                raise TargetError(status)
            if status != expected:
                raise TransferError(
                    "unexpected target status {!r}; expected {!r}".format(
                        status, expected
                    )
                )
            return

    def _next_status(self, deadline: float) -> str:
        while True:
            line = self._extract_line()
            if line is not None:
                marker = line.find(b"P2RECV:")
                if marker >= 0:
                    return line[marker:].decode("ascii", errors="replace").strip()
                continue

            if self.monotonic() >= deadline:
                raise TransferError("timed out waiting for target status")

            data = self.connection.read(256)
            if data is None:
                raise TransferError("serial read returned None")
            if not isinstance(data, (bytes, bytearray)):
                raise TransferError("serial read returned a non-byte value")
            if data:
                self.buffer.extend(data)
                if len(self.buffer) > 8192:
                    marker = self.buffer.rfind(b"P2RECV:")
                    if marker < 0:
                        del self.buffer[:-1024]
                    elif marker > 0:
                        del self.buffer[:marker]

    def _extract_line(self) -> Optional[bytes]:
        positions = [
            position
            for separator in (b"\r", b"\n")
            if (position := self.buffer.find(separator)) >= 0
        ]
        if not positions:
            return None

        end = min(positions)
        line = bytes(self.buffer[:end])
        consumed = end
        while consumed < len(self.buffer) and self.buffer[consumed] in (10, 13):
            consumed += 1
        del self.buffer[:consumed]
        return line


class FileSender:
    def __init__(
        self,
        connection,
        config: TransferConfig,
        output: Optional[TextIO] = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.connection = connection
        self.config = config
        self.output = output
        self.status = StatusReader(connection, config.timeout, monotonic)

    def send(
        self,
        manifest: protocol.FileManifest,
        destination: str,
        force: bool = False,
    ) -> TransferResult:
        destination = protocol.validate_destination(destination)
        command = protocol.make_command(manifest, destination, force)

        reset_input = getattr(self.connection, "reset_input_buffer", None)
        if reset_input is not None:
            reset_input()

        self._write_command(command + b"\r")
        expected_ready = protocol.ready_marker(manifest, destination)
        self.status.expect(expected_ready)
        self._event("READY", "bytes={} crc32={:08X}".format(
            manifest.size, manifest.crc32
        ))

        sent = 0
        sequence = 0
        with manifest.path.open("rb") as stream:
            opened = stream.fileno()
            if protocol.file_fingerprint(os.fstat(opened)) != manifest.fingerprint:
                raise TransferError("source changed before transfer started")

            while sent < manifest.size:
                payload = stream.read(min(self.config.chunk_size, manifest.size - sent))
                if not payload:
                    raise TransferError("source ended before its recorded size")
                self._write_all(protocol.encode_frame(sequence, payload))
                self.connection.flush()
                sent += len(payload)
                self.status.expect(protocol.chunk_marker(sequence, sent))
                sequence += 1

            if stream.read(1):
                raise TransferError("source grew while it was transferred")
            if protocol.file_fingerprint(os.fstat(opened)) != manifest.fingerprint:
                raise TransferError("source changed while it was transferred")

        self.status.expect(protocol.ack_marker(manifest, destination))
        self._event("ACK", "bytes={} chunks={} crc32={:08X}".format(
            sent, sequence, manifest.crc32
        ))
        return TransferResult(sent, sequence, manifest.crc32)

    def _write_all(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = self.connection.write(view)
            if written is None or written <= 0 or written > len(view):
                raise TransferError(
                    "invalid serial write result {!r} for {} bytes".format(
                        written, len(view)
                    )
                )
            view = view[written:]

    def _write_command(self, command: bytes) -> None:
        """Pace NSH input so line-editor redraws cannot overrun P2 UART RX."""

        for byte in command:
            self._write_all(bytes((byte,)))
            self.connection.flush()
            if self.config.command_pace > 0:
                time.sleep(self.config.command_pace)

    def _event(self, name: str, details: str) -> None:
        if self.output is not None:
            print("P2RECV:HOST:{}:{}".format(name, details), file=self.output)


def wait_for_console_prompt(
    connection,
    timeout: float,
    output: Optional[TextIO] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Wait through a possible open-triggered reboot before sending p2recv."""

    if timeout == 0:
        return

    deadline = monotonic() + timeout
    next_probe = monotonic() + 0.25
    buffer = bytearray()

    while monotonic() < deadline:
        data = connection.read(256)
        if data is None:
            raise TransferError("serial read returned None during console sync")
        if not isinstance(data, (bytes, bytearray)):
            raise TransferError(
                "serial read returned a non-byte value during console sync"
            )
        if data:
            buffer.extend(data)
            if b"nsh>" in buffer:
                if output is not None:
                    print("P2RECV:HOST:CONSOLE=READY", file=output)
                return
            if len(buffer) > 8192:
                del buffer[:-4096]

        now = monotonic()
        if now >= next_probe:
            written = connection.write(b"\r")
            if written != 1:
                raise TransferError(
                    "invalid serial write result {!r} during console sync".format(
                        written
                    )
                )
            connection.flush()
            next_probe = now + 0.5

    raise TransferError("timed out waiting for NuttShell console prompt")


def _open_serial(
    factory, config: TransferConfig, preconfigure_control_lines: bool = False
):
    arguments = dict(
        port=config.port,
        baudrate=config.baud,
        timeout=config.read_timeout,
        write_timeout=config.write_timeout,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
        exclusive=True,
    )
    if preconfigure_control_lines:
        arguments["port"] = None
        try:
            connection = factory(**arguments)
        except TypeError as error:
            if "exclusive" not in str(error):
                raise
            arguments.pop("exclusive")
            connection = factory(**arguments)

        try:
            connection.dtr = False
            connection.rts = False
            connection.port = config.port
            connection.open()
        except Exception:
            with contextlib.suppress(Exception):
                connection.close()
            raise

        return connection

    try:
        return factory(**arguments)
    except TypeError as error:
        if "exclusive" not in str(error):
            raise
        arguments.pop("exclusive")
        return factory(**arguments)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="provision a file through the NuttX P2 serial console",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("source", help="host file to send")
    parser.add_argument("destination", help="target path below /mnt/")
    parser.add_argument("--force", action="store_true", help="replace an existing file")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="open the serial port (also requires P2_HIL=1)",
    )
    parser.add_argument("--port", default=os.getenv("P2_PORT", ""))
    parser.add_argument(
        "--baud", type=int, default=int(os.getenv("P2_CONSOLE_BAUD", "230400"))
    )
    parser.add_argument("--chunk-size", type=int, default=protocol.CHUNK_MAX)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--read-timeout", type=float, default=0.1)
    parser.add_argument("--write-timeout", type=float, default=2.0)
    parser.add_argument(
        "--command-pace",
        type=float,
        default=float(os.getenv("P2_RECV_COMMAND_PACE", "0.002")),
        help="seconds between NSH command bytes (payload frames are unpaced)",
    )
    parser.add_argument(
        "--console-sync-timeout",
        type=float,
        default=float(os.getenv("P2_RECV_CONSOLE_SYNC_TIMEOUT", "45")),
        help="seconds to wait for an nsh prompt after opening serial; 0 disables sync",
    )
    parser.add_argument(
        "--lock-file",
        default=os.getenv("P2_LOCK_FILE") or str(monitor.DEFAULT_LOCK_FILE),
    )
    parser.add_argument("--lock-timeout", type=float, default=0.0)
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    serial_factory: Optional[Callable[..., object]] = None,
    serial_exceptions: Optional[Iterable[type]] = None,
    output: TextIO = sys.stdout,
    diagnostics: TextIO = sys.stderr,
) -> int:
    args = build_parser().parse_args(argv)

    try:
        manifest = protocol.inspect_file(args.source)
        destination = protocol.validate_destination(args.destination)
        command = protocol.make_command(manifest, destination, args.force)
    except (OSError, protocol.ProtocolValueError) as error:
        print("CONFIGURATION ERROR: {}".format(error), file=diagnostics)
        return EXIT_SAFETY

    print(
        "P2RECV:PLAN:SOURCE={}:BYTES={}:CRC32={:08X}:DEST={}".format(
            manifest.path, manifest.size, manifest.crc32, destination
        ),
        file=output,
    )
    print("P2RECV:PLAN:COMMAND={}".format(command.decode("ascii")), file=output)

    if not args.execute:
        print(
            "DRY-RUN: serial port was not opened; pass --execute with P2_HIL=1",
            file=output,
        )
        return EXIT_OK
    if os.getenv("P2_HIL", "0") != "1":
        print("HIL REQUIRED: set P2_HIL=1 before --execute", file=diagnostics)
        return EXIT_SAFETY

    config = TransferConfig(
        port=args.port,
        baud=args.baud,
        chunk_size=args.chunk_size,
        timeout=args.timeout,
        read_timeout=args.read_timeout,
        write_timeout=args.write_timeout,
        command_pace=args.command_pace,
        console_sync_timeout=args.console_sync_timeout,
    )
    try:
        config.validate()
        lock = monitor.BoardLock(
            pathlib.Path(args.lock_file).expanduser().resolve(),
            timeout=args.lock_timeout,
        )
    except (OSError, protocol.ProtocolValueError, monitor.ConfigurationError) as error:
        print("CONFIGURATION ERROR: {}".format(error), file=diagnostics)
        return EXIT_SAFETY

    preconfigure_control_lines = serial_factory is None
    if serial_factory is None:
        try:
            import serial
        except ImportError:
            print(
                "CONFIGURATION ERROR: pyserial is required; "
                "install tools/p2/requirements-hil.txt",
                file=diagnostics,
            )
            return EXIT_SAFETY
        serial_factory = serial.Serial
        serial_exceptions = (serial.SerialException, OSError)
    elif serial_exceptions is None:
        serial_exceptions = (OSError,)

    connection = None
    try:
        with lock:
            connection = _open_serial(
                serial_factory,
                config,
                preconfigure_control_lines=preconfigure_control_lines,
            )
            wait_for_console_prompt(
                connection,
                config.console_sync_timeout,
                output=output,
            )
            result = FileSender(connection, config, output=output).send(
                manifest, destination, force=args.force
            )
            print(
                "P2RECV:PASS:BYTES={}:CHUNKS={}:CRC32={:08X}:DEST={}".format(
                    result.bytes_sent,
                    result.chunks_sent,
                    result.crc32,
                    destination,
                ),
                file=output,
            )
            return EXIT_OK
    except monitor.LockBusyError as error:
        print("LOCK BUSY: {}".format(error), file=diagnostics)
        return EXIT_LOCK_BUSY
    except TargetError as error:
        print("TARGET ERROR: {}".format(error), file=diagnostics)
        return EXIT_PROTOCOL
    except TransferError as error:
        print("PROTOCOL ERROR: {}".format(error), file=diagnostics)
        return EXIT_PROTOCOL
    except tuple(serial_exceptions) as error:
        print("SERIAL ERROR: {}".format(monitor.safe_error(error)), file=diagnostics)
        return EXIT_SERIAL
    except KeyboardInterrupt:
        print("INTERRUPTED", file=diagnostics)
        return EXIT_INTERRUPTED
    finally:
        if connection is not None:
            with contextlib.suppress(Exception):
                connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
