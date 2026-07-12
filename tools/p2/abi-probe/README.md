# P2 ABI probe sources

These files are compile-time and link-time probes for the pinned p2llvm
backend.  They are not runnable tests and make no claim about hardware
behavior.  `../run-abi-probes.sh` builds every required C probe at `-O0`,
`-Os`, and `-O2`, archives exact commands and diagnostics, emits object and
linked ELF inspection reports, and does not link p2llvm libc or libp2.

`block_context.S` only tests whether the assembler can encode a 32-long
`setq`/`rdlong`/`wrlong` block transfer.  It is deliberately not presented as
a complete or validated interrupt-context implementation: PTRA and C/Z need
separate, carefully ordered handling, and only HIL can establish correctness.

The 64-bit divide/modulo and atomic load/store/compare-exchange probes are
separately classified because this backend may explicitly reject them or
report that no atomic width is lock-free.  An unexpected compiler failure is
still fatal; only a known unsupported-backend diagnostic is accepted.
