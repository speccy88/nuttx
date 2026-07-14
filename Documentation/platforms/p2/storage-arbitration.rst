Storage arbitration
===================

Status: the production target-C arbiter is **HOST-TESTED**.  Onboard flash,
microSD, and 1,000 alternating flash/SD transactions are **HIL-VERIFIED**.
The P2-EC32MB Rev B exact current-candidate SD write and raw-card inspection
and its no-loader SD-only ROM reset are **HIL-VERIFIED**.  The complete
development-image SD path remains useful historical evidence.  The exact
P2-EC Rev D release passed RAM storage probing, reset-only SPI-flash boot, and
host-installed SD-only ROM boot.  True power-loss recovery remains **BLOCKED** without
a power-cycle control.

The board storage owner serializes P58-P61 with one timed mutex and the states
``IDLE``, ``FLASH_SELECTED``, ``SD_SELECTED``, and ``RECOVERY``.  Flash uses
P60 as clock and P61 as chip select.  microSD swaps those two roles, using P60
as chip select and P61 as clock.  No driver may configure these pins
independently.

The arbiter fails closed on conflicting ownership or a lock timeout, applies a
safe idle state during early board initialization, and requires an explicit
recovery transition after an I/O failure.  Host tests compile and execute the
production state machine in flash-to-SD and SD-to-flash directions, including
timeout and recovery paths.

The board exposes separate polled logical SPI lower halves.  W25 flash uses
mode 3 and microSD uses mode 0, with frequencies capped by
``CONFIG_P2_STORAGE_MAX_FREQUENCY``.  Late initialization reads and validates
the W25 JEDEC identity before entering the generic W25 driver.  An invalid
identity returns ``-ENODEV`` immediately; this avoids the generic driver's
write-completion loop when the P2 Edge ``FLASH`` switch has disconnected the
device and MISO reads as all ones.

With ``FLASH`` on and a supported W25 identity, late initialization keeps the
raw W25 MTD private, requires 256-byte read/write blocks, 4-KiB erase blocks,
and 4,096 erase blocks.  A child MTD created by
``mtd_partition(raw, 2048, 63488)`` exposes exactly
``[0x00080000, 0x01000000)``.  The SMART block driver therefore cannot address
the ``[0x00000000, 0x00080000)`` boot reservation.

The storage profile registers the child as ``/dev/smart0`` without automatic
formatting or mounting.  It also registers ``/dev/mmcsd0`` through the generic
MMC/SD SPI interface.  With ``FLASH`` off for SD-only ROM boot, an invalid
JEDEC result is expected: ``/dev/smart0`` is intentionally absent, startup
does not attempt its SmartFS mount, and MMC/SD initialization continues to
``/dev/mmcsd0``.  The verified flash-on hardware reported JEDEC ``EF7018``, a
400-kHz probe frequency, a 2-MHz active transfer frequency, and an unchanged
boot-region CRC32 ``EE5B9C97`` throughout the destructive campaigns.

HIL evidence
------------

``artifacts/hil/20260713T063712.505220Z-flashfs`` passed all eight actions:
probe, explicit format, 1-MiB write and reset persistence with FNV-1a
``693C9DC5``, 16 rewrite cycles, fill to 15,028,224 bytes with ``ENOSPC``, and
interrupted-write recovery.  The interruption was a controlled DTR reset, not
removal of board power, so it must not be described as a true power-loss test.

``artifacts/hil/20260713T083209.592794Z-sd`` passed all seven actions:
probe, explicit format, 1-MiB write and reset persistence with FNV-1a
``BE5C9DC5``, rename/delete, 64 stress iterations, and 1,000 alternating
flash/SD transactions.  Every action began with a fresh RAM load and target
reset; automatic formatting remained disabled.

The subsequent development image was 402,060 bytes with SHA-256
``e1226636846386e5538e731b0fa568ca99fffeb6f992bc6e271f5b5c86e5b3cf``.
The read-only target action ``p2storage sd-rom-verify`` passed against that
written card in
``/private/tmp/p2-release-final.oBc9V4/ec32mb-sd-rom-inspect-w25-guard``.
It validated the MBR partition, FAT32 VBR and FSInfo, root-directory entry,
contiguous FAT chain, end-of-chain marker, sector coverage, and exact raw
image bytes.

Then
``/private/tmp/p2-release-final.oBc9V4/ec32mb-sd-rom-boot-w25-guard`` passed
after the user physically selected SD-only
``(FLASH,up,down)=(OFF,OFF,ON)``.  The verifier issued one DTR reset and
transmitted zero serial bytes: no loader was downloaded.  The ROM image
reached the ordered P2 boot markers, reported W25 unavailable, continued to
``/dev/mmcsd0``, skipped the absent ``/dev/smart0`` mount, emitted the
selected-board showcase marker, and reached the first NSH prompt.  This is
physical Rev-B development evidence for the layout and W25-off fix; it is not
the identity of the current release candidate.

Current release candidate
-------------------------

The current candidate is bound to NuttX
``14cadad3a6794e10cbc9f0dfb20f352e4844d35f`` and apps
``a333035462f545056e7a2fb859a9fbdc6d4ef831``.  Its P2-EC32MB Rev B raw image
is 402,452 bytes with SHA-256
``6ff205df0f724eab91eb0619b53cffc579819cdcb99049578a9f01cb4ba519e2``;
the matching 494,808-byte ELF has SHA-256
``1409460f5399e267516e6ea394d99cf2b30e638ac55cbc82318175712c01dd3c``.
RAM showcase HIL is **PASS** at
``/tmp/p2-release-final.14cadad-r1/ec32mb-showcase-hil``, and programming
this exact image to serial flash is **PASS** at
``/tmp/p2-release-final.14cadad-r1/ec32mb-flash-program``.  Programming
covered ``[0x00000000,0x00062500)`` after erasing
``[0x00000000,0x00063000)``.  Exact-image reset-only flash boot is **PASS**
from cycles 1--10 under
``/tmp/p2-release-final.14cadad-r1/ec32mb-flashboot-hil``.  Every completed
cycle produced stable CRC ``B31D0271``, zero pre-prompt TX, and verified
persistent sequence ``F23A0713`` / one-MiB FNV-1a ``693C9DC5``.  The
originally requested 20-cycle wrapper was intentionally stopped after ten
redundant PASS results to keep testing proportional.  Cycle 11 and the
top-level ``status.json`` report manual interruption/FAIL and are not claimed
as PASS artifacts.

The first exact-candidate write diagnostic at
``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-write`` timed out.  The subsequent
read-only diagnostic at
``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-inspect`` correctly reported
``P2STORAGE:SD:ROM-FAIL:STAGE=MBR:REASON=FIELDS``.  These are retained
diagnostics, not PASS evidence and not evidence of a candidate-image defect.

The corrective destructive format is **PASS** at
``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-format-final``.  It completed in
302.761617 seconds and produced an MBR type-``0x0c`` partition starting at LBA
2048 with 61,130,752 sectors.  Its ``status.json`` SHA-256 is
``df72f9b37775b00545edee943ad3054ba7a1233a8657f3a957e5e217a4a1126c``.
The exact candidate write is **PASS** at
``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-write-final``: the source is
402,452 bytes with SHA-256
``6ff205df0f724eab91eb0619b53cffc579819cdcb99049578a9f01cb4ba519e2``;
the ``LE32(size) + image + zero padding`` staging payload is 402,456 bytes
with SHA-256
``f7ee30fde6ce7a69b63a5c837d9c28e380df75af7ce6e76bd3ded2336c1e5bbf``;
and ``status.json`` has SHA-256
``622f56c19d3455f895a3fd5623d8f866740d94f47c68379df91bec51c546a21a``.

The independent read-only raw-card inspection is **PASS** at
``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-inspect-final`` in 38.236762
seconds.  It validated the MBR, FAT32 VBR, FSInfo, root entry, a contiguous
25-cluster chain ending at ``0x0fffffff``, and the exact 402,452 image bytes
with FNV-1a ``D0D0F215``.  Its ``status.json`` SHA-256 is
``f5345c72e956425c894549b09148180774368c102986a0bfa3305cd23e01c1e0``.
The independent no-loader SD-only reset is **PASS** at
``/tmp/p2-release-final.14cadad-r1/ec32mb-sdboot-hil``.  At the physically
confirmed ``(FLASH,up,down)=(OFF,OFF,ON)`` setting, it booted the exact
402,452-byte image in 16.688852 seconds, downloaded no loader, transmitted
zero serial bytes, and reached the ordered entry/data/BSS/NuttX, W25-off,
MMC/SD-frequency, ``/dev/mmcsd0``, SmartFS-unavailable, selected-board
showcase, and first-NSH-prompt markers.  Fragmentation verification was true.
The SD-write ``status.json`` SHA-256 bound into this proof is
``622f56c19d3455f895a3fd5623d8f866740d94f47c68379df91bec51c546a21a``;
the SD-boot ``status.json`` SHA-256 is
``61534212bd8bcf9f4ca996d36731c0e612951d7d9554c96ff360aaf607a3e758``.
The evidence is included in the host-verified package and public
``p2-edge-flat-up-v0.1.0`` release.  All 20 assets were downloaded once from
the pre-publication draft to a fresh directory, matched the local package byte
for byte, passed all 19 recorded checksums, and passed the bundled verifier
after the standalone-download executable mode bits were restored.

The P2-EC Rev D build has no PSRAM.  Its 386,752-byte raw image has SHA-256
``596b0f022c28fa4462a6e13692ad54ecab095f17d6532d441e60e0dee481c230``
and its 476,768-byte ELF has SHA-256
``2d1e4f2d84455b6cd15edc31571796d9cc1505fae49de7f65245856b70f5bea7``.
Rev D physical evidence is **PASS** for RAM storage probe, exact-image SPI
flash reset boot, and exact-image SD-only ROM boot.  The bundled serial SD
writer timed out on the attached card; the accepted SD proof used a verified
macOS host copy of the board-specific ``_BOOT_P2.BIX``.

The required candidate evidence is preserved in the local release evidence
archive.  Its ``/tmp`` location remains provisional until the exact asset is
uploaded and verified from a fresh GitHub download.
