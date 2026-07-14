P2 hardware-in-the-loop status
==============================

Snapshot: 2026-07-13.  Build or host-test success is not hardware success.
Every physical claim below names its preserved artifact.  The detailed final
record is :doc:`final-hil-report`; the compact working table requested during
bring-up is ``goal-status-table.md`` in this directory.

Endpoint and safety
-------------------

* Target: P2-EC32MB Rev B.
* Console/loader: ``/dev/cu.usbserial-P97cvdxp`` through a Parallax PropPlug.
* Console baud: 230400.  Loader baud: 2000000.
* Shared host lock: ``/private/tmp/nuttx-p2-hil.lock``.
* Flash, microSD, and loopback destructive gates were explicitly authorized.
* DTR reset is available; externally controlled removal of board power is not.
* Direct digital loopbacks are P0--P1, P2--P3, and P6--P7.  Digital tests
  configure the receiver before enabling one source.
* A BMP180 fixture is connected with P24 SDA and P25 SCL.  The open-drain
  lower half and sensor path passed 20/20 physical cycles.
* The user replaced the P4--P5 direct jumper with the requested series
  resistor and added the capacitor from P5 to ground.  DAC/ADC subsequently
  passed 20/20 physical cycles.  The current release candidate also passed a
  bounded ``/dev/pwm0`` open/start/stop smoke on this RC load, but that is not
  digital PWM waveform/capture qualification.  The 50/50 digital waveform
  result below belongs to the earlier direct-jumper fixture.

Accepted physical results
-------------------------

.. list-table:: Accepted P2 hardware evidence
   :header-rows: 1
   :widths: 24 14 62

   * - Area
     - Result
     - Evidence and exact qualification
   * - Standalone native loop
     - PASS
     - ``artifacts/hil/20260712T211034.259011Z-hello``; 10/10 loads with
       serial, data, BSS, PTRA, counter, LED, command, and echo markers.
   * - Detached context
     - PASS
     - ``artifacts/hil/20260713T034110.407118Z-context``; exactly 1,000,000
       CT1 switches with register, stack, spill, variadic, scratch, and ISR
       stack guards.
   * - Native NuttX boot
     - PASS
     - ``artifacts/hil/20260712T230747.950915Z-boot``; entry zero, Hub text,
       upward PTRA, data/BSS, early P62/P63 console, and CT1 tick.
   * - Deterministic bring-up
     - PASS
     - ``artifacts/hil/20260713T034525.287219Z-bringup``; 100/100 reset/load
       cycles covering tasks, preemption, semaphore wake, heap, and stacks.
   * - NuttShell
     - PASS
     - ``artifacts/hil/20260713T035042.747009Z-nsh``; 50/50 cycles with the
       required command, RX, TX, device, mount, heap, process, and time output.
   * - Historical digital Smart Pins
     - PASS
     - ``artifacts/hil/20260713T063221.439668Z-smartpins``; 50/50 cycles for
       GPIO, edge observation, UART, PWM/capture, and mode-0 SPI.
   * - DAC and ADC
     - PASS
     - ``artifacts/hil/20260713T110743.191438Z-smartpins``; 20/20 cycles and
       60 strictly monotonic samples.  DAC codes 16383, 32767, and 49151
       produced ADC ranges 678--679, 1019--1020, and 1362--1363; both pins
       floated safely after every cycle.
   * - I2C and BMP180
     - PASS
     - ``artifacts/hil/20260713T111043.745628Z-i2c``; 20/20 cycles at address
       ``0x77`` and ID ``0x55`` with true repeated-start transfers, 640
       pressure reads from 100000 through 100019 Pa, and zero recovery pulses.
   * - W25, MTD, and SmartFS
     - PASS
     - ``artifacts/hil/20260713T063712.505220Z-flashfs``; JEDEC ``EF7018``,
       protected boot reservation, 1 MiB hash ``693C9DC5``, persistence,
       rewrites, ENOSPC, and reset-interrupted recovery.
   * - microSD and FAT
     - PASS
     - ``artifacts/hil/20260713T083209.592794Z-sd``; block device, FAT,
       1 MiB hash ``BE5C9DC5``, rename/delete, 64 stress passes, and 1,000
       flash/SD ownership alternations.
   * - External 32 MiB PSRAM
     - PASS
     - ``artifacts/hil/20260713T100106.997809Z-psram`` and
       ``artifacts/hil/20260713T100735.645104Z-psram``; two consecutive full
       32 MiB passes with hash ``634C9DC5``, boundaries, address lines,
       random transfers, concurrency, timeout/recovery, and CE bounds.
   * - Historical independent ROM flash boot
     - PASS
     - ``artifacts/hil/20260713T103452Z-flashboot``; 20/20 DTR resets on one
       serial connection with zero pre-prompt bytes, boot CRC ``23FCF91E``,
       and preserved 1 MiB SmartFS hash ``693C9DC5``.
   * - Exact-candidate reset-only ROM flash boot
     - PASS: ten completed proportional cycles
     - ``/tmp/p2-release-final.14cadad-r1/ec32mb-flashboot-hil/cycle-001``
       through ``cycle-010``; each completed in about 94 seconds with one
       serial connection, zero pre-prompt TX, boot CRC ``B31D0271``, and exact
       persistent sequence ``F23A0713`` / one-MiB FNV-1a ``693C9DC5``.  The
       20-cycle wrapper was intentionally stopped after ten redundant PASS
       results.  Cycle 11 and the top-level manifest report manual
       interruption/FAIL and are not claimed as PASS artifacts.
   * - Historical development ROM microSD boot
     - PASS on P2-EC32MB Rev B
     - ``/private/tmp/p2-release-final.oBc9V4/`` contains the development
       ``ec32mb-sd-rom-inspect-w25-guard`` and
       ``ec32mb-sd-rom-boot-w25-guard`` results.  The first validated the
       on-card MBR, FAT32 metadata, contiguous root-file chain, and exact
       402,060-byte image with SHA-256
       ``e1226636846386e5538e731b0fa568ca99fffeb6f992bc6e271f5b5c86e5b3cf``.
       The second reached the selected-board showcase marker and first NSH
       prompt after one reset with zero serial TX and no loader download in
       SD-only ``(FLASH,up,down)=(OFF,OFF,ON)`` mode.  This proves the fix and
       switch behavior, not the identity of the current release candidate.
   * - Exact-candidate ``_BOOT_P2.BIX`` write, raw layout, and SD-only boot
     - PASS on P2-EC32MB Rev B
     - The first write diagnostic timed out; the following read-only inspector
       correctly rejected the pre-existing MBR fields.  Corrective format
       ``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-format-final`` passed in
       302.761617 seconds with type ``0C``, start LBA 2048, and 61,130,752
       sectors.  Final write
       ``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-write-final`` passed for
       the exact 402,452-byte candidate.  Final read-only inspection
       ``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-inspect-final`` passed in
       38.236762 seconds with valid MBR/VBR/FSInfo/root, a contiguous
       25-cluster chain and EOC, exact bytes, and FNV-1a ``D0D0F215``.  These
       file/layout results were followed by independent SD-only reset PASS
       ``/tmp/p2-release-final.14cadad-r1/ec32mb-sdboot-hil``.  At the
       user-confirmed ``(FLASH,up,down)=(OFF,OFF,ON)`` setting, it booted in
       16.688852 seconds with zero serial TX, no loader download, verified
       contiguity, and all entry-through-NSH markers in order.  Its
       ``status.json`` SHA-256 is
       ``61534212bd8bcf9f4ca996d36731c0e612951d7d9554c96ff360aaf607a3e758``.
   * - Dedicated scheduler stress
     - PASS
     - Build ``artifacts/hil/20260713T112709Z-build-schedstress`` and HIL
       ``artifacts/hil/20260713T112942.518754Z-schedstress``; one physical run
       completed exactly 1,004,078 counted events in 165.434771 seconds across
       priorities, round robin, semaphore, PI mutex, condition variable,
       message queue, signal, timer, pthread, and task-recreation paths.
       Stack, heap restoration, and 512/512 separate concurrent allocations
       also passed.
   * - Raw GETCT clock qualification
     - PASS
     - Build ``artifacts/hil/20260713T113742Z-build-clock`` and HIL
       ``artifacts/hil/20260713T114543.089052Z-clock``; 600 ordered samples
       and ``DONE=600`` covered a 600.555632-second conservative span across
       approximately 25 counter wraps.  The midpoint estimate was
       180,002,153.6455524 Hz (+11.9647 ppm), with conservative bounds of
       179,998,600.271539--180,005,707.159862 Hz (-7.776--+31.706 ppm).  The
       ten-second prefix, one-outstanding-command rule, and five-second
       maximum-gap rule also passed.
   * - Applicable OSTest matrix
     - PASS
     - All 45 applicable matrix rows and all 57/57 strict parser groups passed
       in each applicable cycle.  PI assertions: build
       ``artifacts/hil/20260713T115624Z-build-ostest-pi-assert`` and HIL
       ``artifacts/hil/20260713T115705.736374Z-ostest``; 1/1 in
       1112.947113 seconds with assertions enabled and seven accepted timing
       warnings.  Condition assertions: build
       ``artifacts/hil/20260713T121555Z-build-ostest-cond-assert`` and HIL
       ``artifacts/hil/20260713T121658.724366Z-ostest``; 1/1 in
       1083.622049 seconds with assertions enabled and nine accepted timing
       warnings.
   * - Repeated production OSTest
     - PASS
     - All 57/57 strict parser groups passed in every production cycle.  PI
       production: build
       ``artifacts/hil/20260713T123519Z-build-ostest-pi-production`` and HIL
       ``artifacts/hil/20260713T123627.152482Z-ostest``; 5/5 cycles in
       1109.718123, 1109.821585, 1109.823341, 1109.809943, and 1109.871800
       seconds with 15 accepted timing warnings total.  Condition production:
       build ``artifacts/hil/20260713T140927Z-build-ostest-cond-production``
       and HIL ``artifacts/hil/20260713T141008.365027Z-ostest``; 5/5 cycles in
       1157.549036, 1157.470767, 1157.390669, 1157.597868, and 1157.556411
       seconds with 25 accepted timing warnings total.

The retained diagnostic ``artifacts/hil/20260713T114018.397164Z-clock``
captured 169 clean ordered samples before an isolated reset.  The identical
ELF then passed the complete campaign, so no deterministic defect was
reproduced; the diagnostic remains preserved as a failed run.

Current release-candidate closure
---------------------------------

The current clean candidate is bound to NuttX
``14cadad3a6794e10cbc9f0dfb20f352e4844d35f`` and apps
``a333035462f545056e7a2fb859a9fbdc6d4ef831``.  Its exact build identities
are:

* P2-EC32MB Rev B build
  ``/tmp/p2-release-final.14cadad-r1/ec32mb-build``: 402,452-byte raw image,
  SHA-256
  ``6ff205df0f724eab91eb0619b53cffc579819cdcb99049578a9f01cb4ba519e2``;
  494,808-byte ELF, SHA-256
  ``1409460f5399e267516e6ea394d99cf2b30e638ac55cbc82318175712c01dd3c``;
  build-status/config SHA-256
  ``518e6dc825e0501d54c208e869af97a8ed820af0bccca123ba1aab96896edede`` /
  ``28fcde788eddbf82c100426015b7331b37ccc747ee8973e5d7d457256f70f252``.
* P2-EC Rev D build
  ``/tmp/p2-release-final.14cadad-r1/ec-revd-build``: 386,752-byte raw image,
  SHA-256
  ``596b0f022c28fa4462a6e13692ad54ecab095f17d6532d441e60e0dee481c230``;
  476,768-byte ELF, SHA-256
  ``2d1e4f2d84455b6cd15edc31571796d9cc1505fae49de7f65245856b70f5bea7``;
  build-status/config SHA-256
  ``0f27ae1662c18ab051372d157ac030aa3f66bebb104a68ef3597462878737608`` /
  ``ce66616aa712d9834372ef0bb7810f50262e55cfc2991aea22c43ea940c6a1ff``.
  Rev D has no PSRAM and remains **HIL-REQUIRED** for every runtime claim.

Both candidate builds are flat UP; SMP is deliberately not enabled.

At this snapshot, Rev-B RAM showcase HIL is **PASS** in
``/tmp/p2-release-final.14cadad-r1/ec32mb-showcase-hil`` and programming the
candidate to serial flash is **PASS** in
``/tmp/p2-release-final.14cadad-r1/ec32mb-flash-program``.  The showcase
covered NSH help, LEDs, Tab completion, history, Ctrl-C, GPIO, UART, ADC/DAC,
RC-safe PWM smoke, SPI, BMP180 I2C, storage probe, and the complete 32 MiB
PSRAM service.  All 16 stages passed in 379.246116 seconds; its status SHA-256
is ``2ce85939d560a2e727b845d1e87f758939dd6028ce6b6afaba1bcc1c031e8250``.
Programming covered ``[0x00000000,0x00062500)`` after erasing
``[0x00000000,0x00063000)``.  Exact-candidate reset-only flash boot is
**PASS** from completed cycle artifacts 1--10 under
``/tmp/p2-release-final.14cadad-r1/ec32mb-flashboot-hil``.  Each cycle took
about 94 seconds and proved CRC ``B31D0271``, zero pre-prompt TX, and exact
persistent sequence ``F23A0713`` / one-MiB FNV-1a ``693C9DC5``.  The
originally requested 20-cycle wrapper was intentionally stopped after ten
redundant PASS results to keep testing proportional.  Cycle 11 and the
top-level ``status.json`` report manual interruption/FAIL, not a hardware
failure, and are not claimed as PASS artifacts.  Exact-candidate SD format,
write, and read-only raw-card verification are **PASS** at
``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-format-final``,
``ec32mb-sd-write-final``, and ``ec32mb-sd-inspect-final``.  The final card
contains the exact 402,452-byte candidate in a contiguous 25-cluster file and
the target reproduced FNV-1a ``D0D0F215``.  Independent SD-only reset boot is
**PASS** at ``ec32mb-sdboot-hil`` in 16.688852 seconds with user-confirmed
``(OFF,OFF,ON)``, no loader download, zero serial TX, and ordered
entry/data/BSS/NuttX, W25-off, microSD, SmartFS-unavailable, showcase, and NSH
markers.  The boot status SHA-256 is
``61534212bd8bcf9f4ca996d36731c0e612951d7d9554c96ff360aaf607a3e758``.
The release package, extracted-bundle verification, one fresh GitHub
pre-publication draft download, and publication are **PASS**.  The public,
normal ``p2-edge-flat-up-v0.1.0`` release contains all 20 byte-matched assets;
all 19 recorded checksums and the bundled verifier passed after restoring the
standalone-download executable mode bits.

The fresh ABI run is **PASS** at
``/tmp/p2-release-final.14cadad-r1/abi/20260713T231547Z``.  It records NuttX
``14cadad3a6794e10cbc9f0dfb20f352e4844d35f`` and clang SHA-256
``cc89d3c27b75c9e059093d1e5c6cc7a392b74d977e30d90ca9994f97001224f7``.
All nine capability statuses are ``SUPPORTED``; the summary SHA-256 is
``ba91f6134733cfd5d0e02725d4ed64af1786f4b30e11ebe18a548dc4160e95a0``
and the accepted lock SHA-256 is
``66871ac6bb8a96fbea5b5fc405e6a1a3743fa6c441775737cab80066678250aa``.
The authoritative paired-tree host suite passed 316 tests in 19.629 seconds.

The required candidate evidence above is preserved in the local evidence
archive with SHA-256
``be8550353b06fea07500cbff22b627b6edc8e91124ed6b0fe2f3e624eaa8a9ab``.
Its current ``/tmp`` location is not durable until the exact release asset is
uploaded and verified from a fresh GitHub download.

Remaining evidence work
-----------------------

* The four-profile OSTest campaign is complete.  The earlier diagnostic
  ``artifacts/hil/20260713T040951.397206Z-ostest`` remains preserved as the
  pre-fix timeout which motivated the non-yielding completion-state poll; it
  is superseded by the accepted PI assertion and production artifacts above.
* ``artifacts/hil/abi/20260713T155112Z`` remains the historical pre-fix ABI
  baseline.  The fresh candidate-bound ABI PASS is the provisional
  ``/tmp/p2-release-final.14cadad-r1/abi/20260713T231547Z`` run described
  above and is preserved in the local release evidence archive.

Explicit blockers and non-claims
--------------------------------

* True power-cycle and power-loss recovery cannot run until an external power
  control command is available.  Reset interruption is reported separately.
* Card-absent behavior requires physically removing the microSD.  There is no
  invented card-detect GPIO.
* P2-EC32MB Rev B switch positions used by the accepted boot evidence are
  physically confirmed: serial/flash is ``(ON,OFF,OFF)`` and SD-only is
  ``(OFF,OFF,ON)`` in ``(FLASH,up,down)`` order.
* P2-EC Rev D is build- and statically verified, but equivalent RAM, flash,
  and SD-boot physical campaigns remain **HIL-REQUIRED** on that board.
* **DEFERRED / OUT OF SCOPE:** NuttX SMP is not implemented.
  ``CONFIG_SMP`` is deliberately rejected; the accepted architecture is flat
  UP plus measured deterministic service cogs.  A future two-CPU project
  would need secondary startup, per-CPU scheduler state, atomics/spinlocks,
  IPI/reschedule, affinity/migration, barriers, and physical multicog stress.
  SMP does not gate completion of the current flat-UP goal.

Finish gate
-----------

The applicable OSTest matrix and the current candidate's RAM showcase, ABI,
host/static checks, clean dual-board builds, flash programming, and ten-cycle
reset-only flash-boot proof are complete.  Exact-candidate SD writing and
raw-card inspection plus its no-loader SD-only ROM reset are also complete.
The local 20-file package, all six installer dry-runs, extracted-bundle
verification, one fresh GitHub pre-publication draft download, normal release,
and both fork-master fast-forwards are complete.  The superseded draft PR is
closed.  The flat-UP release closure is complete.
Fixture-dependent gaps remain named rather than being converted into support
claims; the explicitly deferred SMP work is not part of this finish gate.
