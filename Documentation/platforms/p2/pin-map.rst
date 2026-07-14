P2 Edge module pin maps
=======================

Status: board reservations are **STATICALLY-VERIFIED** against the production
pin manager.  The installed digital fixture is **HIL-VERIFIED**; the BMP180
and analog fixtures are also **HIL-VERIFIED**.

.. list-table:: Board pin ownership
   :header-rows: 1

   * - Pins
     - Default role
     - Availability
   * - P0-P37
     - Application Smart Pins
     - Free until claimed by a configured lower half
   * - P38-P39
     - Buffered onboard LEDs
     - Reserved with ``CONFIG_ARCH_LEDS`` or ``CONFIG_USERLED``
   * - P40-P55
     - Four PSRAM QPI data banks
     - Always reserved for PSRAM
   * - P56
     - Common PSRAM clock
     - Always reserved for PSRAM
   * - P57
     - Common PSRAM chip enable
     - Always reserved for PSRAM
   * - P58-P59
     - Shared flash/microSD MISO/MOSI
     - Always reserved for storage
   * - P60-P61
     - Shared flash/microSD clock/select
     - Always reserved for storage
   * - P62-P63
     - Console TX/RX
     - Always reserved for console

``p2_pin_reserved_role()`` is the authoritative reservation policy.  Fixture
assignments below are deliberately not compiled in as permanent board
reservations, so a configuration must still claim them through the central
manager.

P2-EC Rev D differs deliberately: it has no PSRAM, so P40-P55 are not reserved
for PSRAM.  P56/P57 are its active-high board LEDs and are reserved only when
``CONFIG_ARCH_LEDS`` or ``CONFIG_USERLED`` is enabled.  P58-P61 remain storage
and P62/P63 remain the console.  The exact Rev D release passed physical HIL:
P56/P57 LED-device control, the installed Smart Pin fixtures, BMP180 I2C,
storage probe, and explicit absence of ``/dev/psram0``.

.. list-table:: Installed and reported HIL fixture
   :header-rows: 1

   * - Pins
     - Connection or device
     - Current status
   * - P0/P1
     - Direct GPIO and edge loopback
     - 50/50 HIL cycles passed
   * - P2/P3
     - Direct configurable-UART loopback
     - 50/50 HIL cycles passed
   * - P4/P5
     - Current resistive/capacitive DAC-to-ADC fixture
     - Current fixture: DAC/ADC and RC-safe PWM smoke passed; no digital
       waveform/capture qualification
   * - P6/P7
     - Direct SPI MOSI-to-MISO loopback
     - 50/50 HIL cycles passed
   * - P8/P9
     - SPI SCK/chip-select outputs, left externally unconnected
     - Used by the verified mode-0 loopback
   * - P24/P25
     - BMP180 SDA/SCL respectively
     - Board I2C and BMP180 path passed 20/20 live cycles

The board contract assigns P24 to SDA and P25 to SCL.  The implemented
open-drain bit-bang lower half registers ``/dev/i2c0``; the optional BMP180
binding expects fixed address ``0x77``, verifies chip ID ``0x55``, and
registers ``/dev/press0``.  The live artifact
``artifacts/hil/20260713T111043.745628Z-i2c`` verified that contract over 20/20
cycles, including true repeated-start transfers and 640 pressure reads.  The
fixture still depends on effective external 3.3-V pull-ups.  Detailed
drive-order and direct-jumper constraints are in :doc:`hil-wiring`.

The older 50/50 Smart Pin campaign used a direct P4--P5 jumper and qualified
digital PWM/capture waveforms for that historical fixture.  The installed
fixture is now the series resistor from P4 to P5 plus the P5-to-ground
capacitor.  It passed the 20/20 DAC/ADC campaign and the current candidate's
bounded ``/dev/pwm0`` open/start/stop smoke.  That RC-safe smoke proves device
open/control/stop behavior only; it is not a current digital waveform or
capture measurement.
