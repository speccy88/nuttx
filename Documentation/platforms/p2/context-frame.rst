Context frame
=============

Status: DRAFTED.

The draft frame saves r0-r31 at offsets 0..124, PTRA at 128, PC at 132, C/Z state at 136, and interrupt-return state at 140. The total frame is 36 32-bit words. Static assertions in ``arch/p2/src/common/p2_asserts.c`` check these offsets. Interrupt nesting policy is initially disabled until the first entry path is proven on hardware.
