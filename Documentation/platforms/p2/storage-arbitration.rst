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
``CONFIG_P2_STORAGE_MAX_FREQUENCY``.  Late initialization keeps the W25 MTD
private and registers the generic microSD block interface as ``/dev/mmcsd0``.
Flash partition enforcement, SmartFS registration, SD-media validation, and
all electrical behavior remain HIL-required.
