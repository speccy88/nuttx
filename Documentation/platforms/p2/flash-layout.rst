Flash layout
============

Status: DRAFTED, HOST-TESTED for validation rules.

The 16 MiB W25-class flash draft reserves 0x000000-0x0fffff as protected boot/image space and 0x100000-0xffffff for SmartFS. ``tools/p2/mkflash.py`` rejects overlap, erase misalignment, flash overflow, boot image overflow, and Hub image overflow. Actual ROM boot format is HIL-REQUIRED.
