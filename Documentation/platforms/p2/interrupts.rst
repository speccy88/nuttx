Interrupts and timer
====================

Status: DRAFTED, HIL-REQUIRED.

The intended design saves the complete compiler-visible state before using C registers, records the prior interrupt enable state, dispatches via ``irq_dispatch()``, and restores the selected TCB. The timer uses the P2 free-running counter with wrap-safe subtraction; pure arithmetic is HOST-TESTED.
