"""Canonical P2-EC32MB flash layout and loadp2 erase-range model."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import pathlib
from typing import Iterable


FLASH_SIZE = 0x01000000
PAGE_SIZE = 0x100
ERASE = 0x1000
LARGE_ERASE = 0x10000
ROM_BOOT_WINDOW_SIZE = 0x400
PAYLOAD_OFFSET = 0x90
MIN_PROGRAM_PAGES = ROM_BOOT_WINDOW_SIZE // PAGE_SIZE
HUB_RAM = 0x7C000
LARGE_ERASE_THRESHOLD_PAGES = 64
FLASH_INPUT_FORMAT = "loadp2-single-flash-input-v1"


@dataclasses.dataclass(frozen=True)
class Partition:
    name: str
    offset: int
    size: int
    protected: bool

    @property
    def end(self) -> int:
        return self.offset + self.size


@dataclasses.dataclass(frozen=True)
class FlashPlan:
    image_size: int
    image_padded_size: int
    payload_offset: int
    payload_end: int
    program_end: int
    erase_end: int


def image_manifest(data: bytes) -> dict[str, object]:
    """Return the canonical mkflash manifest for one raw Hub image."""

    plan = image_plan(len(data))
    validate(image_size=len(data))
    return {
        "format": FLASH_INPUT_FORMAT,
        "image_size": len(data),
        "image_sha256": hashlib.sha256(data).hexdigest(),
        "payload_offset": plan.payload_offset,
        "payload_end": plan.payload_end,
        "program_end": plan.program_end,
        "erase_end": plan.erase_end,
    }


def validate_image_manifest(image: pathlib.Path,
                            manifest: pathlib.Path | None = None
                            ) -> dict[str, object]:
    """Validate an adjacent mkflash manifest against its raw image."""

    image = pathlib.Path(image)
    if manifest is None:
        manifest = image.with_suffix(image.suffix + ".json")
    else:
        manifest = pathlib.Path(manifest)

    try:
        data = image.read_bytes()
        observed = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read flash input manifest: {exc}") from exc

    if not isinstance(observed, dict):
        raise ValueError("flash input manifest must contain a JSON object")

    expected = image_manifest(data)
    if set(observed) != set(expected):
        raise ValueError("flash input manifest fields do not match the schema")

    for key, value in expected.items():
        if observed.get(key) != value:
            raise ValueError(
                f"flash input manifest {key} mismatch: "
                f"expected {value!r}, got {observed.get(key)!r}"
            )

    return expected


def align_up(value: int, alignment: int) -> int:
    if value < 0 or alignment <= 0:
        raise ValueError("alignment inputs must be positive")
    return (value + alignment - 1) // alignment * alignment


def loadp2_erase_end(program_pages: int) -> int:
    """Model the pinned loadp2 flash_loader.spin2 erase algorithm."""

    if program_pages <= 0:
        raise ValueError("program page count must be positive")

    remaining = program_pages
    erased = 0
    while remaining:
        if remaining > LARGE_ERASE_THRESHOLD_PAGES:
            block_size = LARGE_ERASE
        else:
            block_size = ERASE

        block_pages = block_size // PAGE_SIZE
        remaining -= min(remaining, block_pages)
        erased += block_size

    return erased


def image_plan(image_size: int) -> FlashPlan:
    if image_size <= 0:
        raise ValueError("image is empty")
    if image_size > HUB_RAM:
        raise ValueError("hub image overflow")
    if image_size % 4 != 0:
        raise ValueError("hub image size must be four-byte aligned")

    padded = image_size
    payload_end = PAYLOAD_OFFSET + padded
    program_pages = max(
        MIN_PROGRAM_PAGES,
        align_up(payload_end, PAGE_SIZE) // PAGE_SIZE,
    )
    program_end = program_pages * PAGE_SIZE
    erase_end = loadp2_erase_end(program_pages)
    return FlashPlan(
        image_size=image_size,
        image_padded_size=padded,
        payload_offset=PAYLOAD_OFFSET,
        payload_end=payload_end,
        program_end=program_end,
        erase_end=erase_end,
    )


BOOT_SIZE = image_plan(HUB_RAM).erase_end
PARTITION_OBJECTS = (
    Partition("boot", 0, BOOT_SIZE, True),
    Partition("smartfs", BOOT_SIZE, FLASH_SIZE - BOOT_SIZE, False),
)
PARTITIONS = tuple(
    (part.name, part.offset, part.size, part.protected)
    for part in PARTITION_OBJECTS
)


def _partitions(parts: Iterable[object]) -> tuple[Partition, ...]:
    result = []
    for part in parts:
        if isinstance(part, Partition):
            result.append(part)
        else:
            name, offset, size, protected = part  # type: ignore[misc]
            result.append(Partition(name, offset, size, bool(protected)))
    return tuple(result)


def validate(parts=PARTITIONS, flash_size=FLASH_SIZE, erase=ERASE,
             image_size=0):
    normalized = sorted(_partitions(parts), key=lambda part: part.offset)
    if not normalized:
        raise ValueError("no flash partitions")

    end = 0
    for part in normalized:
        if part.offset % erase or part.size <= 0 or part.size % erase:
            raise ValueError(f"{part.name}: erase alignment")
        if part.offset < end:
            raise ValueError(f"{part.name}: overlap")
        if part.end > flash_size:
            raise ValueError(f"{part.name}: capacity")
        end = part.end

    boot = [part for part in normalized if part.name == "boot"]
    if len(boot) != 1 or boot[0].offset != 0 or not boot[0].protected:
        raise ValueError("boot partition must be unique, protected, and at zero")
    if image_size:
        plan = image_plan(image_size)
        if plan.erase_end > boot[0].end:
            raise ValueError("image erase range crosses boot partition")
    return True


def layout_data() -> dict[str, object]:
    maximum = image_plan(HUB_RAM)
    return {
        "schema": 2,
        "flash_size": FLASH_SIZE,
        "page_size": PAGE_SIZE,
        "erase_size": ERASE,
        "large_erase_size": LARGE_ERASE,
        "rom_boot_window_size": ROM_BOOT_WINDOW_SIZE,
        "payload_offset": PAYLOAD_OFFSET,
        "minimum_program_pages": MIN_PROGRAM_PAGES,
        "max_image_size": HUB_RAM,
        "max_program_end": maximum.program_end,
        "max_erase_end": maximum.erase_end,
        "partitions": [dataclasses.asdict(part) for part in PARTITION_OBJECTS],
    }


def render_json() -> str:
    return json.dumps(layout_data(), indent=2, sort_keys=True) + "\n"


def render_header() -> str:
    boot, smartfs = PARTITION_OBJECTS
    maximum = image_plan(HUB_RAM)
    return f"""/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/include/board_flash_layout.h
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.  The
 * ASF licenses this file to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance with the
 * License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
 * WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the
 * License for the specific language governing permissions and limitations
 * under the License.
 *
 ****************************************************************************/

/* Generated by tools/p2/generate-flash-layout.py. */

#ifndef __BOARDS_P2_P2X8C4M64P_P2_EC32MB_INCLUDE_FLASH_LAYOUT_H
#define __BOARDS_P2_P2X8C4M64P_P2_EC32MB_INCLUDE_FLASH_LAYOUT_H

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_FLASH_SIZE_BYTES       0x{FLASH_SIZE:08x}u
#define P2_FLASH_PAGE_BYTES       0x{PAGE_SIZE:08x}u
#define P2_FLASH_ERASE_BYTES      0x{ERASE:08x}u
#define P2_FLASH_LARGE_ERASE_BYTES 0x{LARGE_ERASE:08x}u
#define P2_FLASH_ROM_BOOT_WINDOW_BYTES 0x{ROM_BOOT_WINDOW_SIZE:08x}u
#define P2_FLASH_PAYLOAD_OFFSET   0x{PAYLOAD_OFFSET:08x}u
#define P2_FLASH_MIN_PROGRAM_PAGES 0x{MIN_PROGRAM_PAGES:08x}u
#define P2_FLASH_BOOT_IMAGE_MAX   0x{HUB_RAM:08x}u
#define P2_FLASH_MAX_PROGRAM_END  0x{maximum.program_end:08x}u
#define P2_FLASH_BOOT_OFFSET      0x{boot.offset:08x}u
#define P2_FLASH_BOOT_SIZE        0x{boot.size:08x}u
#define P2_FLASH_FS_OFFSET        0x{smartfs.offset:08x}u
#define P2_FLASH_FS_SIZE          0x{smartfs.size:08x}u

#endif
"""


def render_rst() -> str:
    boot, smartfs = PARTITION_OBJECTS
    maximum = image_plan(HUB_RAM)
    return f"""Flash layout
============

Status: TARGET-ENFORCED logical layout; HIL-REQUIRED media validation, flash
boot, and filesystem persistence.

This file is generated by ``tools/p2/generate-flash-layout.py`` from
``tools/p2/lib/flash_layout.py``.  The model reserves the 16 MiB W25-class
flash as follows:

* ``0x{boot.offset:06x}-0x{boot.end - 1:06x}``: logical boot reservation
  ({boot.size // 1024} KiB).  The raw MTD and boot reservation are private and
  have no writable device node.
* ``0x{smartfs.offset:06x}-0x{smartfs.end - 1:06x}``: logical filesystem
  reservation ({smartfs.size / (1024 * 1024):g} MiB).  The board passes only
  ``mtd_partition(raw, 2048, 63488)`` to ``smart_initialize(0, ...)``, which
  registers ``/dev/smart0`` without automatically formatting or mounting it.

The boundary follows the pinned ``loadp2 -SINGLE -FLASH`` implementation.
The ROM reads a 1 KiB boot window, but loadp2 places the first application byte
at ``0x{PAYLOAD_OFFSET:06x}`` inside that window and programs at least four
256-byte pages.  The P2 port permits at most ``0x{HUB_RAM:x}`` image bytes, so
programmed data ends by ``0x{maximum.program_end:06x}``.  Because loadp2 uses
a 64 KiB erase whenever more than 64 program pages remain, the maximum erase
range is ``[0x000000, 0x{maximum.erase_end:06x})``.

``tools/p2/verify-flash-layout.py`` checks generated-file drift and prints
exact program/erase ranges for an input.  ``tools/p2/flash.sh`` requires an
explicit port, image, ``--execute``, ``P2_HIL=1``, and
``P2_ALLOW_FLASH_WRITE=1`` before invoking the pinned loader.  It also requires
``P2_ALLOW_SD_WRITE=1`` because the flash loader drives shared P60/P61 in a way
that can select and clock an installed microSD card.  The target requires
256-byte blocks, 4 KiB erase blocks, 4096 erase blocks, and capacity ID
``0x18`` before registering the private child.  This is software containment
through the MTD partition, not unsupported W25 hardware locking.  JEDEC
identity, formatting boundaries, boot-region integrity, persistence, and
independent flash boot remain HIL-required.
"""


def generated_files(root: pathlib.Path) -> dict[pathlib.Path, str]:
    return {
        root / "boards/p2/p2x8c4m64p/p2-ec32mb/include/board_flash_layout.h":
            render_header(),
        root / "Documentation/platforms/p2/flash-layout.rst": render_rst(),
    }
