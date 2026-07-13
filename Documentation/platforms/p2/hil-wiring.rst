Phase 11 HIL wiring
===================

Status: the four pin pairs were reported installed as direct jumper shorts on
2026-07-12.  No series resistor was reported.  Only a one-source/one-input
digital test may drive these links.  The DAC/ADC stage must remain disabled
until the P4/P5 link is replaced by, or verified to contain, suitable analog
series resistance.

Safety rules
------------

* Never connect two push-pull outputs.  The driver must claim and configure
  the receiving pin as an input before enabling the source output.
* Use one common ground between the P2 board and any instrument.
* Keep every signal in the P2 0-V to 3.3-V domain.
* Power off before changing a link.  A series resistor is still recommended
  for every future fixture revision.
* On every error path, disable the Smart Pin engine and leave both pins
  floating.  The central pin manager records this as ``P2_PIN_SAFE_FLOAT``.

Installed fixture allocation
----------------------------

.. list-table:: Fixed Phase 11 loopback fixture
   :header-rows: 1

   * - Physical connection
     - Reported wiring
     - Enabled HIL roles
     - Disabled or incomplete roles
   * - P0 to P1
     - Direct jumper
     - P0 GPIO output to P1 GPIO input/edge input
     - None, provided P1 is configured first
   * - P2 to P3
     - Direct jumper
     - P2 configurable UART TX to P3 UART RX
     - None, provided P3 is configured first
   * - P4 to P5
     - Direct jumper
     - P4 digital PWM output to P5 capture input
     - DAC/ADC HIL is disabled without analog series resistance
   * - P6 to P7
     - Direct jumper
     - None
     - SPI needs separately allocated clock and chip-select pins

The ``smartpins`` configuration exercises P0/P1, P2/P3, and digital P4/P5.
It compiles the ADC and DAC lower halves, but
``CONFIG_TESTING_P2SMARTPINS_DAC_ADC`` remains off.  A later analog fixture
may reuse P4/P5 only after the direct jumper is replaced and the prior digital
owner is closed.  P6/P7 are only a potential unidirectional SPI data link;
clock and chip select remain separate controller signals and must not be
shorted to each other.

I2C is not a loopback test.  A future I2C fixture needs a real responding
peripheral and open-drain SDA/SCL lines with 3.3-V pull-ups.  No I2C pins are
allocated until that device is identified.

Console isolation
-----------------

P62/P63 remain dedicated to the programming/console adapter.  None of the
Phase 11 links may be moved onto those pins.  Storage pins P58-P61 and PSRAM
pins P40-P57 are also outside the loopback fixture.
