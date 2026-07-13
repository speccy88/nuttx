Context frame
=============

Status: **HIL-VERIFIED** for the current flat, uniprocessor port.

``arch/p2/include/context.h`` is the shared C/PASM2 source of truth.  A public
saved context is 38 32-bit words (152 bytes):

.. list-table:: Public ``xcptcontext`` register layout
   :header-rows: 1

   * - Words
     - Byte offsets
     - Contents
   * - 0-31
     - 0-124
     - ``r0`` through ``r31``
   * - 32
     - 128
     - ``PA``
   * - 33
     - 132
     - ``PB``
   * - 34
     - 136
     - Logical post-resume ``PTRA``
   * - 35
     - 140
     - ``PTRB``
   * - 36
     - 144
     - Interrupt state; bit 1 records ``STALLI``
   * - 37
     - 148
     - Packed C, Z, and 20-bit resume PC

The hardware scratch frame is deliberately ordered as the packed resume word
followed by the 37 register words.  The public TCB buffer remains register
first so element zero is ``r0``.  The C dispatcher translates explicitly
between the two layouts.  ``PTRA`` records the logical value after resume;
the common ``RETI1`` path subtracts one long immediately before returning.
Interrupt entry never borrows ``[PTRA - 4]`` because compiler-generated
outgoing arguments may still occupy that location.

Static assertions in ``context.h``, ``irq.h``, and
``arch/p2/src/common/p2_asserts.c`` enforce the offsets and total size.  The
native CT1 proof in ``artifacts/hil/20260713T034110.407118Z-context`` completed
1,000,000 switches with register, stack, nested-spill, variadic, 64-bit
arithmetic, and interrupt-canary checks.  This evidence does not extend the
frame contract to SMP or protected/kernel builds; both remain unsupported.
