Storage arbitration
===================

Status: TARGET-C HOST-TESTED, HIL-REQUIRED electrically.

The board storage owner serializes P58-P61 with one timed mutex and the states
``IDLE``, ``FLASH_SELECTED``, ``SD_SELECTED``, and ``RECOVERY``.  Flash uses
P60 as clock and P61 as chip select.  microSD uses P60 as chip select and P61
as clock.  No driver may configure these pins independently.

The target C arbiter fails closed on conflicting ownership or a lock timeout,
applies a safe idle state during early board initialization, and requires an
explicit recovery transition after an I/O failure.  A compiled host test runs
the production transition engine in both flash-to-SD and SD-to-flash
directions, including timeout and recovery paths.

The board provides separate polled logical SPI lower halves: W25 flash uses
mode 3 and microSD uses mode 0, with conservative frequencies capped by
``CONFIG_P2_STORAGE_MAX_FREQUENCY``.  Late initialization keeps the raw W25
MTD private, requires the detected part to report 256-byte read/write blocks,
4 KiB erase blocks, and 4096 erase blocks (16 MiB), and records its three-byte
JEDEC identity.  A child MTD created by ``mtd_partition(raw, 2048, 63488)``
exposes exactly ``[0x080000, 0x1000000)``; the SMART block driver therefore
cannot address the ``[0x000000, 0x080000)`` boot reservation.

The storage profile registers that child as ``/dev/smart0`` without
auto-formatting or mounting it.  It also registers the generic microSD block
interface as ``/dev/mmcsd0``.  The board emits exact JEDEC, raw geometry, and
partition-layout markers for HIL evidence.  Flash-media validation, SmartFS
format/mount/persistence, SD-media validation, and all electrical behavior
remain HIL-required.
