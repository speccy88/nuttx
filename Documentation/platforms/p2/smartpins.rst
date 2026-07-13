Smart Pin support
=================

Status
------

The target C pin manager and its host behavior tests are **HOST-TESTED**.
GPIO, configurable UART, PWM, capture/counter, ADC, and DAC lower halves are
**COMPILED** as isolated target objects.  The integrated ``smartpins`` image
and all electrical behavior remain **HIL-REQUIRED**.

Single source of truth
----------------------

``p2_ec32mb_pins.c`` is the only ownership implementation.  Host tests compile
that target C file directly; there is no second Python ownership model.

Each of the 64 records contains the physical pin, board-reserved role, current
owner, reference count, direction, Smart Pin mode, drive/pull state, event
selector, owning cog, and final-release safe state.  The manager allocates a P2
hardware lock so the Hub-RAM records remain coherent when service cogs are
added.

Claims have these fail-closed rules:

* A reserved pin accepts only the owner corresponding to its reserved role.
* The first claim records both owner and cog.
* A repeated claim increments the reference count only for that same owner and
  cog.  Overflow returns ``-EOVERFLOW``.
* Another owner or cog receives ``-EBUSY``.
* Configure and release by a non-owner return ``-EPERM``.
* The last release stops the Smart Pin and applies the recorded safe state
  before clearing ownership.  The default safe state is a floating input.
* Event selectors SE1 through SE4 are unique per owning cog.  An allocation
  conflict returns ``-EBUSY``.

Board reservations
------------------

P40-P57 are reserved for PSRAM, P58-P61 for storage, and P62-P63 for the
console.  P38-P39 are reserved for the buffered board LEDs only when
``CONFIG_ARCH_LEDS`` is enabled.

Standard device interfaces
--------------------------

``CONFIG_P2_EC32MB_GPIO`` registers the configured physical pins through the
standard NuttX GPIO upper half.  With the default HIL fixture, P0 is
``/dev/gpio0`` and P1 is ``/dev/gpio1``.  Supported pin types are floating
input, 15-kohm pull-up input, 15-kohm pull-down input, push-pull output, and
open-drain output.  Switching pin type goes through the central owner record
before touching WRPIN, OUT, or DIR.

GPIO interrupt pin types use the normal attach, enable, signal-registration,
and mask operations.  CT1 currently owns the only architecture interrupt
channel with a complete NuttX context save, so GPIO edges are sampled by
``CONFIG_SYSTEMTICK_HOOK`` at 100 Hz.  The callback is generated only after a
real input-level transition is observed.  This is a useful low-rate fallback,
not a claim of hardware-rate edge capture.  Debounce remains ``-ENOSYS``.

``CONFIG_P2_EC32MB_UART1`` registers ``/dev/ttyS1`` on P2/P3.  It supports
8-N-1 termios baud changes from 1,200 through 1,000,000 baud.  The Smart Pin
receiver is configured before the transmitter is enabled.  TX completion is
bounded by the hardware busy indication; looped-back RX is drained after each
word and independent RX is sampled by the system-tick hook.

``CONFIG_P2_EC32MB_PWM`` registers ``/dev/pwm0`` on P4.  The standard PWM
frequency and unsigned b16 duty are translated to the P2 sawtooth frame and
base-period fields.  Frequencies outside the two 16-bit hardware fields fail
with ``-ERANGE``.

``CONFIG_P2_EC32MB_CAPTURE`` registers ``/dev/cap0`` on P5.  The standard
``CAPIOC_DUTYCYCLE``, ``CAPIOC_FREQUENCY``, ``CAPIOC_EDGES``, and
``CAPIOC_ALL`` calls use the P2 period-time, period-state, and continuous
rising-edge counter modes.  ``CAPIOC_PULSES`` and ``CAPIOC_CLR_CNT`` are also
supported.  Synchronous measurements have a one-second timeout and are
intended for stable periodic signals.

``CONFIG_P2_EC32MB_ADC`` registers the internally clocked SINC2 sampler as
``/dev/adc0`` on P5.  ``ANIOC_TRIGGER`` returns raw, uncalibrated accumulator
values and ``ANIOC_GET_NCHANNELS`` returns one.  ``CONFIG_P2_EC32MB_DAC``
registers ``/dev/dac0`` on P4 using the 990-ohm, 3.3-V, 16-bit PWM-dithered
DAC mode.  These lower halves may be present in the image, but their direct
P4/P5 HIL stage is disabled for the installed jumper fixture; see
:doc:`hil-wiring`.

P4/P5 are shared dynamically.  Opening PWM conflicts with an open DAC, and
opening capture conflicts with an open ADC.  The central pin manager returns
``-EBUSY`` rather than allowing two lower halves to configure the same pin.
Closing the owner disables its Smart Pin and leaves the physical pin floating.

General-purpose SPI and I2C host lower halves are not implemented.  SPI remains
**BLOCKED** on allocating separate clock and chip-select pins in addition to
the installed P6/P7 data jumper.  I2C remains **BLOCKED** on identifying a real
responding 3.3-V device and its pulled-up SDA/SCL pair.
