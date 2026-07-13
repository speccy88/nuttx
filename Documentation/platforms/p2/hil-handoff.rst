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
resistive/capacitive analog fixture.  P8 SCK and P9 chip select are unconnected
SPI outputs.  A BMP180 is installed with P24 SDA and P25 SCL.  Follow
:doc:`hil-wiring`: configure every receiver before its source, never drive
both ends of a direct jumper, and verify the I2C pull-ups and idle voltage
before enabling open-drain I2C.
The implemented endpoints are ``/dev/i2c0`` and, after the fixed-address
``0x77``/chip-ID ``0x55`` BMP180 probe succeeds, ``/dev/press0``.

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
* independent ROM flash boot, 20/20 DTR resets:
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

Do not rerun destructive storage tests merely to rediscover these results.
If a future fixture-dependent gap is brought into scope, resume from that
specific item and keep serial ownership exclusive through
``/private/tmp/nuttx-p2-hil.lock``.

Known physical blockers and deferred scope
------------------------------------------

No power-cycle command is configured, so true power-loss testing remains
blocked.  SMP is **DEFERRED / OUT OF SCOPE** for this goal: it is an
unsupported future architecture project, not a HIL toggle or a flat-UP finish
gate.
