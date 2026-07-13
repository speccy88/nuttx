Storage arbitration
===================

Status: the production target-C arbiter is **HOST-TESTED**.  Onboard flash,
microSD, and 1,000 alternating flash/SD transactions are **HIL-VERIFIED**.
True power-loss recovery remains **BLOCKED** without a power-cycle control.

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
``CONFIG_P2_STORAGE_MAX_FREQUENCY``.  Late initialization keeps the raw W25
MTD private, requires 256-byte read/write blocks, 4-KiB erase blocks, and
4,096 erase blocks, and records the JEDEC identity.  A child MTD created by
``mtd_partition(raw, 2048, 63488)`` exposes exactly
``[0x00080000, 0x01000000)``.  The SMART block driver therefore cannot address
the ``[0x00000000, 0x00080000)`` boot reservation.

The storage profile registers the child as ``/dev/smart0`` without automatic
formatting or mounting.  It also registers ``/dev/mmcsd0`` through the generic
MMC/SD SPI interface.  The verified hardware reported JEDEC ``EF7018``, a
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
