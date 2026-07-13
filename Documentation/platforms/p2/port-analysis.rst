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

Hardware evidence includes 100/100 kernel bring-up cycles, 50/50 NSH cycles,
50/50 digital Smart Pin cycles, 20/20 DAC/ADC cycles, 20/20 BMP180 I2C cycles,
full flash and SD campaigns, 20/20 independent ROM flash boots, two consecutive
complete 32-MiB PSRAM runs, the standalone 1,000,000-switch context proof, and
a 600-sample host-referenced raw GETCT campaign spanning a conservative
600.555632 seconds.  Evidence paths and their limits are documented in the
subsystem pages rather than being inferred from successful compilation.

Unsupported or incomplete areas
-------------------------------

* SMP, protected/kernel builds, nested interrupt routing, and non-timer
  architecture IRQ sources are not implemented.
* Reset-interrupted flash recovery is verified; true power-loss recovery is
  not, because no controlled power-cycle command is available.

Those boundaries remain explicit unsupported, deferred, or ``HIL-REQUIRED``
items; they are not implied successes of the working UP port.  SMP is
**DEFERRED / OUT OF SCOPE** and does not gate completion of the accepted
flat-UP configuration.
