#!/usr/bin/env python3
"""Shared host-side protocol helpers for the NuttX ``p2recv`` command."""

from __future__ import annotations

import binascii
import os
import pathlib
import re
import struct
from dataclasses import dataclass
from typing import BinaryIO


FRAME_MAGIC = b"P2RF"
FRAME_HEADER = struct.Struct("<4sIII")
CHUNK_MAX = 256
TARGET_FILE_MAX = 0x7FFFFFFF
NSH_COMMAND_MAX = 255

_SAFE_DESTINATION = re.compile(r"^/mnt/[A-Za-z0-9._/-]+$")
_READY = re.compile(
    r"^P2RECV:READY:SIZE=([0-9]+):CRC32=([0-9A-F]{8}):"
    r"CHUNK_MAX=([0-9]+):PATH=(/mnt/[A-Za-z0-9._/-]+)$"
)


class ProtocolValueError(ValueError):
    """A local value cannot be represented safely by the target protocol."""


@dataclass(frozen=True)
class FileManifest:
    path: pathlib.Path
    size: int
    crc32: int
    fingerprint: tuple[int, int, int, int]


@dataclass(frozen=True)
class ReadyStatus:
    size: int
    crc32: int
    chunk_max: int
    destination: str


def file_fingerprint(status: os.stat_result) -> tuple[int, int, int, int]:
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)


def crc32_stream(stream: BinaryIO, block_size: int = 65536) -> int:
    if block_size <= 0:
        raise ProtocolValueError("CRC block size must be greater than zero")

    value = 0
    while True:
        block = stream.read(block_size)
        if not block:
            return value & 0xFFFFFFFF
        value = binascii.crc32(block, value)


def inspect_file(path: os.PathLike[str] | str) -> FileManifest:
    resolved = pathlib.Path(path).expanduser().resolve()
    before = resolved.stat()
    if not resolved.is_file():
        raise ProtocolValueError("source is not a regular file: {}".format(resolved))
    if before.st_size > TARGET_FILE_MAX:
        raise ProtocolValueError(
            "source is too large for p2recv: {} > {}".format(
                before.st_size, TARGET_FILE_MAX
            )
        )

    with resolved.open("rb") as stream:
        checksum = crc32_stream(stream)

    after = resolved.stat()
    if file_fingerprint(before) != file_fingerprint(after):
        raise ProtocolValueError("source changed while its CRC-32 was calculated")

    return FileManifest(
        path=resolved,
        size=before.st_size,
        crc32=checksum,
        fingerprint=file_fingerprint(before),
    )


def validate_destination(destination: str) -> str:
    if not _SAFE_DESTINATION.fullmatch(destination):
        raise ProtocolValueError(
            "destination must be a simple absolute path below /mnt/"
        )

    components = destination.split("/")[1:]
    if any(component in ("", ".", "..") for component in components):
        raise ProtocolValueError("destination contains an unsafe path component")
    if destination.endswith("/"):
        raise ProtocolValueError("destination must name a file")
    return destination


def make_command(manifest: FileManifest, destination: str, force: bool) -> bytes:
    destination = validate_destination(destination)
    command = "p2recv {}{} {} {:08X}".format(
        "-f " if force else "", destination, manifest.size, manifest.crc32
    ).encode("ascii")
    if len(command) > NSH_COMMAND_MAX:
        raise ProtocolValueError(
            "p2recv command is {} bytes; NSH limit is {}".format(
                len(command), NSH_COMMAND_MAX
            )
        )
    return command


def encode_frame(sequence: int, payload: bytes) -> bytes:
    if not 0 <= sequence <= 0xFFFFFFFF:
        raise ProtocolValueError("frame sequence is outside uint32 range")
    if not 1 <= len(payload) <= CHUNK_MAX:
        raise ProtocolValueError(
            "frame payload must contain 1..{} bytes".format(CHUNK_MAX)
        )
    checksum = binascii.crc32(payload) & 0xFFFFFFFF
    return FRAME_HEADER.pack(FRAME_MAGIC, sequence, len(payload), checksum) + payload


def ready_marker(manifest: FileManifest, destination: str) -> str:
    return (
        "P2RECV:READY:SIZE={}:CRC32={:08X}:CHUNK_MAX={}:PATH={}".format(
            manifest.size, manifest.crc32, CHUNK_MAX, destination
        )
    )


def chunk_marker(sequence: int, received: int) -> str:
    return "P2RECV:CHUNK:SEQ={}:BYTES={}".format(sequence, received)


def ack_marker(manifest: FileManifest, destination: str) -> str:
    return "P2RECV:ACK:BYTES={}:CRC32={:08X}:PATH={}".format(
        manifest.size, manifest.crc32, destination
    )


def parse_ready(status: str) -> ReadyStatus:
    match = _READY.fullmatch(status)
    if match is None:
        raise ProtocolValueError("malformed READY status: {!r}".format(status))
    return ReadyStatus(
        size=int(match.group(1), 10),
        crc32=int(match.group(2), 16),
        chunk_max=int(match.group(3), 10),
        destination=match.group(4),
    )
