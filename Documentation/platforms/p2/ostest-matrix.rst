P2 ``ostest`` matrix
=====================

Status: profiles, build/configuration locks, and strict marker parser are
**IMPLEMENTED** and the complete applicable physical matrix is
**HIL-ACCEPTED**.  All 45 required rows and 57/57 strict parser groups per
cycle passed; the two fixture-conditional rows remain blocked and the six
architecture-inapplicable rows remain N/A.

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

``PASS`` requires a complete accepted physical campaign, not compilation or
host execution.  ``N/A`` is reserved for a capability the P2 architecture
does not expose.
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
     - PASS
     - all applicable campaigns
   * - Task create and arguments
     - ``ostest_main.c``
     - flat build
     - Required
     - ``ostest``
     - ``user_main: Begin argument test``
     - PASS
     - all applicable campaigns
   * - Environment
     - ``ostest_main.c``
     - ``!DISABLE_ENVIRON``
     - Required
     - ``ostest``
     - ``ostest_main: putenv``
     - PASS
     - all applicable campaigns
   * - getopt family
     - ``getopt.c``
     - always
     - Required
     - ``ostest``
     - ``user_main: getopt() test``
     - PASS
     - all applicable campaigns
   * - libc memmem
     - ``libc_memmem.c``
     - always
     - Required
     - ``ostest``
     - ``user_main: libc tests``
     - PASS
     - all applicable campaigns
   * - TLS slots
     - ``tls.c``
     - ``TLS_NELEM > 0``
     - Required
     - ``ostest``
     - ``tls: Successfully set``
     - PASS
     - all applicable campaigns
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
     - PASS
     - all applicable campaigns
   * - ``/dev/null``
     - ``dev_null.c``
     - ``DEV_NULL``
     - Required
     - ``ostest``
     - ``user_main: /dev/null test``
     - PASS
     - all applicable campaigns
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
     - PASS
     - all applicable campaigns
   * - Parent/child wait
     - ``waitpid.c``
     - parent, child status, ``WAITPID``
     - Required
     - ``ostest``
     - ``user_main: waitpid test``
     - PASS
     - all applicable campaigns
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
     - PASS
     - all applicable campaigns
   * - Mutex
     - ``mutex.c``
     - pthreads
     - Required
     - ``ostest``
     - ``user_main: mutex test``
     - PASS
     - all applicable campaigns
   * - Timed mutex
     - ``timedmutex.c``
     - pthreads
     - Required
     - ``ostest``
     - ``user_main: timed mutex test``
     - PASS
     - all applicable campaigns
   * - Recursive/error-check mutex
     - ``rmutex.c``
     - ``PTHREAD_MUTEX_TYPES``
     - Required
     - ``ostest``
     - ``user_main: recursive mutex test``
     - PASS
     - all applicable campaigns
   * - Pthread-specific data
     - ``specific.c``
     - ``TLS_NELEM > 0``
     - Required
     - ``ostest``
     - ``user_main: pthread-specific data test``
     - PASS
     - all applicable campaigns
   * - Pthread cancel
     - ``cancel.c``
     - pthreads, cancellation points
     - Required
     - ``ostest``
     - ``user_main: cancel test``
     - PASS
     - all applicable campaigns
   * - Robust mutex
     - ``robust.c``
     - mutex mode not unsafe-only
     - Required
     - ``ostest``
     - ``user_main: robust test``
     - PASS
     - all applicable campaigns
   * - Semaphore
     - ``sem.c``
     - pthreads
     - Required
     - ``ostest``
     - ``user_main: semaphore test``
     - PASS
     - all applicable campaigns
   * - Timed semaphore
     - ``semtimed.c``
     - pthreads
     - Required
     - ``ostest``
     - ``user_main: timed semaphore test``
     - PASS
     - all applicable campaigns
   * - Named semaphore
     - ``nsem.c``
     - ``FS_NAMED_SEMAPHORES``
     - Required
     - ``ostest``
     - ``user_main: Named semaphore test``
     - PASS
     - all applicable campaigns
   * - Condition variable
     - ``cond.c``
     - pthreads, no priority inheritance
     - Required
     - ``ostest-cond-*``
     - ``cond_test: Initializing mutex`` then ``cond_test: Errors 0 0``
     - PASS
     - all applicable campaigns
   * - Pthread exit/self
     - ``pthread_exit.c``
     - ``SCHED_WAITPID``
     - Required
     - ``ostest``
     - ``user_main: pthread_exit() test``
     - PASS
     - all applicable campaigns
   * - Pthread rwlock
     - ``pthread_rwlock.c``
     - pthreads
     - Required
     - ``ostest``
     - ``user_main: pthread_rwlock test``
     - PASS
     - all applicable campaigns
   * - Rwlock cancellation
     - ``pthread_rwlock_cancel.c``
     - pthreads, cancellation points
     - Required
     - ``ostest``
     - ``user_main: pthread_rwlock_cancel test``
     - PASS
     - all applicable campaigns
   * - Cleanup handlers
     - ``pthread_cleanup.c``
     - ``TLS_NCLEANUP > 0``
     - Required
     - ``ostest``
     - ``user_main: pthread_cleanup test``
     - PASS
     - all applicable campaigns
   * - Timed condition wait
     - ``timedwait.c``
     - pthreads
     - Required
     - ``ostest``
     - ``user_main: timed wait test``
     - PASS
     - all applicable campaigns
   * - Timed message queue
     - ``timedmqueue.c``
     - POSIX mqueue and pthreads
     - Required
     - ``ostest``
     - ``user_main: timed message queue test``
     - PASS
     - all applicable campaigns
   * - Signal mask
     - ``sigprocmask.c``
     - signals enabled
     - Required
     - ``ostest``
     - ``user_main: sigprocmask test``
     - PASS
     - all applicable campaigns
   * - Message queue
     - ``mqueue.c``
     - POSIX mqueue, pthreads, signals
     - Required
     - ``ostest``
     - ``user_main: message queue test``
     - PASS
     - all applicable campaigns
   * - Stop/continue actions
     - ``suspend.c``
     - SIGSTOP and SIGKILL actions
     - Required
     - ``ostest``
     - ``user_main: signal action test``
     - PASS
     - all applicable campaigns
   * - Signal handler
     - ``sighand.c``
     - full signals
     - Required
     - ``ostest``
     - ``user_main: signal handler test``
     - PASS
     - all applicable campaigns
   * - Nested signal handler
     - ``signest.c``
     - full signals
     - Required
     - ``ostest``
     - ``user_main: nested signal handler test``
     - PASS
     - all applicable campaigns
   * - POSIX timer, signal
     - ``posixtimer.c``
     - full signals, POSIX timers
     - Required
     - ``ostest``
     - ``user_main: POSIX timer test``
     - PASS
     - all applicable campaigns
   * - Flat spinlock API
     - ``spinlock.c``
     - flat build
     - Required
     - ``ostest``
     - ``user_main: spinlock test``
     - PASS
     - all applicable campaigns
   * - Watchdog
     - ``wdog.c``
     - flat build
     - Required
     - ``ostest``
     - ``user_main: wdog test``
     - PASS
     - all applicable campaigns
   * - High-resolution timer
     - ``hrtimer.c``
     - ``HRTIMER``
     - Required
     - ``ostest``
     - ``user_main: hrtimer test``
     - PASS
     - all applicable campaigns
   * - POSIX timer, thread
     - ``sigev_thread.c``
     - POSIX timers, ``SIG_EVTHREAD``
     - Required
     - ``ostest``
     - ``user_main: SIGEV_THREAD timer test``
     - PASS
     - all applicable campaigns
   * - Round robin
     - ``roundrobin.c``
     - ``RR_INTERVAL > 0``
     - Required
     - ``ostest``
     - ``user_main: round-robin scheduler test``
     - PASS
     - all applicable campaigns
   * - Sporadic scheduler
     - ``sporadic.c``
     - ``SCHED_SPORADIC``
     - Required
     - ``ostest``
     - ``user_main: sporadic scheduler test``
     - PASS
     - all applicable campaigns
   * - Dual sporadic threads
     - ``sporadic2.c``
     - ``SCHED_SPORADIC``
     - Required
     - ``ostest``
     - ``user_main: Dual sporadic thread test``
     - PASS
     - all applicable campaigns
   * - Pthread barrier
     - ``barrier.c``
     - pthreads
     - Required
     - ``ostest``
     - ``user_main: barrier test``
     - PASS
     - all applicable campaigns
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
     - PASS
     - all applicable campaigns
   * - Scheduler lock
     - ``schedlock.c``
     - pthreads
     - Required
     - ``ostest``
     - ``user_main: scheduler lock test``
     - PASS
     - all applicable campaigns
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
     - PASS
     - all applicable campaigns
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
     - PASS
     - all applicable campaigns

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
HIL-REQUIRED or BLOCKED to PASS without a physical-hardware log path; the 45
applicable rows above have the four accepted paths recorded below.

The four RAM-only HIL reproduction commands are::

  ./tools/p2/test-ostest.py --execute --profile pi --assertion-run
  ./tools/p2/test-ostest.py --execute --profile pi
  ./tools/p2/test-ostest.py --execute --profile cond --assertion-run
  ./tools/p2/test-ostest.py --execute --profile cond

The wrapper pins the exact defconfig, derived marker matrix, assertion state,
3600-second per-cycle timeout, and cycle count.  Assertion runs are one cycle;
production runs are five cycles.  Caller-supplied options cannot weaken those
fixed values.

Accepted physical campaigns
---------------------------

.. list-table:: Accepted OSTest profiles
   :header-rows: 1
   :widths: 20 12 24 20 24

   * - Profile
     - Result
     - Elapsed seconds
     - Build artifact
     - HIL artifact and warnings
   * - ``ostest-pi-assert``
     - PASS 1/1; assertions true
     - 1112.947113
     - ``artifacts/hil/20260713T115624Z-build-ostest-pi-assert``
     - ``artifacts/hil/20260713T115705.736374Z-ostest``; seven hrtimer timing
       warnings
   * - ``ostest-cond-assert``
     - PASS 1/1; assertions true
     - 1083.622049
     - ``artifacts/hil/20260713T121555Z-build-ostest-cond-assert``
     - ``artifacts/hil/20260713T121658.724366Z-ostest``; nine hrtimer timing
       warnings
   * - ``ostest-pi-production``
     - PASS 5/5; assertions false
     - 1109.718123, 1109.821585, 1109.823341, 1109.809943, 1109.871800
     - ``artifacts/hil/20260713T123519Z-build-ostest-pi-production``
     - ``artifacts/hil/20260713T123627.152482Z-ostest``; 15 hrtimer timing
       warnings total
   * - ``ostest-cond-production``
     - PASS 5/5; assertions false
     - 1157.549036, 1157.470767, 1157.390669, 1157.597868, 1157.556411
     - ``artifacts/hil/20260713T140927Z-build-ostest-cond-production``
     - ``artifacts/hil/20260713T141008.365027Z-ostest``; 25 hrtimer timing
       warnings total

Every artifact has final status ``PASS`` and the parser policy explicitly
counts hrtimer timing warnings without making them fatal.  Across the four
campaigns, 12/12 RAM-load/reset cycles completed the complete ordered marker
sequence with 57/57 strict parser groups, the final memory report,
``user_main: Exiting``, and
``ostest_main: Exiting with status 0``.  PI profiles observed the one required
condition-test incompatibility skip; condition profiles instead observed
``cond_test: Errors 0 0``.

The earlier diagnostic ``artifacts/hil/20260713T040951.397206Z-ostest``
completed the actual low/high-priority inheritance handshake, but its
medium-priority worker remained inside one ``INT_MAX`` volatile busy-loop
chunk until the former 1800-second host bound expired.  The accepted source
checks ``nhighpri_running()`` on every iteration without sleeping, blocking,
yielding, or lowering priority.  Broken inheritance still retains the timeout
proof, while successful inheritance lets the completed high-priority thread
terminate the obsolete chunk.  The accepted PI assertion and five-cycle
production campaigns supersede that pre-fix diagnostic.

Dedicated scheduler stress
--------------------------

Upstream ``ostest`` is not the dedicated million-event scheduler stress test,
so the latter has a separate target image and strict parser.  That gate is now
HIL-ACCEPTED: ``artifacts/hil/20260713T112942.518754Z-schedstress`` passed one
physical run in 165.434771 seconds with exactly 1,004,078 counted events:
2,000 priority handoffs, 100,000 round-robin events, 600,000 semaphore events,
2,000 priority-inheritance mutex events, 100,000 condition-variable events,
100,000 message-queue events, 100,000 signal events, 10 timer events, four
pthread create/join/cancel lifecycle events, and 64 task exit/recreation
events.

The same run reported 896 bytes used in a 6,088-byte checked stack and heap
usage of 8,240 bytes before, 12,344 during, and 8,240 after the allocation
check.  Its separate two-worker concurrent allocator check passed 512/512
overlapping allocations (256 rounds per worker); these allocations are not
included in the 1,004,078 scheduler-event total.  The sealed build evidence is
``artifacts/hil/20260713T112709Z-build-schedstress``.  This accepted result
does not replace any required ``ostest`` row or campaign above; those rows now
have their own accepted evidence.
