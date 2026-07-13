Cloud draft report (historical)
===============================

Status: **HISTORICAL**.  This page records the original cloud-only starting
point and must not be used as the current HIL acceptance report.

Original starting point
-----------------------

The continuation began on branch ``work`` at
``39cc55135fd24f02006e56f9fc1f0476edea1888``.  At that time the checkout had
the P2 architecture, board, documentation, and tool skeletons, but the cloud
image lacked executable p2llvm, loadp2, and kconfig-frontends tools.  Build and
ABI steps were honestly recorded as ``BLOCKED`` rather than replaced by fake
success hooks.

That phase added the ``p2-ec32mb:bringup`` profile, expanded the offline ABI
probe sources, and made cloud build/bootstrap wrappers preserve diagnostic
artifacts.  ``tools/p2/dependencies.lock`` is the resulting environment
snapshot; its old ``BLOCKED_missing`` values describe that container only.

Current disposition of the old blockers
---------------------------------------

The maintained macOS workflow now uses ``tools/p2/bootstrap-local.sh`` and the
hash-pinned ``tools/p2/toolchain.lock``.  The port links native P2 ELFs and has
real startup, upward-stack context switching, INT1/CT1 timer delivery, serial,
board lower halves, storage arbitration, and a PSRAM service cog.  The old
claims that p2llvm was absent, Kconfig could not run, or all target images were
unlinked are therefore obsolete.

Representative completed evidence is:

* 1,000,000 native context switches;
* 100/100 NuttX bring-up and 50/50 NSH cycles;
* 50/50 GPIO/edge/UART/PWM-capture/SPI cycles;
* complete destructive flash and microSD campaigns;
* 20/20 DAC/ADC and 20/20 BMP180 I2C cycles;
* 20/20 independent ROM flash boots;
* two consecutive full 32-MiB PSRAM write/read runs; and
* a 600-sample host-referenced raw GETCT campaign spanning a conservative
  600.555632 seconds.

The exact artifact paths and limitations are maintained in
:doc:`context-frame`, :doc:`interrupts`, :doc:`smartpins`,
:doc:`storage-arbitration`, :doc:`psram-service`, and :doc:`hil-handoff`.

Still-open boundaries
---------------------

The working UP port does not imply SMP, protected builds, or general interrupt
routing.  P24/P25 I2C with the fixed-address BMP180 and the
resistive/capacitive P4/P5 DAC/ADC fixture now have physical HIL evidence.
True power-loss recovery remains open.  That is a current evidence gap, not
an original cloud-tool availability failure.
