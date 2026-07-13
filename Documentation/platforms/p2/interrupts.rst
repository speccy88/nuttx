Interrupts and timer
====================

Status: the CT1 timer, complete context save/restore, and flat-UP scheduler
handoff are **HIL-VERIFIED**.  Additional interrupt sources and nesting are
**BLOCKED** on architecture implementation.  SMP is **DEFERRED / OUT OF
SCOPE** for the current goal; ``CONFIG_SMP`` remains unsupported but does not
gate flat-UP completion.

The current port routes CT1 to cog interrupt channel INT1 and dispatches it as
``P2_IRQ_TIMER0``.  No other event-to-channel mapping is implemented:
``up_enable_irq()`` and ``up_disable_irq()`` fail closed for every other
vector, while interrupt priority and trigger-type operations return
``-ENOSYS``.  Logical vector slots 2 through 15 are reservations, not working
interrupt-controller inputs.

INT1 entry writes the packed ``IRET1`` resume word and every compiler-visible
register to fixed, guarded Hub scratch before using C registers.  It then
switches to a separate guarded 512-long interrupt stack, calls
``irq_dispatch()``, records any scheduler-selected TCB, and restores through
one common ``RETI1`` veneer.  Thread-mode context restore deliberately
triggers INT1 so the same busy-channel return path is used.  The implementation
is compile-time rejected for ``CONFIG_SMP`` and non-flat builds.

The system tick reads the free-running counter with ``GETCT`` and programs CT1
from an absolute deadline.  Each ISR advances that deadline by the configured
tick interval before calling ``nxsched_process_timer()``, preserving phase
instead of accumulating handler latency.  Tick conversion arithmetic is
host-tested.

Hardware evidence includes the 1,000,000-switch native context run in
``artifacts/hil/20260713T034110.407118Z-context`` and 100/100 NuttX bring-up
cycles in ``artifacts/hil/20260713T034525.287219Z-bringup``.  The 50-cycle NSH
campaign also exercised ``sleep 1`` successfully, but that is scheduler smoke
evidence only.  Dedicated raw-counter HIL passed in
``artifacts/hil/20260713T114543.089052Z-clock`` with 600 ordered samples over
a conservative 600.555632-second host-monotonic span.  Its midpoint estimate
was 180,002,153.6455524 Hz (+11.9647 ppm), with conservative bounds of
179,998,600.271539--180,005,707.159862 Hz (-7.776--+31.706 ppm).  These are
host-bracketed measurements under a broad structural gate, not a declared
oscillator-accuracy tolerance across temperature, voltage, or aging.
