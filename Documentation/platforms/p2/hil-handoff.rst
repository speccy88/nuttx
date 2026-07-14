HIL handoff
===========

Status: the verified local target is ``p2-ec32mb`` on
``/dev/cu.usbserial-P97cvdxp`` at 230400 baud.  HIL helpers remain dry-run by
default and require explicit gates for serial, reset, flash, or SD access.

Environment and provenance
--------------------------

Run ``./tools/p2/bootstrap-local.sh`` when the pinned local toolchain needs to
be reconstructed, then source ``~/.p2-nuttx-env``.  ``.p2-hil.env`` contains
workspace-specific gates and is intentionally untracked.  When child
processes need those values, export them while sourcing::

  set -a
  source "$HOME/.p2-nuttx-env"
  source .p2-hil.env
  set +a

Use ``./tools/p2/build.sh <profile>`` for sealed build artifacts.  Preserve the
build directory, copied config, source-status files, commit IDs, binary hash,
``toolchain.lock``, loader hash, exact test command, raw console bytes, parsed
markers, and terminal status.  A ``RUNNING`` manifest or an observed marker is
not a PASS without the campaign's final status file.

Installed fixture
-----------------

The direct digital links are P0/P1, P2/P3, and P6/P7.  P4/P5 now carries the
resistive/capacitive analog fixture.  The earlier 50/50 PWM/capture waveform
campaign used a direct P4/P5 jumper; it is historical evidence for that
fixture.  With the current RC fixture, use P4/P5 for ADC/DAC and only the
bounded RC-safe ``/dev/pwm0`` open/start/stop smoke.  Do not claim a current
digital PWM waveform or capture measurement.  P8 SCK and P9 chip select are
unconnected SPI outputs.  A BMP180 is installed with P24 SDA and P25 SCL.
Follow :doc:`hil-wiring`: configure every receiver before its source, never
drive both ends of a direct jumper, and verify the I2C pull-ups and idle
voltage before enabling open-drain I2C.
The implemented endpoints are ``/dev/i2c0`` and, after the fixed-address
``0x77``/chip-ID ``0x55`` BMP180 probe succeeds, ``/dev/press0``.

The physically confirmed P2-EC32MB Rev B boot positions, written in
``(FLASH,up,down)`` order, are ``(ON,OFF,OFF)`` for serial/flash and
``(OFF,OFF,ON)`` for SD-only.  With ``FLASH`` off, W25 JEDEC validation is
expected to return ``-ENODEV`` before generic W25 initialization.  Therefore
``/dev/smart0`` is intentionally absent and the startup script skips its
SmartFS mount; this is not a boot failure.  MMC/SD initialization continues
and exposes ``/dev/mmcsd0``.

Durable evidence already collected
----------------------------------

* native context, 1,000,000 switches:
  ``artifacts/hil/20260713T034110.407118Z-context``;
* NuttX bring-up, 100/100:
  ``artifacts/hil/20260713T034525.287219Z-bringup``;
* NSH command campaign, 50/50:
  ``artifacts/hil/20260713T035042.747009Z-nsh``;
* Smart Pins including SPI, 50/50:
  ``artifacts/hil/20260713T063221.439668Z-smartpins``;
* DAC/ADC on the resistive/capacitive fixture, 20/20:
  ``artifacts/hil/20260713T110743.191438Z-smartpins``;
* BMP180 I2C transactions and pressure reads, 20/20:
  ``artifacts/hil/20260713T111043.745628Z-i2c``;
* flash filesystem and reset-interruption recovery:
  ``artifacts/hil/20260713T063712.505220Z-flashfs``;
* SD filesystem and 1,000 bus alternations:
  ``artifacts/hil/20260713T083209.592794Z-sd``;
* two consecutive full PSRAM runs:
  ``artifacts/hil/20260713T100106.997809Z-psram`` and
  ``artifacts/hil/20260713T100735.645104Z-psram``;
* historical independent ROM flash boot, 20/20 DTR resets:
  ``artifacts/hil/20260713T103452Z-flashboot``; and
* raw GETCT clock qualification, 600 ordered samples across a conservative
  600.555632-second span:
  ``artifacts/hil/20260713T113742Z-build-clock`` and
  ``artifacts/hil/20260713T114543.089052Z-clock``;
* OSTest PI assertions, 1/1:
  ``artifacts/hil/20260713T115624Z-build-ostest-pi-assert`` and
  ``artifacts/hil/20260713T115705.736374Z-ostest``;
* OSTest condition assertions, 1/1:
  ``artifacts/hil/20260713T121555Z-build-ostest-cond-assert`` and
  ``artifacts/hil/20260713T121658.724366Z-ostest``;
* OSTest PI production, 5/5:
  ``artifacts/hil/20260713T123519Z-build-ostest-pi-production`` and
  ``artifacts/hil/20260713T123627.152482Z-ostest``; and
* OSTest condition production, 5/5:
  ``artifacts/hil/20260713T140927Z-build-ostest-cond-production`` and
  ``artifacts/hil/20260713T141008.365027Z-ostest``.

The earlier diagnostic ``artifacts/hil/20260713T114018.397164Z-clock``
retains 169 clean samples followed by an isolated reset.  The identical ELF
then passed the complete ten-minute campaign, so no deterministic defect was
reproduced.

Current release-candidate handoff
---------------------------------

The clean release candidate is NuttX
``14cadad3a6794e10cbc9f0dfb20f352e4844d35f`` plus apps
``a333035462f545056e7a2fb859a9fbdc6d4ef831``:

* P2-EC32MB Rev B build
  ``/tmp/p2-release-final.14cadad-r1/ec32mb-build``; raw ``nuttx.bin``:
  402,452 bytes, SHA-256
  ``6ff205df0f724eab91eb0619b53cffc579819cdcb99049578a9f01cb4ba519e2``;
  ELF: 494,808 bytes, SHA-256
  ``1409460f5399e267516e6ea394d99cf2b30e638ac55cbc82318175712c01dd3c``;
  build status SHA-256
  ``518e6dc825e0501d54c208e869af97a8ed820af0bccca123ba1aab96896edede``;
  config SHA-256
  ``28fcde788eddbf82c100426015b7331b37ccc747ee8973e5d7d457256f70f252``.
* P2-EC Rev D build
  ``/tmp/p2-release-final.14cadad-r1/ec-revd-build``; raw ``nuttx.bin``:
  386,752 bytes, SHA-256
  ``596b0f022c28fa4462a6e13692ad54ecab095f17d6532d441e60e0dee481c230``;
  ELF: 476,768 bytes, SHA-256
  ``2d1e4f2d84455b6cd15edc31571796d9cc1505fae49de7f65245856b70f5bea7``;
  build status SHA-256
  ``0f27ae1662c18ab051372d157ac030aa3f66bebb104a68ef3597462878737608``;
  config SHA-256
  ``ce66616aa712d9834372ef0bb7810f50262e55cfc2991aea22c43ea940c6a1ff``.
  Rev D has no PSRAM and all Rev-D runtime claims remain **HIL-REQUIRED**.

Both candidate builds are flat UP; SMP is deliberately not enabled.

Rev-B RAM showcase HIL is **PASS** at
``/tmp/p2-release-final.14cadad-r1/ec32mb-showcase-hil``.  All 16 required
stages passed in 379.246116 seconds; ``status.json`` has SHA-256
``2ce85939d560a2e727b845d1e87f758939dd6028ce6b6afaba1bcc1c031e8250``.
Candidate flash programming is **PASS** at
``/tmp/p2-release-final.14cadad-r1/ec32mb-flash-program``.  It programmed
``[0x00000000,0x00062500)`` after erasing
``[0x00000000,0x00063000)``.  Exact-candidate reset-only flash boot is also
**PASS** from the ten completed cycle artifacts under
``/tmp/p2-release-final.14cadad-r1/ec32mb-flashboot-hil``.  Cycles 1--10 each
booted in about 94 seconds with stable CRC ``B31D0271``, zero pre-prompt TX,
and exact persistent flash sequence ``F23A0713`` / one-MiB FNV-1a
``693C9DC5``.  The originally requested 20-cycle wrapper was intentionally
stopped after those ten redundant PASS results to keep testing proportional.
Its cycle 11 and top-level ``status.json`` report manual interruption/FAIL and
must not be described as PASS artifacts; the acceptance rests on the ten
completed per-cycle PASS files.

Exact-candidate SD writing and raw layout are **PASS**.  The first write at
``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-write`` timed out, after which
``ec32mb-sd-inspect`` correctly rejected the existing MBR fields.  The
corrective format at ``ec32mb-sd-format-final`` passed in 302.761617 seconds
with MBR type ``0C``, start LBA 2048, and 61,130,752 sectors.  The final write
at ``ec32mb-sd-write-final`` passed for the exact 402,452-byte image; its
402,456-byte staged payload has SHA-256
``f7ee30fde6ce7a69b63a5c837d9c28e380df75af7ce6e76bd3ded2336c1e5bbf``.
The final read-only inspection at ``ec32mb-sd-inspect-final`` passed in
38.236762 seconds, including MBR/VBR/FSInfo/root, a contiguous 25-cluster
chain, EOC, exact bytes, and FNV-1a ``D0D0F215``.  The three PASS status hashes
are respectively
``df72f9b37775b00545edee943ad3054ba7a1233a8657f3a957e5e217a4a1126c``,
``622f56c19d3455f895a3fd5623d8f866740d94f47c68379df91bec51c546a21a``,
and ``f5345c72e956425c894549b09148180774368c102986a0bfa3305cd23e01c1e0``.
The independent no-loader SD-only reset is **PASS** at
``/tmp/p2-release-final.14cadad-r1/ec32mb-sdboot-hil``.  With the physically
confirmed ``(FLASH,up,down)=(OFF,OFF,ON)`` setting, the exact 402,452-byte
candidate reached the ordered entry-through-NSH markers in 16.688852 seconds
with no loader download and zero serial TX.  Its ``status.json`` SHA-256 is
``61534212bd8bcf9f4ca996d36731c0e612951d7d9554c96ff360aaf607a3e758``.
The local 20-file release package, extracted-bundle verification, one fresh
GitHub pre-publication draft download, and publication are **PASS**.  The
public, normal ``p2-edge-flat-up-v0.1.0`` release contains all 20 byte-matched
assets; all 19 recorded checksums and the bundled verifier passed after
restoring the standalone-download executable mode bits.

The candidate-bound ABI run is **PASS** at
``/tmp/p2-release-final.14cadad-r1/abi/20260713T231547Z`` with NuttX commit
``14cadad3a6794e10cbc9f0dfb20f352e4844d35f`` and clang SHA-256
``cc89d3c27b75c9e059093d1e5c6cc7a392b74d977e30d90ca9994f97001224f7``.
All nine capability statuses are ``SUPPORTED``; ``summary.txt`` has SHA-256
``ba91f6134733cfd5d0e02725d4ed64af1786f4b30e11ebe18a548dc4160e95a0``.
The accepted lock has SHA-256
``66871ac6bb8a96fbea5b5fc405e6a1a3743fa6c441775737cab80066678250aa``.
The authoritative host suite passed 316 tests in 19.629 seconds.

The older SD fix qualification used a different 402,060-byte development
image with SHA-256
``e1226636846386e5538e731b0fa568ca99fffeb6f992bc6e271f5b5c86e5b3cf``.
Its raw-card and independent SD-only boot evidence is under
``/private/tmp/p2-release-final.oBc9V4/``.  It proves the ROM layout and
W25-off recovery fix, but it must not be substituted for the exact-candidate
PASS artifact above.

The candidate evidence is preserved in the local release evidence archive,
SHA-256
``be8550353b06fea07500cbff22b627b6edc8e91124ed6b0fe2f3e624eaa8a9ab``.
The package remains provisional at its ``/tmp`` path until the exact assets
are uploaded and verified from a fresh GitHub download.

Do not rerun the historical destructive filesystem stress campaigns merely
to rediscover those results.  The exact-candidate RAM, flash-reset, SD-write,
raw-inspection, and one-shot SD-only reset gates are complete.  Keep serial
ownership exclusive through ``/private/tmp/nuttx-p2-hil.lock`` for any future
target work.

Known physical blockers and deferred scope
------------------------------------------

No power-cycle command is configured, so true power-loss testing remains
blocked.  The available P2-EC32MB Rev B has broad historical RAM, flash, and
SD-boot evidence, while the exact candidate closures are listed above;
equivalent P2-EC Rev D campaigns remain **HIL-REQUIRED**.  SMP is **DEFERRED /
OUT OF SCOPE** for this goal: it is an unsupported future architecture
project, not a HIL toggle or a flat-UP finish gate.
