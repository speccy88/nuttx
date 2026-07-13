Memory map
==========

Status: **STATICALLY-VERIFIED** by the linker and ELF verifier, with startup
and normal Hub-memory execution **HIL-VERIFIED**.

The P2 has 512 KiB of Hub RAM, but the pinned ``loadp2`` RAM loader initializes
only ``[0x00000000, 0x0007c000)``.  The linker therefore excludes the top
16 KiB, ``[0x0007c000, 0x00080000)``, from the load image, stacks, and heap.

.. list-table:: Fixed loader and execution regions
   :header-rows: 1

   * - Hub address
     - Purpose
   * - ``0x00000000``
     - ROM entry; a COGEXEC jump to cog address ``0x10``
   * - ``0x00000004``-``0x0000003f``
     - Loader metadata reservation
   * - ``0x00000014``
     - Loader clock-frequency word
   * - ``0x00000018``
     - Loader clock-mode word
   * - ``0x0000001c``
     - Loader baud word
   * - ``0x00000040``
     - COGEXEC bootstrap which restarts cog 0 in HUBEXEC mode
   * - ``0x00000200``
     - Start of the fixed p2llvm LUT image window
   * - ``0x00000a00``
     - Start of ordinary Hub text
   * - ``0x0007c000``
     - Exclusive end of the usable loader/runtime Hub window

Text, read-only data, initialized data, BSS, the CPU0 idle/TLS reservation,
the upward-growing initial stack, and the heap follow in that order.  The
idle/TLS reservation and live initial stack are each 4 KiB.  Their exact
addresses after ``0x0a00`` vary with the selected configuration and must be
read from ``nuttx.map`` rather than copied between builds.

The linker assertions enforce every fixed address and all region ordering;
``tools/p2/verify-elf.py`` independently checks the linked image and loader
segments.  The 100-cycle NuttX bring-up campaign at
``artifacts/hil/20260713T034525.287219Z-bringup`` verified entry, initialized
data, BSS, heap setup, timer startup, and scheduler handoff on hardware.

External 32-MiB PSRAM is explicitly serviced storage.  It is not part of Hub
RAM and cannot hold NuttX code, C objects, heap allocations, or task stacks;
see :doc:`psram-service`.
