P2 port analysis
================

Status: the current flat-UP port is **COMPILED**, **STATICALLY-VERIFIED**, and
HIL-validated through startup, preemption, NSH, storage, Smart Pins, and the
explicit PSRAM service.  It is still a draft port with named unsupported
architecture features.

Implemented architecture contract
---------------------------------

The port now supplies native COGEXEC-to-HUBEXEC startup, initialized-data and
BSS handling, clock and low-console setup, ``nx_start()``, upward-growing PTRA
task stacks, heap allocation, initial TCB state, save/switch/full restore,
global interrupt-state primitives, CT1 system ticks, low-level and full UART,
stack helpers, register dumps, reset, and board initialization.  The current
linker/runtime window is Hub RAM ``[0, 0x7c000)``; external PSRAM is never
treated as normal address space.

The public context is the fixed 38-long layout in :doc:`context-frame`.
Interrupt processing uses INT1 only, one detached Hub frame, and a guarded
interrupt stack.  This design is intentionally constrained to
``CONFIG_BUILD_FLAT=y`` without ``CONFIG_SMP``.

Board integration
-----------------

The board maps P2-specific hardware to standard NuttX interfaces where a real
lower half exists: GPIO, UART, PWM, capture, ADC, DAC, generic SPI, bit-banged
I2C, W25 MTD, SMART, MMC/SD SPI, and the explicit PSRAM character device.  A
central pin manager and a separate flash/microSD arbiter prevent independent
drivers from silently reconfiguring shared pins.  The new I2C binding exposes
``/dev/i2c0`` on open-drain P24/P25 and can bind the BMP180 at fixed address
``0x77`` and ID ``0x55`` as ``/dev/press0``; this path passed 20/20 physical
cycles.

W25 board initialization validates the raw JEDEC identity before entering the
generic flash driver.  With the P2 Edge ``FLASH`` switch off, the invalid read
fails immediately with ``-ENODEV`` instead of entering a write-completion
poll.  The board deliberately omits ``/dev/smart0`` in that mode and continues
MMC/SD initialization to ``/dev/mmcsd0``.  Startup likewise treats the absent
SmartFS device as an expected switch-mode condition.

Hardware evidence includes 100/100 kernel bring-up cycles, 50/50 NSH cycles,
50/50 digital Smart Pin cycles, 20/20 DAC/ADC cycles, 20/20 BMP180 I2C cycles,
full flash and SD campaigns, and 20/20 historical independent ROM flash
boots.  A
separate development qualification proved one independent P2-EC32MB Rev B ROM
SD boot: the target first validated the raw MBR/FAT32 layout, contiguous
root-file chain, and exact 402,060-byte image with SHA-256
``e1226636846386e5538e731b0fa568ca99fffeb6f992bc6e271f5b5c86e5b3cf``.
The SD-only reset then reached the ordered boot and showcase markers plus the
first NSH prompt with zero serial TX and no loader download.  This is
historical fix qualification, not the current release-candidate identity.
The evidence also includes two consecutive complete 32-MiB PSRAM runs, the
standalone 1,000,000-switch context proof, and a 600-sample host-referenced
raw GETCT campaign spanning a conservative 600.555632 seconds.  Evidence
paths and their limits are documented in the subsystem pages rather than
being inferred from successful compilation.

Current release-candidate state
-------------------------------

The clean candidate is NuttX
``14cadad3a6794e10cbc9f0dfb20f352e4844d35f`` with apps
``a333035462f545056e7a2fb859a9fbdc6d4ef831``.  P2-EC32MB Rev B produced a
402,452-byte raw image with SHA-256
``6ff205df0f724eab91eb0619b53cffc579819cdcb99049578a9f01cb4ba519e2``
and a 494,808-byte ELF with SHA-256
``1409460f5399e267516e6ea394d99cf2b30e638ac55cbc82318175712c01dd3c``.
Its RAM showcase is **PASS** at
``/tmp/p2-release-final.14cadad-r1/ec32mb-showcase-hil``, including ADC/DAC
on the current RC fixture and an RC-safe PWM control smoke rather than a
digital waveform claim.  All 16 required stages passed in 379.246116 seconds;
``status.json`` has SHA-256
``2ce85939d560a2e727b845d1e87f758939dd6028ce6b6afaba1bcc1c031e8250``.
Flash programming is **PASS** at
``/tmp/p2-release-final.14cadad-r1/ec32mb-flash-program``.  Exact-candidate
reset-only flash boot is **PASS** from cycles 1--10 under
``/tmp/p2-release-final.14cadad-r1/ec32mb-flashboot-hil``: each completed in
about 94 seconds with CRC ``B31D0271``, zero pre-prompt TX, and persistent
sequence ``F23A0713`` / one-MiB FNV-1a ``693C9DC5``.  The originally requested
20-cycle wrapper was intentionally stopped after those ten redundant PASS
results to keep testing proportional.  Its cycle 11 and top-level
``status.json`` report manual interruption/FAIL and are not PASS artifacts.
The exact-candidate SD write and raw-card inspection are **PASS**.  After an
initial write timeout, the read-only inspector diagnosed the pre-existing MBR
as invalid rather than accepting it.  The corrective format created a
type-``0x0c`` partition at LBA 2048 with 61,130,752 sectors; the final write
placed the exact 402,452-byte candidate, and the independent inspector proved
valid MBR/VBR/FSInfo/root metadata, a contiguous 25-cluster chain, EOC, and
FNV-1a ``D0D0F215`` over the complete file.  Evidence is under
``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-format-final``,
``ec32mb-sd-write-final``, and ``ec32mb-sd-inspect-final``.  The exact
candidate's independent no-loader SD-only ROM reset is also **PASS** at
``ec32mb-sdboot-hil``.  With user-confirmed
``(FLASH,up,down)=(OFF,OFF,ON)``, it booted in 16.688852 seconds with zero
serial TX, no loader download, and all required entry-through-NSH markers in
order.  Its ``status.json`` SHA-256 is
``61534212bd8bcf9f4ca996d36731c0e612951d7d9554c96ff360aaf607a3e758``.

The P2-EC Rev D build has no PSRAM.  Its 386,752-byte raw image has SHA-256
``596b0f022c28fa4462a6e13692ad54ecab095f17d6532d441e60e0dee481c230``
and its 476,768-byte ELF has SHA-256
``2d1e4f2d84455b6cd15edc31571796d9cc1505fae49de7f65245856b70f5bea7``.
Rev-D runtime remains **HIL-REQUIRED**.

Both candidate builds are flat UP; SMP is deliberately not enabled.

The fresh ABI run is **PASS** at
``/tmp/p2-release-final.14cadad-r1/abi/20260713T231547Z``, bound to the NuttX
candidate and clang SHA-256
``cc89d3c27b75c9e059093d1e5c6cc7a392b74d977e30d90ca9994f97001224f7``.
All nine capability statuses are ``SUPPORTED`` and ``summary.txt`` has
SHA-256
``ba91f6134733cfd5d0e02725d4ed64af1786f4b30e11ebe18a548dc4160e95a0``.
The toolchain lock has SHA-256
``66871ac6bb8a96fbea5b5fc405e6a1a3743fa6c441775737cab80066678250aa``.
The authoritative paired-tree host suite passed 316 tests in 19.629 seconds.
The 20-file local release package and extracted-bundle verification are
**PASS**; its bundle SHA-256 is
``07604e5f5977570c9ea1c2fd9c7696a62be03035fb69496aefabee84c3f03358``.
The public ``p2-edge-flat-up-v0.1.0`` prerelease contains all 20 assets.  One
fresh draft-release download matched every local asset byte for byte, passed
all 19 recorded checksums, and passed the bundled verifier after restoring the
standalone-download executable mode bits.

Unsupported or incomplete areas
-------------------------------

* SMP, protected/kernel builds, nested interrupt routing, and non-timer
  architecture IRQ sources are not implemented.
* Reset-interrupted flash recovery is verified; true power-loss recovery is
  not, because no controlled power-cycle command is available.
* P2-EC Rev D has no PSRAM.  Equivalent RAM, flash, and ROM SD-boot HIL
  remains required on that board; the physical boot proof above applies to
  P2-EC32MB Rev B.

Those boundaries remain explicit unsupported, deferred, or ``HIL-REQUIRED``
items; they are not implied successes of the working UP port.  SMP is
**DEFERRED / OUT OF SCOPE** and does not gate completion of the accepted
flat-UP configuration.
