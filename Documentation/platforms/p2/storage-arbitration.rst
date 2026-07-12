Storage arbitration
===================

Status: DRAFTED, HOST-TESTED for host state model, HIL-REQUIRED electrically.

A board owner serializes P58-P61 with states IDLE, FLASH_SELECTED, SD_SELECTED, and RECOVERY. Flash uses P60 as clock and P61 as chip select. microSD uses P60 as chip select and P61 as clock. No driver may configure these pins independently.
