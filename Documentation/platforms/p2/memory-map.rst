Memory map
==========

Status: DRAFTED.

P2 Hub RAM is 512 KiB and is the only memory considered for NuttX code, data, heap, and stacks. External 32 MiB PSRAM is not normal RAM. The linker script and final memory report remain BLOCKED until p2llvm link command details are finalized.
