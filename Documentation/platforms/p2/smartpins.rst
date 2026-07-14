Smart Pin support
=================

Status
------

The production C pin manager is **HOST-TESTED**.  GPIO, GPIO edge sampling,
configurable UART, PWM/capture, and general-purpose SPI are **HIL-VERIFIED**
for the digital fixture used in that campaign.  ADC and DAC are also
**HIL-VERIFIED** on the resistive/capacitive P4/P5 fixture.  General-purpose
I2C is **HIL-VERIFIED** for P24/P25 and the installed BMP180.

Single source of truth
----------------------

``p2_ec32mb_pins.c`` is the only ownership implementation.  Host tests compile
that target C file directly; there is no second Python ownership model.

Each of the 64 records contains the physical pin, board-reserved role, current
owner, reference count, direction, Smart Pin mode, drive/pull state, event
selector, owning cog, and final-release safe state.  The manager uses a P2
hardware lock so the Hub-RAM records remain coherent between the scheduler
and service cogs.

Claims have these fail-closed rules:

* A reserved pin accepts only the owner corresponding to its reserved role.
* The first claim records both owner and cog.
* A repeated claim increments the reference count only for that same owner
  and cog.  Overflow returns ``-EOVERFLOW``.
* Another owner or cog receives ``-EBUSY``.
* Configure and release by a non-owner return ``-EPERM``.
* The last release stops the Smart Pin and applies the recorded safe state
  before clearing ownership.  The default safe state is a floating input.
* Event selectors SE1 through SE4 are unique per owning cog.  An allocation
  conflict returns ``-EBUSY``.

Board reservations
------------------

On P2-EC32MB Rev B, P40-P57 are reserved for PSRAM, P58-P61 for storage, and
P62-P63 for the console.  P38-P39 are reserved for the buffered board LEDs
when either ``CONFIG_ARCH_LEDS`` or ``CONFIG_USERLED`` is enabled.  P0-P37
remain dynamically claimable.

P2-EC Rev D has no PSRAM, so P40-P55 remain available for configured lower
halves.  P56-P57 are instead its active-high LEDs and are reserved when either
LED option is enabled; storage and console retain P58-P61 and P62-P63.  That
mapping is compiled, statically verified, and physically HIL-verified by the
exact Rev D showcase.  See :doc:`pin-map` for the installed fixture
allocations and the complete board distinction.

Standard device interfaces
--------------------------

``CONFIG_P2_EC32MB_GPIO`` registers configured physical pins through the
standard NuttX GPIO upper half.  In the HIL profile P0 is ``/dev/gpio0`` and
P1 is ``/dev/gpio1``.  Supported types are floating input, 15-kohm pull-up or
pull-down input, push-pull output, and open-drain output.  Switching pin type
goes through the owner record before touching WRPIN, OUT, or DIR.

GPIO interrupt pin types use the normal attach, enable, signal-registration,
and mask operations.  CT1 currently owns the only complete architecture
interrupt channel, so GPIO transitions are sampled by
``CONFIG_SYSTEMTICK_HOOK`` at 100 Hz.  A callback is generated only after a
real input-level change.  This is a low-rate fallback, not hardware-rate edge
capture.  Debounce remains ``-ENOSYS``.

``CONFIG_P2_EC32MB_UART1`` registers ``/dev/ttyS1`` on P2/P3.  It supports
8-N-1 termios baud changes from 1,200 through 1,000,000 baud.  The receiver is
configured before the transmitter is enabled.  TX completion is bounded by
the hardware busy indication; looped-back RX is drained after each word and
independent RX is sampled by the system-tick hook.

``CONFIG_P2_EC32MB_PWM`` registers ``/dev/pwm0`` on P4.  Standard PWM
frequency and unsigned b16 duty are translated to the P2 sawtooth frame and
base-period fields.  Frequencies outside the two 16-bit hardware fields fail
with ``-ERANGE``.

``CONFIG_P2_EC32MB_CAPTURE`` registers ``/dev/cap0`` on P5.  The standard
``CAPIOC_DUTYCYCLE``, ``CAPIOC_FREQUENCY``, ``CAPIOC_EDGES``, and
``CAPIOC_ALL`` operations use P2 period-time, period-state, and continuous
rising-edge counter modes.  ``CAPIOC_PULSES`` and ``CAPIOC_CLR_CNT`` are also
supported.  Synchronous measurements have a one-second timeout.

``CONFIG_P2_EC32MB_SPI`` registers a bit-banged ``/dev/spi0``.  The verified
profile uses mode 0 at a requested 100 kHz: P6 MOSI is directly connected to
P7 MISO, while P8 SCK and P9 chip select are deliberately unconnected.  The
input is claimed before any output is enabled, and MOSI, MISO, SCK, and CS all
float after deselect.  This proves the controller loopback path, not operation
with an external SPI peripheral.

Analog and I2C boundaries
-------------------------

``CONFIG_P2_EC32MB_ADC`` registers raw, uncalibrated SINC2 accumulator samples
as ``/dev/adc0`` on P5.  ``CONFIG_P2_EC32MB_DAC`` registers ``/dev/dac0`` on
P4 using the 990-ohm, 3.3-V, 16-bit PWM-dithered DAC mode.  P4/P5 are shared
dynamically: PWM conflicts with DAC and capture conflicts with ADC.  The
original P4/P5 direct jumper was used only for the verified one-source digital
PWM/capture test.  It has now been replaced with the requested series resistor
and P5-to-ground capacitor.  The separate analog profile passed 20/20 cycles.
The current candidate also passed a bounded ``/dev/pwm0`` open/start/stop
smoke while driving that RC load.  This is an RC-safe device-control check,
not digital waveform or capture qualification; the latter remains historical
evidence from the direct-jumper fixture.

The board I2C implementation claims P24 as SDA and P25 as SCL through the
central pin manager and exposes the NuttX bit-bang bus as ``/dev/i2c0``.  Both
lines use open-drain drive; high is an external pull-up responsibility.  The
optional BMP180 binding uses its fixed 7-bit address ``0x77``, requires chip ID
``0x55``, and exposes the legacy pressure interface as ``/dev/press0``.

The physical I2C campaign verified the installed BMP180 at address ``0x77``
and ID ``0x55`` using a true write/NOSTOP repeated-start read.  It completed
640 pressure reads and did not need a bus-recovery pulse.

HIL evidence
------------

``artifacts/hil/20260713T063221.439668Z-smartpins`` completed 50/50 reset/load
cycles on the historical direct-jumper fixture.  Every cycle verified the
GPIO pattern, six sampled edges, a 16-byte UART record, 1-kHz PWM at
25/50/75 percent duty, and a 16-byte SPI loopback; each stage emitted its
safe-float marker.  DAC/ADC and I2C were not part of that campaign.  A
subsequent 20-cycle analog campaign at
``artifacts/hil/20260713T110743.191438Z-smartpins`` produced strictly
increasing ADC values at all three DAC codes and floated both pins after every
cycle.  The separate I2C campaign
``artifacts/hil/20260713T111043.745628Z-i2c`` passed 20/20 cycles, including
true repeated starts, 640 pressure readings from 100000 through 100019 Pa, and
zero recovery pulses.

The current NuttX ``14cadad3a6794e10cbc9f0dfb20f352e4844d35f`` / apps
``a333035462f545056e7a2fb859a9fbdc6d4ef831`` Rev-B RAM showcase is **PASS**
at ``/tmp/p2-release-final.14cadad-r1/ec32mb-showcase-hil``.  It reran GPIO,
edge, UART, ADC/DAC, SPI, BMP180 I2C, and the RC-safe PWM smoke without
claiming a digital P4/P5 waveform.  All 16 required showcase stages passed in
379.246116 seconds; the status SHA-256 is
``2ce85939d560a2e727b845d1e87f758939dd6028ce6b6afaba1bcc1c031e8250``.
This ``/tmp`` evidence is provisional and must be preserved in or linked from
the release package.  P2-EC Rev D has no PSRAM, uses P56/P57 for LEDs, and its
exact showcase passed the same installed GPIO, edge, UART, ADC/DAC, PWM-smoke,
SPI, and I2C fixtures at
``/tmp/p2-revd-final.14cadad-r1/revd-showcase-hil-pass``.
