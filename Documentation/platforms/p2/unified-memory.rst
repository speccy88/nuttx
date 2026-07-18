Unified PSRAM pointer model
===========================

Status: **DRAFTED** and **HIL-REQUIRED**.  The host-side checker is
**HOST-TESTED**, but that only tests the checker logic.  A successful P2
compile proves code-generation shape, not PSRAM electrical operation, heap
integrity, concurrency safety, or endurance.  The hardware results in
:doc:`psram-service` belong to the legacy character-device profile and do not
qualify this profile.

What ``unified`` means
----------------------

The ``p2-ec32mb:unified`` profile makes the board's 32 MiB PSRAM available to
the ordinary user heap.  ``malloc()`` and the ``kumm_*`` interfaces may return
a pointer in the tagged range below; callers use that pointer as a normal C
object pointer.  The profile does not register ``/dev/psram0``.

This is source-level unified memory, not a new P2 bus mapping.  The Propeller 2
still cannot issue a native ``rdlong`` or ``wrlong`` to PSRAM.  An opt-in
p2llvm pass routes accesses whose pointer is not provably a Hub global or Hub
stack object through runtime helpers.  Each helper distinguishes a tagged
external pointer from an ordinary Hub pointer and performs the corresponding
PSRAM or native Hub operation.

.. list-table:: Pointer regions in the unified profile
   :header-rows: 1

   * - Pointer range
     - Meaning
     - Access mechanism
   * - ``0x00000000``--``0x0007bfff``
     - Loader-visible Hub window
     - Native P2 byte, word, and long instructions
   * - ``0x10000000``--``0x11ffffff``
     - Tagged 32-MiB PSRAM window
     - Compiler-inserted ``__p2_xmem_*`` helpers
   * - ``0x12000000``
     - Exclusive end of the tagged window
     - Never a valid byte address

For an external pointer ``p``, the PSRAM byte offset is
``(uintptr_t)p - 0x10000000``.  The tag leaves the hardware Hub address range
unambiguous and catches one-past-the-end before a wire transaction.

Heap placement
--------------

``CONFIG_MM_KERNEL_HEAP=y`` keeps the dedicated kernel heap in Hub RAM.  The
initial user heap is also Hub RAM.  After the PSRAM service is ready,
``kumm_addregion((void *)0x10000000, 32 * 1024 * 1024)`` adds a second user
heap region; ``CONFIG_MM_REGIONS=2`` is therefore part of the profile
contract.  Heap metadata stored in that second region is itself reached
through the compiler/runtime access path.

This split is deliberate:

* text, read-only data, globals, BSS, TLS, interrupt state, task stacks, and
  the kernel heap stay in Hub RAM;
* ordinary dynamically allocated user C objects may live in either region;
* PSRAM never contains executable code or a task stack; and
* APIs implemented by uninstrumented assembly, a device engine, or another
  cog still require a Hub staging buffer unless their contract explicitly
  accepts tagged pointers.

Automatically created task stacks always come from ``kmm_*``.  A
caller-provided stack is accepted only when its complete, overflow-checked
range lies in physical Hub RAM; a tagged or otherwise out-of-Hub range fails
with ``-ENOTSUP``.

Compiler/runtime ABI
--------------------

The lowering is enabled only with ``-mllvm -p2-unified-memory``.  Without
that option, p2llvm retains its legacy native-memory behavior.  The unified
profile must apply the option consistently to every C translation unit that
can dereference a heap pointer, including the NuttX memory manager.  The
helper implementations are excluded from their own lowering to avoid
recursion.

The fixed helper symbol set is:

* ``__p2_xmem_load8``, ``__p2_xmem_load16``, ``__p2_xmem_load32``, and
  ``__p2_xmem_load64``;
* ``__p2_xmem_store8``, ``__p2_xmem_store16``, ``__p2_xmem_store32``, and
  ``__p2_xmem_store64``; and
* ``__p2_xmem_memcpy``, ``__p2_xmem_memmove``, and ``__p2_xmem_memset``.

Loads and stores of pointers and floating-point values use the matching
32- or 64-bit helper with the value bits preserved.  The bulk helpers must
handle Hub-to-Hub, Hub-to-PSRAM, PSRAM-to-Hub, and PSRAM-to-PSRAM operands;
``memmove`` must retain overlap semantics.

The compile-only probe is ``tools/p2/probes/unified-memory.c``.  Run
``python3 tools/p2/check-unified-memory-codegen.py`` against the selected
P2 clang.  At ``-O0``, ``-Os``, and ``-O2`` the checker requires dynamic
scalar and bulk accesses to reference the exact helper symbols, requires
proven global and stack accesses to remain native, and also verifies that the
pass is off without its explicit flag.  Negative C and LLVM-IR probes require
explicit compiler rejection of dynamic atomic loads, stores, RMW,
compare-exchange, and inline-assembly pointer operands.  An unpatched compiler
is reported as ``BLOCKED``; the tool must not turn that result into a passing
claim.

A formal ``byval`` object is a bounded Hub provenance root because the P2
calling convention copies it into the incoming stack frame.  Before instruction
selection, unified lowering stages every unproven ``byval`` actual through a
Hub alloca, so the backend's native copy never reads a tagged pointer.  This is
also the supported path for NuttX ``va_list`` wrappers: ``va_arg`` remains
native for the bounded formal copy, while an arbitrary ``va_list`` pointer is
rejected explicitly.

Known semantic limits
---------------------

The helper ABI does not implement LLVM atomic loads, stores,
read/modify/write, or compare-exchange.  The compiler contract therefore
rejects a dynamic atomic operation or corresponding ``__atomic_*`` library
operation instead of silently replacing it with non-atomic helpers.
External-memory objects must not be used for atomics, locks, scheduler state,
interrupt-shared volatile state, or memory-mapped I/O.  Place those objects
in Hub RAM.

Inline assembly is not rewritten into helpers.  Unified mode rejects an
inline-assembly call with an unproven pointer operand.  Smuggling a tagged
pointer through an integer constraint or a prebuilt assembly interface is
outside compiler analysis and is forbidden; audited inline assembly must
accept only a proven Hub object or an explicitly converted PSRAM byte offset.

External accesses are dramatically slower than Hub accesses and can block on
the serialized PSRAM service.  Code must not dereference a tagged pointer in
an interrupt handler or another context that cannot wait.  A compiler-only
pass also cannot prove that third-party assembly or prebuilt objects obey the
pointer ABI; such code needs an audited Hub buffer boundary.

The unified transfer timeout is deliberately fail-closed.  It attempts
cancellation and, after a bounded grace interval, stops the service cog before
returning so a late worker cannot write through a dead Hub stack pointer.
Completion polling uses a nonblocking hardware-lock attempt: even a service cog
parked while owning that lock cannot stop the hardware-counter deadline.
Forced cleanup stops the known cog before releasing and returning its orphaned
hardware lock.  That terminal failure requires a board reset; in-place
post-timeout recovery is not claimed.

Hardware acceptance still required
-----------------------------------

Do not promote this profile beyond **HIL-REQUIRED** until one exact image has
at least demonstrated:

* boot and shell operation with no ``/dev/psram0`` registration;
* Hub-resident code, globals, kernel allocations, and task stacks;
* allocations that exceed the Hub heap and return tagged pointers;
* aligned and unaligned 8-, 16-, 32-, and 64-bit loads/stores across natural
  word, chip-lane, page, and end-of-device boundaries;
* ``memcpy``, overlapping ``memmove``, and ``memset`` for every Hub/PSRAM
  direction;
* allocator split/coalesce/realloc/free stress with content verification;
* concurrent task access; and
* a destructive full 32-MiB write/read pass on the unified image.

The full HIL profile emits ``P2XMEM:BOUNDARY:PASS`` only after helper-driven
unaligned 16-, 32-, and 64-bit accesses cross an interleaved PSRAM page edge
and valid 8-, 16-, 32-, and 64-bit accesses end exactly at ``0x12000000``.
The bounded timeout/cancellation path still needs a separate fault-injection
campaign; a normal successful memory sweep cannot exercise it.  A dedicated
image may enable
``CONFIG_P2_EC32MB_PSRAM_UNIFIED_FAULT_INJECT_RAW_LOCK``.  After the normal
self-test passes, it parks the worker while the descriptor lock is held,
requires ``-ETIMEDOUT`` followed by terminal ``-ENODEV``, emits
``P2XMEM:FAULT_RAW_LOCK:PASS:TERMINAL``, and deliberately resets the board.
The option is disabled in both checked-in unified profiles.

Those results must identify the NuttX commit, p2llvm commit, compiler flags,
profile, image hash, board revision, and raw logs.  Until then, the feature is
a draft implementation rather than evidence of working hardware.
