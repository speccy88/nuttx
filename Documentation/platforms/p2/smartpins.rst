Smart Pin support
=================

Status: DRAFTED.

The central pin manager tracks owner/reference state and rejects conflicts with PSRAM, storage, console, and LED reservations. The first standard API target is GPIO. Electrical modes, event timing, PWM/capture/ADC/DAC/I2C/SPI waveforms, and Smart Pin interrupts are HIL-REQUIRED.
