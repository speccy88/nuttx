p2llvm ABI draft
================

Status: DRAFTED, HIL-REQUIRED.

The ABI working assumption is 32-bit registers r0-r31, PTRA as compiler stack pointer, upward-growing stack, and PC/condition state saved explicitly by interrupt entry. ``arch/p2/include/context.h`` is the single source for frame indices. ``tools/p2/run-abi-probes.sh`` is a placeholder for compiling probes with pinned p2llvm. Actual compiler output must be reconciled before removing DRAFTED status.
