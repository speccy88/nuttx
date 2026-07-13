Phase 11 HIL wiring
===================

Status: direct digital jumpers P0/P1, P2/P3, and P6/P7 were reported installed
and have completed the 50-cycle Smart Pin campaign.  The user subsequently
replaced P4/P5 with the requested resistive/capacitive analog fixture.  A
BMP180 is installed with P24 as SDA and P25 as SCL.  Both new fixtures are now
HIL-verified.

The installed analog path is P4--series resistor--P5 with a capacitor from P5
to ground, replacing the former direct jumper.  P5 remains input-only; no
divider or external voltage is needed.  Exact values requested for this
fixture were 1 kohm in series and 100 nF to ground.

Safety rules
------------

* Never connect two push-pull outputs.  Claim and configure the receiving pin
  as an input before enabling the source output.
* Use one common ground and keep every signal in the P2 0-V to 3.3-V domain.
* Power off before changing a link.  Series resistors are recommended for a
  future fixture revision.
* On every error path, disable the Smart Pin engine and leave all participating
  pins floating as ``P2_PIN_SAFE_FLOAT``.
* I2C pins must be open-drain.  Check the live idle levels and effective 3.3-V
  pull-ups before enabling the controller; do not assume the BMP180 module
  supplies suitable pull-ups.

Installed loopback fixtures
---------------------------

.. list-table:: Current loopback fixture
   :header-rows: 1

   * - Physical connection
     - Reported wiring
     - Enabled HIL role
     - Constraint
   * - P0 to P1
     - Direct jumper
     - P0 GPIO output to P1 input and sampled edges
     - Configure P1 first
   * - P2 to P3
     - Direct jumper
     - P2 configurable UART TX to P3 RX
     - Configure P3 first
   * - P4 to P5
     - 1 kohm series; 100 nF from P5 to ground
     - P4 DAC to P5 ADC
     - Configure P5 first; 20/20 analog cycles passed
   * - P6 to P7
     - Direct jumper
     - P6 SPI MOSI to P7 MISO
     - Configure P7 first
   * - P8 and P9
     - No external connection reported
     - SPI SCK and chip select outputs
     - Both float after deselect

The accepted digital ``smartpins`` artifact predates the P4/P5 fixture
replacement and exercised GPIO, sampled GPIO edges, configurable UART,
direct-jumper digital PWM/capture, and mode-0 SPI at a requested 100 kHz.  Its
evidence is ``artifacts/hil/20260713T063221.439668Z-smartpins``.  The
subsequent analog artifact
``artifacts/hil/20260713T110743.191438Z-smartpins`` passed 20/20 cycles and all
60 monotonic DAC-to-ADC samples with safe float after each cycle.

Installed BMP180 fixture
------------------------

.. list-table:: Reported I2C fixture
   :header-rows: 1

   * - Signal
     - P2 pin
     - Status
   * - BMP180 SDA
     - P24
     - ``/dev/i2c0`` SDA; 20/20 live cycles passed
   * - BMP180 SCL
     - P25
     - ``/dev/i2c0`` SCL; 20/20 live cycles passed

The lower half uses open-drain P24/P25 and registers ``/dev/i2c0``.  Its BMP180
binding verified address ``0x77`` and chip ID ``0x55``, then registered
``/dev/press0``.  The physical campaign at
``artifacts/hil/20260713T111043.745628Z-i2c`` passed 20/20 cycles with true
repeated-start reads, 640 pressure readings from 100000 through 100019 Pa, and
zero bus-recovery pulses.

Console and onboard-bus isolation
---------------------------------

P62/P63 remain dedicated to the programming/console adapter.  Storage pins
P58-P61 and PSRAM pins P40-P57 are also outside the loopback fixture.  None of
the temporary links or BMP180 signals may be moved onto those reserved pins.
