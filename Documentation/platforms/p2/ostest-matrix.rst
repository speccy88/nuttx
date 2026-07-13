P2 ``ostest`` matrix
=====================

Status: DRAFTED, HIL-REQUIRED.

Scope and profiles
------------------

This matrix is derived from the pinned ``../apps/testing/ostest`` source tree.
The baseline is a flat, single-CPU P2 image whose entry point is
``ostest_main``; it does not contain NSH.  Four checked-in profiles make the
two required logical matrices and their run modes explicit:

* ``ostest-pi-assert`` and ``ostest-pi-production`` enable priority
  inheritance and require the priority-inheritance test.  The upstream
  condition-variable test emits its documented incompatibility skip.
* ``ostest-cond-assert`` and ``ostest-cond-production`` disable priority
  inheritance and require ``cond_test()`` to reach its zero-error summary.

The assertion profiles enable ``CONFIG_DEBUG_ASSERTIONS``.  Production
profiles disable it and retain ``CONFIG_DEBUG_FULLOPT``.  Otherwise the four
profiles enable the same parent/child status and ``waitpid``, full signals,
POSIX timers and message queues, pthread cancellation points and cleanup
handlers, recursive and robust mutexes, named semaphores, round-robin and
sporadic scheduling, work queues, high-resolution timers, scheduler events,
and stack coloration.

This split is necessary because upstream ``ostest_main.c`` deliberately skips
``cond_test()`` whenever ``CONFIG_PRIORITY_INHERITANCE`` is enabled.  It is a
test-logic split, not a workaround for a P2 failure.  Further size-based
splits are permitted only after a linker map proves a profile cannot fit.  In
the table, the shorthand ``ostest`` means every profile for which the row is
applicable.

``STACK_USAGE`` retains compiler stack-usage reports, while
``STACK_COLORATION`` and the P2 ``up_check_tcbstack()`` implementation enable
the runtime high-water checks.  Static ``.su`` files are supplementary and
are not accepted as a substitute for the required runtime coloration run.

The AIO and multi-user groups need filesystem or credential fixtures not
provided by this direct-entry scheduler image.  They remain explicitly
conditional below; if those features are added to the P2 test profile, their
rows become mandatory and should live in a fixture-bearing image rather than
being silently skipped.

Test inventory
--------------

``HIL-REQUIRED`` means that compilation or host execution is not a result.
``N/A`` is reserved for a capability the P2 architecture does not expose.
The expected marker is the entry marker emitted by ``ostest_main.c``.  The
parser also requires the following entry marker (or the suite-final marker),
which proves that the group returned.

.. list-table::
   :header-rows: 1
   :widths: 17 15 22 12 12 22 10 10

   * - Test
     - Source
     - Required configuration
     - Applicability
     - Image
     - Expected marker
     - Hardware result
     - Log
   * - Standard I/O
     - ``ostest_main.c``
     - console and file descriptors 1/2
     - Required
     - ``ostest``
     - ``stdio_test: Standard I/O Check``
     - HIL-REQUIRED
     - --
   * - Task create and arguments
     - ``ostest_main.c``
     - flat build
     - Required
     - ``ostest``
     - ``user_main: Begin argument test``
     - HIL-REQUIRED
     - --
   * - Environment
     - ``ostest_main.c``
     - ``!DISABLE_ENVIRON``
     - Required
     - ``ostest``
     - ``ostest_main: putenv``
     - HIL-REQUIRED
     - --
   * - getopt family
     - ``getopt.c``
     - always
     - Required
     - ``ostest``
     - ``user_main: getopt() test``
     - HIL-REQUIRED
     - --
   * - libc memmem
     - ``libc_memmem.c``
     - always
     - Required
     - ``ostest``
     - ``user_main: libc tests``
     - HIL-REQUIRED
     - --
   * - TLS slots
     - ``tls.c``
     - ``TLS_NELEM > 0``
     - Required
     - ``ostest``
     - ``tls: Successfully set``
     - HIL-REQUIRED
     - --
   * - Compiler thread-local
     - ``sched_thread_local.c``
     - ``ARCH_HAVE_THREAD_LOCAL``
     - N/A
     - --
     - --
     - N/A
     - --
   * - setvbuf
     - ``setvbuf.c``
     - buffered stdio
     - Required
     - ``ostest``
     - ``user_main: setvbuf test``
     - HIL-REQUIRED
     - --
   * - ``/dev/null``
     - ``dev_null.c``
     - ``DEV_NULL``
     - Required
     - ``ostest``
     - ``user_main: /dev/null test``
     - HIL-REQUIRED
     - --
   * - Asynchronous I/O
     - ``aio.c``
     - ``FS_AIO``, writable fixture
     - Conditional
     - future fixture image
     - ``user_main: AIO test``
     - BLOCKED
     - --
   * - FPU context
     - ``fpu.c``
     - ``ARCH_FPU``
     - N/A
     - --
     - --
     - N/A
     - --
   * - Task restart/recreation
     - ``restart.c``
     - flat build
     - Required
     - ``ostest``
     - ``user_main: task_restart test``
     - HIL-REQUIRED
     - --
   * - Parent/child wait
     - ``waitpid.c``
     - parent, child status, ``WAITPID``
     - Required
     - ``ostest``
     - ``user_main: waitpid test``
     - HIL-REQUIRED
     - --
   * - Multi-user identity
     - ``multiuser.c``
     - ``TESTING_OSTEST_MULTIUSER``
     - Conditional
     - future fixture image
     - ``user_main: multi-user test``
     - BLOCKED
     - --
   * - Work queue
     - ``wqueue.c``
     - ``SCHED_WORKQUEUE``
     - Required
     - ``ostest``
     - ``user_main: wqueue test``
     - HIL-REQUIRED
     - --
   * - Mutex
     - ``mutex.c``
     - pthreads
     - Required
     - ``ostest``
     - ``user_main: mutex test``
     - HIL-REQUIRED
     - --
   * - Timed mutex
     - ``timedmutex.c``
     - pthreads
     - Required
     - ``ostest``
     - ``user_main: timed mutex test``
     - HIL-REQUIRED
     - --
   * - Recursive/error-check mutex
     - ``rmutex.c``
     - ``PTHREAD_MUTEX_TYPES``
     - Required
     - ``ostest``
     - ``user_main: recursive mutex test``
     - HIL-REQUIRED
     - --
   * - Pthread-specific data
     - ``specific.c``
     - ``TLS_NELEM > 0``
     - Required
     - ``ostest``
     - ``user_main: pthread-specific data test``
     - HIL-REQUIRED
     - --
   * - Pthread cancel
     - ``cancel.c``
     - pthreads, cancellation points
     - Required
     - ``ostest``
     - ``user_main: cancel test``
     - HIL-REQUIRED
     - --
   * - Robust mutex
     - ``robust.c``
     - mutex mode not unsafe-only
     - Required
     - ``ostest``
     - ``user_main: robust test``
     - HIL-REQUIRED
     - --
   * - Semaphore
     - ``sem.c``
     - pthreads
     - Required
     - ``ostest``
     - ``user_main: semaphore test``
     - HIL-REQUIRED
     - --
   * - Timed semaphore
     - ``semtimed.c``
     - pthreads
     - Required
     - ``ostest``
     - ``user_main: timed semaphore test``
     - HIL-REQUIRED
     - --
   * - Named semaphore
     - ``nsem.c``
     - ``FS_NAMED_SEMAPHORES``
     - Required
     - ``ostest``
     - ``user_main: Named semaphore test``
     - HIL-REQUIRED
     - --
   * - Condition variable
     - ``cond.c``
     - pthreads, no priority inheritance
     - Required
     - ``ostest-cond-*``
     - ``cond_test: Initializing mutex`` then ``cond_test: Errors 0 0``
     - HIL-REQUIRED
     - --
   * - Pthread exit/self
     - ``pthread_exit.c``
     - ``SCHED_WAITPID``
     - Required
     - ``ostest``
     - ``user_main: pthread_exit() test``
     - HIL-REQUIRED
     - --
   * - Pthread rwlock
     - ``pthread_rwlock.c``
     - pthreads
     - Required
     - ``ostest``
     - ``user_main: pthread_rwlock test``
     - HIL-REQUIRED
     - --
   * - Rwlock cancellation
     - ``pthread_rwlock_cancel.c``
     - pthreads, cancellation points
     - Required
     - ``ostest``
     - ``user_main: pthread_rwlock_cancel test``
     - HIL-REQUIRED
     - --
   * - Cleanup handlers
     - ``pthread_cleanup.c``
     - ``TLS_NCLEANUP > 0``
     - Required
     - ``ostest``
     - ``user_main: pthread_cleanup test``
     - HIL-REQUIRED
     - --
   * - Timed condition wait
     - ``timedwait.c``
     - pthreads
     - Required
     - ``ostest``
     - ``user_main: timed wait test``
     - HIL-REQUIRED
     - --
   * - Timed message queue
     - ``timedmqueue.c``
     - POSIX mqueue and pthreads
     - Required
     - ``ostest``
     - ``user_main: timed message queue test``
     - HIL-REQUIRED
     - --
   * - Signal mask
     - ``sigprocmask.c``
     - signals enabled
     - Required
     - ``ostest``
     - ``user_main: sigprocmask test``
     - HIL-REQUIRED
     - --
   * - Message queue
     - ``mqueue.c``
     - POSIX mqueue, pthreads, signals
     - Required
     - ``ostest``
     - ``user_main: message queue test``
     - HIL-REQUIRED
     - --
   * - Stop/continue actions
     - ``suspend.c``
     - SIGSTOP and SIGKILL actions
     - Required
     - ``ostest``
     - ``user_main: signal action test``
     - HIL-REQUIRED
     - --
   * - Signal handler
     - ``sighand.c``
     - full signals
     - Required
     - ``ostest``
     - ``user_main: signal handler test``
     - HIL-REQUIRED
     - --
   * - Nested signal handler
     - ``signest.c``
     - full signals
     - Required
     - ``ostest``
     - ``user_main: nested signal handler test``
     - HIL-REQUIRED
     - --
   * - POSIX timer, signal
     - ``posixtimer.c``
     - full signals, POSIX timers
     - Required
     - ``ostest``
     - ``user_main: POSIX timer test``
     - HIL-REQUIRED
     - --
   * - Flat spinlock API
     - ``spinlock.c``
     - flat build
     - Required
     - ``ostest``
     - ``user_main: spinlock test``
     - HIL-REQUIRED
     - --
   * - Watchdog
     - ``wdog.c``
     - flat build
     - Required
     - ``ostest``
     - ``user_main: wdog test``
     - HIL-REQUIRED
     - --
   * - High-resolution timer
     - ``hrtimer.c``
     - ``HRTIMER``
     - Required
     - ``ostest``
     - ``user_main: hrtimer test``
     - HIL-REQUIRED
     - --
   * - POSIX timer, thread
     - ``sigev_thread.c``
     - POSIX timers, ``SIG_EVTHREAD``
     - Required
     - ``ostest``
     - ``user_main: SIGEV_THREAD timer test``
     - HIL-REQUIRED
     - --
   * - Round robin
     - ``roundrobin.c``
     - ``RR_INTERVAL > 0``
     - Required
     - ``ostest``
     - ``user_main: round-robin scheduler test``
     - HIL-REQUIRED
     - --
   * - Sporadic scheduler
     - ``sporadic.c``
     - ``SCHED_SPORADIC``
     - Required
     - ``ostest``
     - ``user_main: sporadic scheduler test``
     - HIL-REQUIRED
     - --
   * - Dual sporadic threads
     - ``sporadic2.c``
     - ``SCHED_SPORADIC``
     - Required
     - ``ostest``
     - ``user_main: Dual sporadic thread test``
     - HIL-REQUIRED
     - --
   * - Pthread barrier
     - ``barrier.c``
     - pthreads
     - Required
     - ``ostest``
     - ``user_main: barrier test``
     - HIL-REQUIRED
     - --
   * - setjmp/longjmp
     - ``setjmp.c``
     - ``ARCH_SETJMP_H``
     - N/A
     - --
     - --
     - N/A
     - --
   * - Priority inheritance
     - ``prioinherit.c``
     - ``PRIORITY_INHERITANCE``
     - Required
     - ``ostest-pi-*``
     - ``user_main: priority inheritance test``
     - HIL-REQUIRED
     - --
   * - Scheduler lock
     - ``schedlock.c``
     - pthreads
     - Required
     - ``ostest``
     - ``user_main: scheduler lock test``
     - HIL-REQUIRED
     - --
   * - vfork
     - ``vfork.c``
     - ``ARCH_HAVE_FORK`` and ``WAITPID``
     - N/A
     - --
     - --
     - N/A
     - --
   * - SMP call
     - ``smp_call.c``
     - SMP flat build
     - N/A for UP
     - --
     - --
     - N/A
     - --
   * - Scheduler events
     - ``nxevent.c``
     - ``SCHED_EVENTS`` and flat build
     - Required
     - ``ostest``
     - ``user_main: nxevent test``
     - HIL-REQUIRED
     - --
   * - Performance counter
     - ``perf_gettime.c``
     - architecture performance events
     - N/A
     - --
     - --
     - N/A
     - --
   * - Suite memory/final status
     - ``ostest_main.c``
     - ``TESTING_OSTEST_WAITRESULT``
     - Required
     - every image
     - ``user_main: Exiting`` then ``ostest_main: Exiting with status 0``
     - HIL-REQUIRED
     - --

Parser and run contract
-----------------------

The HIL parser must derive its ordered marker list from the captured ``.config``
for the exact ELF, not from a smaller hard-coded smoke list.  A test group is
complete only when its entry marker appears in canonical order and the next
enabled group's marker appears; the final group is complete only when both
``user_main: Exiting`` and ``ostest_main: Exiting with status 0`` appear.
Repeated markers or an early P2 boot marker after ``user_main`` starts indicate
an unexpected reboot.

For an applicable row the parser must reject any unexpected ``Skipping`` as
well as any ``ERROR``, ``FAIL``, panic, assertion, stack overflow, register
dump, unexpected IRQ, timeout, serial disconnect, or unexpected reboot.  The
PI profiles require the one documented condition-test skip; the condition
profiles instead require the real condition test's zero-error summary.
Missing markers and status zero without the complete ordered marker sequence
are failures.  The artifact directory must retain the exact profile name and
defconfig, ELF and image hashes, map, generated configuration, toolchain lock,
raw serial transcript, parsed result, and reset index.

Each logical matrix must run once with ``DEBUG_ASSERTIONS`` enabled and five
consecutive times at production optimization with assertions disabled,
spanning at least five independent RAM-load/reset cycles.  Runtime stack
coloration is enabled in all four profiles.  No row may change from
HIL-REQUIRED or BLOCKED to PASS without a physical-hardware log path.

The four required RAM-only HIL commands are::

  ./tools/p2/test-ostest.py --execute --profile pi --assertion-run
  ./tools/p2/test-ostest.py --execute --profile pi
  ./tools/p2/test-ostest.py --execute --profile cond --assertion-run
  ./tools/p2/test-ostest.py --execute --profile cond

The wrapper pins the exact defconfig, derived marker matrix, assertion state,
1800-second per-cycle timeout, and cycle count.  Assertion runs are one cycle;
production runs are five cycles.  Caller-supplied options cannot weaken those
fixed values.

Dedicated scheduler stress
--------------------------

Upstream ``ostest`` is not the required million-reschedule stress test.  A
separate target test remains BLOCKED and must count at least 1,000,000 actual
reschedules while covering priorities, round robin, semaphores, mutexes,
priority inheritance, condition variables, message queues, signals, timers,
pthread create/join/cancel, task exit and recreation, and allocation under
concurrency.  Its parser must require the exact count and a completion marker
for every listed mechanism before accepting a final PASS marker.
