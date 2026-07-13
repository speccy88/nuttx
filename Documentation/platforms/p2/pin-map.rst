P2-EC32MB pin map
=================

Status: board reservations are **STATICALLY-VERIFIED** against the current
board definition; external electrical behavior is **HIL-REQUIRED**.

.. list-table:: Board pin ownership
   :header-rows: 1

   * - Pins
     - Default role
     - Availability
   * - P0-P37
     - Application Smart Pins
     - Free until claimed
   * - P38-P39
     - Buffered onboard LEDs
     - Reserved only with ``CONFIG_ARCH_LEDS``
   * - P40-P57
     - Four PSRAM data banks plus common clock/select
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

The installed Phase 11 fixture uses direct jumpers on four free adjacent
pairs: P0/P1, P2/P3, P4/P5, and P6/P7.  The safe enabled roles and the analog
and SPI tests which must remain disabled are in :doc:`hil-wiring`.
