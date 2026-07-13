SMP evaluation
==============

Status: **DEFERRED / OUT OF SCOPE** for the current completion goal.  The
supported configuration is a stable flat **UP** kernel on cog 0 plus
deterministic non-scheduler service cogs.  NuttX SMP remains unsupported and
no SMP defconfig is provided, but it does not gate flat-UP acceptance.

The distinction is important.  The console RX worker and PSRAM engine may run
on additional cogs, but they do not execute NuttX tasks, own scheduler state,
or make the kernel an SMP build.  Their Hub protocols have explicit producer,
consumer, lock, timeout, and pin-ownership rules.

The current interrupt/context implementation deliberately rejects
``CONFIG_SMP`` at compile time.  It has one ``g_current_regs`` value, indexes
``g_running_tasks[0]``, owns one fixed interrupt frame and stack, routes CT1 to
INT1 on cog 0, and provides no secondary scheduler-cog startup.  The
architecture Kconfig also does not select ``ARCH_HAVE_MULTICPU`` or
``ARCH_HAVE_TESTSET``.

A real SMP port would require, at minimum:

* secondary-cog boot, idle TCBs, TLS, and per-CPU interrupt stacks;
* per-cog current-register and running-task state;
* interprocessor interrupts and scheduler attention events;
* hardware-lock-backed test-and-set/spinlock semantics and memory barriers;
* timer ownership, task migration, affinity, and cross-cog wakeups; and
* failure-safe coordination with console, storage, and PSRAM service cogs.

Until those pieces and an SMP-specific HIL campaign exist, enabling
``CONFIG_SMP`` is not an experiment supported by this port.  The maintained
architecture is UP plus bounded service cogs.  Any SMP implementation and
qualification is a separate future project, not unfinished work in this goal.
