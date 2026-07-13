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
  passed 20/20 physical cycles.

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
   * - Digital Smart Pins
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
   * - Independent ROM flash boot
     - PASS
     - ``artifacts/hil/20260713T103452Z-flashboot``; 20/20 DTR resets on one
       serial connection with zero pre-prompt bytes, boot CRC ``23FCF91E``,
       and preserved 1 MiB SmartFS hash ``693C9DC5``.
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

Remaining evidence work
-----------------------

* The four-profile OSTest campaign is complete.  The earlier diagnostic
  ``artifacts/hil/20260713T040951.397206Z-ostest`` remains preserved as the
  pre-fix timeout which motivated the non-yielding completion-state poll; it
  is superseded by the accepted PI assertion and production artifacts above.
* The post-freeze ABI matrix is complete at
  ``artifacts/hil/abi/20260713T155112Z``.  It records NuttX source/tool commit
  ``cfaf600a55f41d8ea538b83b1c8c1ce459c9996a`` and the active clang SHA-256;
  all nine capability status files are ``SUPPORTED``.  The independent
  64-bit comparison verifier passed all 41,472 functional boundary pairs.

Explicit blockers and non-claims
--------------------------------

* True power-cycle and power-loss recovery cannot run until an external power
  control command is available.  Reset interruption is reported separately.
* Card-absent behavior requires physically removing the microSD.  There is no
  invented card-detect GPIO.
* The board-switch behavior is known, but its printed label has not been
  visually confirmed.
* **DEFERRED / OUT OF SCOPE:** NuttX SMP is not implemented.
  ``CONFIG_SMP`` is deliberately rejected; the accepted architecture is flat
  UP plus measured deterministic service cogs.  A future two-CPU project
  would need secondary startup, per-CPU scheduler state, atomics/spinlocks,
  IPI/reschedule, affinity/migration, barriers, and physical multicog stress.
  SMP does not gate completion of the current flat-UP goal.

Finish gate
-----------

The applicable OSTest matrix, post-freeze ABI evidence, host/static checks,
artifact index, and committed implementation baselines are complete.  The
flat-UP project is complete.  Fixture-dependent gaps remain named rather than
being converted into support claims; the explicitly deferred SMP work is not
part of this finish gate.
