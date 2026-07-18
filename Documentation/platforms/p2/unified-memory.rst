Unified PSRAM pointer model
===========================

Status: **HIL-VERIFIED** on the exact P2-EC32MB Rev B images identified below.
The normal image proved the 32-MiB tagged user heap and absence of
``/dev/psram0`` under NSH.  The full image then passed a destructive
33,554,432-byte write/read, boundary, scalar, bulk, allocator, and concurrent
access campaign.  The compiler checker is **HOST-TESTED**.  This result does
not claim endurance, temperature qualification, externally measured QPI
timing, or the separate raw-lock timeout fault injection.

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

Profiles may reserve an aligned prefix for validated external data and
container backing before adding the remainder as a user-heap region.  In
particular, :doc:`python` assigns the first 16 MiB to CPython external data
and its packaged runtime, then installs the upper 16 MiB as the ordinary user
heap.  This still uses the same tagged-pointer ABI and never registers
``/dev/psram0``.

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

Hardware evidence and remaining limit
-------------------------------------

The 2026-07-18 campaign used a P2-EC32MB Rev B at 180 MHz on
``/dev/cu.usbserial-P97cvdxp``.  ``loadp2`` loaded both images into RAM with
``-ZERO -DTR -t``; no flash or SD write was performed.  The frozen source and
compiler provenance for both firmware images was:

* NuttX ``c19be7b5c62042a264d262de41d7ba6c1e75c37a`` and apps
  ``71b90cca497d18e667f091bef8113343f746badf``;
* p2llvm ``bdcefcce7860b2232c06f35726fea679a3a7309c`` with llvm-project
  ``72a9bb1ef2656d9953d1f41a8196d425ff2ab0b1``;
* preemption and unified patch SHA-256 values
  ``3d4c7a031bc9d260ba9ebe93a93e287d27f6142ccb081eb3a544fa7875cb8d27``
  and
  ``b99b12aecbe84d62d978fe311e66a6a17a19a86c0913daae96788d41e7bc9f8f``;
* clang/clang++ SHA-256
  ``c3e35e36112f6528c2864a172b6871115cabbeb6fb8222f08fdb962bd0d01e87``
  and llc SHA-256
  ``90d78269f9575b852e417a88e1c0ce25c2764339c68f95093728cb091dac0560``;
* exact toolchain-lock SHA-256
  ``997cc0399829d9300f3234d9dd3d570e5d63debc22c6dd0e12fbeb07901095fe``;
  and
* compiler option ``-mllvm -p2-unified-memory`` on the unified builds.

The host-only extended-timeout HIL runner was commit
``641de4441e19ba2a26e011920ce6f41fdf1338cf``; it did not change the firmware
source commit above.  The exact ``hil.py`` copy preserved with the passing
full run has SHA-256
``f5f2e290011b4355fb31b8873e16b8446b3d706966ef9ba330192167d7885ff4``.
The superseded artifact ``/private/tmp/p2-unified-hil-full-c19be7b`` is
correctly recorded as ``FAIL``: its original 600-second host bound expired
after every write checkpoint and the first 8 MiB of clean readback, with no
target failure marker.  Its status SHA-256 is
``f2918c35da7a753cfd015b5c3dd4d2e81ef624ea7c6490cd3de5238da54c45cb``.
It motivated the narrowly gated runner change above and is not counted as
passing hardware evidence; the same unchanged ELF subsequently completed the
authoritative run below.

The normal ``p2-ec32mb:unified`` build is preserved at
``/private/tmp/p2-unified-build-c19be7b``.  Its ELF SHA-256 is
``69e22c182460b335dd9f99d63b7c85c55e797133c91033c101e555573ef94df5``
and its raw binary SHA-256 is
``88a6d82bd5a92f61fadc113604bd68ccc570f88bd8966e3673616dacb25a3c3c``.
The one-cycle NSH artifact at
``/private/tmp/p2-unified-hil-normal-c19be7b`` is ``PASS``; its top-level
status SHA-256 is
``8a51d085583823d31bc52cd1ca0623c1cf7f343f3dc3ce76a83674c50bb988bd``.
The target reported:

.. code-block:: text

        total       used       free    maxused    maxfree  nused  nfree name
       131068       4676     126392       7064     124384     19      2 Kmem
     33686492       2236   33684256       2616   33554416      9      2 Umem

``ls /dev`` listed only ``console``, ``null``, ``ttyS0``, and ``zero``.
``uname`` identified NuttX commit ``c19be7b5c6``.  Shell, process, sleep, and
mount probes also passed.  The raw serial record is
``/private/tmp/p2-unified-hil-normal-c19be7b/cycle-001/console.raw``.
A post-sweep reload of the same normal image repeated those results and left
the board running that image.  Its artifact is
``/private/tmp/p2-unified-hil-normal-postfull-c19be7b`` and its status SHA-256
is ``66ec3ecae9d25b57077e226b8edda4b4ebbf5c8f71fe2442508e35c032410a76``.
That reload is board-state evidence only: its generic copied ``.config`` was
the then-current ``unified-hil`` workspace configuration, so the clean normal
build and first normal HIL artifact above remain the configuration provenance.

The destructive ``p2-ec32mb:unified-hil`` build is preserved at
``/private/tmp/p2-unified-hil-build-c19be7b``.  The clean build-status SHA-256
is ``ed2ee762ef0ca383d54c9d87a2bbcf50417ab2b42c6847ce335e595e36708d79``;
the ELF and raw-binary SHA-256 values are, respectively,
``db975694b44c0c432c1826af1ab792515500654a2ef2d3b0fafa7b84fe796b5c``
and
``52d1bf4344e0b9f72946f1bd4101f76fff8529e51f5f4acab22b6f09ffc1f449``.
Both self-tests were enabled and raw-lock fault injection was disabled.

The authoritative HIL artifact is
``/private/tmp/p2-unified-hil-full-pass-c19be7b``.  It used the ``boot``
protocol with a hard 1,800-second limit and finished ``PASS`` in
1,271.601346 seconds.  Its top-level status SHA-256 is
``0b80ad000fa6a3712d53519d2148e84ef1fa6e2a9fce9ab74a3e963f85e100a8``;
the raw serial log SHA-256 is
``853d57c927423f38f940d132124275eb1d08257144ae66f671074a6307966143``.
All eight 4-MiB write progress markers and all eight read markers appeared in
order, using the literal form ``P2XMEM:FULL:PROGRESS:WRITE=...`` and
``P2XMEM:FULL:PROGRESS:READ=...`` through ``02000000``.  The decisive terminal
markers were:

.. code-block:: text

   P2XMEM:BOUNDARY:PASS
   P2XMEM:NODEV:PASS
   P2XMEM:SCALAR:PASS
   P2XMEM:BULK:PASS
   P2XMEM:GEOMETRY:PASS
   P2XMEM:CONCURRENT:PASS
   P2XMEM:HEAP:PASS
   P2XMEM:FULL:PASS:FNV=B51C9DC5
   P2XMEM:PASS

The boundary marker covers unaligned 16-, 32-, and 64-bit accesses across an
interleaved PSRAM page edge and valid 8-, 16-, 32-, and 64-bit accesses ending
exactly at ``0x12000000``.  The full test writes, reads, compares, and hashes
every PSRAM byte before the allocator installs external-region metadata.  The
remaining scalar, bulk, geometry, concurrent-task, realloc, fragmentation,
and content-preservation checks then exercise the memory through ordinary
tagged pointers and the NuttX heap.

The bounded timeout/cancellation fault path remains **HIL-REQUIRED** because a
successful memory sweep cannot exercise it.  A dedicated image may enable
``CONFIG_P2_EC32MB_PSRAM_UNIFIED_FAULT_INJECT_RAW_LOCK``.  It parks the worker
while the descriptor lock is held, requires ``-ETIMEDOUT`` followed by
terminal ``-ENODEV``, emits
``P2XMEM:FAULT_RAW_LOCK:PASS:TERMINAL``, and deliberately resets the board.
The option was disabled in both verified images.  No endurance, temperature,
or externally instrumented QPI waveform/timing claim is made.
