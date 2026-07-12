P2 port analysis
================

Status: DRAFTED.

Inspected current tree patterns: architecture selection is rooted in ``arch/Kconfig`` and board selection in ``boards/Kconfig``.  Reference ports inspected include OpenRISC for a small flat 32-bit port, Xtensa for LLVM/Clang-relevant integration, and RISC-V/Xtensa for SMP-capable interfaces.  Current required hooks include startup into ``nx_start()``, ``up_initial_state()``, ``up_switch_context()``, ``up_fullcontextrestore()``, interrupt save/restore, timer initialization, heap allocation, stack helpers, low-level putc, and board initialization.

Stack-direction audit: generic NuttX has many downward-stack assumptions in arch ports; P2 p2llvm uses PTRA upward stack per ABI documentation and must be mechanically probed before runtime. This draft records upward-stack requirements but does not claim the scheduler has been HIL-validated.

Storage APIs inspected: SPI uses ``struct spi_dev_s`` and W25 binding is ``w25_initialize()``; MMC/SD SPI binding is ``mmcsd_spislotinitialize()``; GPIO upper half is ``include/nuttx/ioexpander/gpio.h``. Smart Pin is P2-specific and should map to standard GPIO/PWM/capture/ADC/DAC/I2C/SPI lower halves.
