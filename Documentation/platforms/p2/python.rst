CPython on unified PSRAM
========================

Status: **DRAFTED** and **HOST-TESTED**.  The ``p2-ec32mb:python`` profile,
packager, container validator, and fail-closed HIL runner have host coverage.
The current diagnostic image is also **COMPILED** and
**STATICALLY-VERIFIED**, including its final ELF residency audit.  Target
Python execution is **HIL-REQUIRED** until the pending evidence section below
records passing runs from the exact clean resident image and container being
qualified.  A successful build, upload, startup marker, or dry run is not a
complete runtime result.  The strict full-range unified-PSRAM substrate is
independently **HIL-VERIFIED** for its exact image; that does not by itself
qualify CPython or its REPL.

The application is the NuttX `CPython interpreter
<https://nuttx.apache.org/docs/latest/applications/interpreters/python/index.html>`_.
The P2 profile adapts it to the board's small Hub RAM and 32 MiB external
PSRAM.  It does not add a second Python implementation.

Architecture
------------

The complete 32 MiB PSRAM participates in the Python runtime, but it is not a
hardware-mapped P2 address range.  The profile uses the tagged-pointer ABI in
:doc:`unified-memory`: p2llvm lowers ordinary C loads, stores, and bulk memory
operations that may reach tagged addresses to ``__p2_xmem_*`` helpers.  The
helpers serialize transfers through the board's QPI service cog.

There is deliberately no ``/dev/psram0``.  CPython allocators, NuttX's user
heap, externalized CPython data, the packaged standard library, and overlay
backing use tagged pointers or validated container offsets.  Code, interrupt
state, the kernel heap, TLS, and every task stack remain in Hub RAM.  PSRAM
does not become directly executable memory.

The Python profile gives the 32 MiB tagged window this fixed high-level
layout:

.. list-table:: Python PSRAM layout
   :header-rows: 1

   * - Tagged range
     - Size
     - Use
   * - ``0x10000000``--``0x102fffff``
     - 3 MiB
     - Compiler-externalized initialized data and zero-fill.  The linked
       ``.p2.xdata`` and ``.p2.xbss`` ranges must fit before ``0x10300000``.
   * - ``0x10300000``--``0x10ffffff``
     - 13 MiB
     - Validated ``nuttx.p2py`` backing window.  The actual container occupies
       only its declared length; the rest remains reserved from the heap.
   * - ``0x11000000``--``0x11ffffff``
     - 16 MiB
     - Ordinary NuttX user-heap region installed with ``kumm_addregion()``.

The first 16 MiB is therefore a runtime reserve, not lost memory.  The
container carries the external-data initializer, Hub overlay groups, and the
read-only CPython ROMFS.  A SHA-256 build fingerprint binds it to the resident
ELF; the target rejects a mismatched or malformed container before starting
CPython.  The upper 16 MiB is available through normal ``malloc()`` and
``kumm_*`` allocation.  Together, these paths give the RTOS-controlled Python
runtime access to the entire device without exposing a character driver.

CPython text cannot execute from PSRAM.  p2llvm divides nonresident functions
into groups stored in the container.  The runtime copies the requested group
into an 88-KiB Hub execution slot and calls it through a resident veneer.  The
resident image, overlay slot, external-data destinations, and container are
checked for overlap and bounds at link, package, and target initialization
time.  Each loader call compares its source and size with a coherent snapshot
of the installed resident descriptor and rechecks that source against the
validated container backing range.  It therefore needs only the payload read,
not a redundant reread of the packed 16-byte group record.  The resident
dispatcher still checks the copied payload's CRC before publishing it as
executable.  Link-assigned functions use a stable translation-unit identity
derived from their source path relative to an explicit source root plus a
build-domain namespace and variant.  Equivalent clean checkouts therefore
generate the same identity, while CPython, zlib, and libm cannot accidentally
collide.

The launcher, overlay dispatcher and failure path, telemetry and serial-output
path, and CPython allocator and thread/TSS startup surface are pinned in Hub
RAM.  This includes the normal ``PyMem_*`` and ``PyObject_*`` allocation
frontends, not only the public raw-allocation wrappers.  The post-link
``verify-python-residency.py`` audit reads ``nuttx.full`` symbol and section
metadata plus the linker-published veneer bounds.  It rejects a four-byte
overlay veneer masquerading as a resident function, an overlaid compiler
clone, a non-executable implementation, or an implementation at or above the
overlay slot.  Garbage-collected optional TSS helpers are recorded explicitly
as not linked; every other requirement is mandatory.

The audit also treats the measured explicit overlays as closed sets.  Group 7
contains exactly 149 reviewed type-initialization and module-attribute startup
bodies; the audited 83,980-byte pre-change section plus the ten measured
bodies projects to 89,816 bytes, leaving only 296 bytes in the 90,112-byte
slot.  Group 8 contains exactly three cyclic-GC visitors and eleven built-in
traversal helpers.  Its pre-change body measurements project to 3,324 bytes,
leaving 86,788 bytes.  These figures are planning evidence, not acceptance
criteria: every rebuilt ``nuttx.full`` must contain one concrete executable
section for each group, the complete required stub/body inventory, no
unreviewed body, and an actual section end at or below the linker-published
slot end.  CPython also links two local functions named ``module_traverse``;
the group-8 body identifies the reviewed implementation while every public
homonym is still required to be a well-formed four-byte overlay veneer.

Boot and runtime contract
-------------------------

``p2-ec32mb:python`` builds a small RAM-loadable resident image plus a separate
container:

* ``nuttx`` is the resident ELF and ``nuttx.bin`` is the image passed to
  ``loadp2``;
* ``nuttx.full`` is packaging input and diagnostic evidence, not an image to
  load into Hub RAM; and
* ``nuttx.p2py`` is uploaded over the console after each reset and remains in
  volatile PSRAM for subsequent Python commands.

The first ``python`` command after reset enters binary upload protocol v3 at
exactly 2000000 baud.  A logical payload of up to 65536 bytes has a 12-byte
offset, length, and CRC-32 header.  A dedicated cog drains the P63
asynchronous-RX Smart Pin into the Python profile's existing 1024-byte lower
SPSC ring as soon as each hardware-sampled byte completes.  Before advertising
upload readiness, scheduler cog 0 pauses promotion into the 1280-byte serial
upper half and consumes that lower ring directly.  The fixed preamble and
every logical block therefore share one unambiguous binary receive path.  The
scheduler stays locked from each logical block's first header byte through its
last payload byte while timer interrupts and the 30-second frame deadline stay
active.  It streams one logical payload into the already-reserved 90112-byte
overlay execution slot, which is still unpublished and unreachable by overlay
veneers at this stage.
No additional large BSS, stack buffer, or receive ring is retained.

The host keeps exactly one logical block in flight and streams it through
blocking raw-TTY writes of at most 1024 bytes without artificial quiet gaps.
It deliberately clears the POSIX ``O_NONBLOCK`` flag left by pyserial because
partial nonblocking writes caused reproducible frame corruption at 2 Mbaud.
The target validates the exact logical-block CRC, writes the block to PSRAM,
updates the whole-container CRC, and only then replies ``P2AK`` with the new
committed offset.  ``P2NK`` with the unchanged offset authorizes
retransmission of that exact block, with at most three retransmissions.  Any
timeout, malformed response, wrong offset, receive drop, or unsafe RX-mode
transition fails closed; an ambiguous lost ACK is never retried.  Normal
serial upper-half service is restored before container initialization or
ordinary runtime console input.

The fixed expected payload length lets a complete frame with a corrupted
header or payload CRC be drained and retried.  Byte insertion, deletion, or
truncation cannot be resynchronized safely and requires a cold reset.  Before
returning to NSH, both target RX buffering layers are purged.  A successful
upload also requires the hardware RX-drop counter to remain zero.  The host
then waits for container validation, runtime initialization, buffered
ROM-disk registration, and ROMFS mounting before accepting Python output.
The ROM disk explicitly disables XIP because a tagged PSRAM pointer is not a
CPU-readable memory mapping.

Board late initialization mounts ``tmpfs`` at ``/tmp`` automatically and
fails closed if it cannot do so.  The filesystem has a dedicated 1-MiB heap
whose backing allocation comes from the unified user heap.  No interactive
``mkdir`` or ``mount`` command is part of the runtime setup.  The launcher
checks the mount type before every CPython start.

The target installation prefix is ``/usr/local``.  The launcher sets
``PYTHONHOME=/usr/local``, uses the packaged
``/usr/local/lib/python313.zip``, sets ``HOME=/tmp``, and disables the user
site.  Because that layout is fixed by the board profile, a P2-only path
configuration hook populates ``PyConfig`` directly with ``/tmp`` and the
target-native standard-library ZIP.  This deliberately bypasses frozen
``getpath.py`` and its otherwise repeated overlay traffic.  The hook emits
ordered ``P2PY:PATHCONFIG:BEGIN`` and ``P2PY:PATHCONFIG:PASS`` markers; a
``P2PY:PATHCONFIG:FAIL`` marker is fatal to HIL qualification.

The P2 build selectively freezes exactly ``encodings``,
``encodings.aliases``, and ``encodings.utf_8`` so Unicode initialization does
not recover those startup modules through the ROMFS ZIP.  Their generated
marshal arrays enter the linker through the const-designated
``.p2.xdata.ro`` input section, which is merged into initialized
``.p2.xdata`` in external PSRAM.  Unified PSRAM remains writable; the suffix
records compiler/linker provenance and is not a hardware write-protection
claim.  The post-link audit requires each exact symbol to be a unique,
nonempty ``STT_OBJECT`` in allocatable, non-executable ``SHT_PROGBITS`` inside
``0x10000000..0x12000000``, verifies its input-section map containment, and
prints its address and size.  Full HIL additionally requires all three loaded
modules to report ``__spec__.origin == "frozen"`` before using their alias
table to resolve the non-frozen ``latin1`` codec.  Thus static placement alone
cannot receive credit for a ROMFS-loaded startup module.

The P2 profile also disables automatic ``site`` initialization through a
P2-only ``PyConfig.site_import`` hook, without changing the caller's argv;
therefore normal startup reports ``sys.flags.no_site == 1``.  ``site`` remains
packaged, and ``import site; site.main()`` explicitly performs its normal
processing while the user site stays disabled.  Normal, ``python -E``, and
``python -I`` startup must all receive the same fixed search path; host staging
paths are rejected during the build.

Skipping automatic ``site`` also means a valid startup may perform no
``os.stat()`` calls and therefore emit no pre-``MAIN:PASS`` ``fill_time``
diagnostics.  The HIL runner validates every such call if one occurs, but does
not invent a startup minimum.  Positive Python-path soft-float proof instead
comes from the first arithmetic worker: after asserting
``sys.flags.no_site == 1``, it brackets one explicit ``os.stat("/tmp")`` with
``P2PYTEST:SOFTFLOAT:BEGIN`` and ``P2PYTEST:SOFTFLOAT:PASS``.  Qualification
requires exactly three complete, ordered 11-record ``fill_time`` sequences
between those markers (``st_atime``, ``st_mtime``, and ``st_ctime``), followed
by ``P2PYTEST:ARITH:PASS`` in the same lifecycle.

The P2 runtime intentionally has these limits:

* only one Python process may own the process-global CPython runtime at a
  time; a concurrent launch fails with ``EBUSY`` and a later launch must still
  work;
* ``_thread`` is excluded, so Python threads are unsupported;
* ``_interpreters`` is excluded, so CPython subinterpreters are unsupported;
* ``pip`` and the optional NuttX Python package are not included in this
  profile; and
* ``/tmp``, the container, imported module state, and all other PSRAM content
  are volatile across reset or power loss.

These exclusions are build contracts, not modules that silently fail at
runtime.  Native extensions also have to obey the unified-memory restrictions
in :doc:`unified-memory`, including the prohibition on dynamic external
atomics and unaudited inline-assembly pointer operands.

Overlay hot-transition telemetry
--------------------------------

The resident overlay dispatcher maintains an eight-entry Space-Saving summary
of non-direct cross-group calls.  Its 256-byte table and counters are native
Hub BSS; profiling never reads or writes a counter in PSRAM.  A key contains
the caller group, the caller's group-relative ``CALLA`` instruction offset,
the target group, and the zero-based resident target-stub index.  For resident
group zero, the caller offset is the absolute Hub callsite address because the
Hub origin is zero.  This remains true when a resident helper runs inside a
nested overlay call: telemetry records caller group zero while the dispatch
shadow independently retains the loaded pageable group needed by the eventual
return.  Same-loaded-group direct calls return before any profile-key decoding.
Invalid resume or group-relative offsets fail closed before a record can be
counted.  Loader registration resets the complete summary.

The Python profile samples this table every 60 seconds and once at worker
exit.  Each coherent snapshot begins with::

  P2PY:HOT:<STAGE>:N=<2HEX>:T=<16HEX>

It is followed by ``N`` records in this exact grammar::

  P2PY:HOT:<STAGE>:R=<2HEX>:CG=<8HEX>:CO=<8HEX>:TG=<8HEX>:TS=<8HEX>:C=<16HEX>:E=<16HEX>

All hexadecimal digits are uppercase.  ``R`` is the zero-based transport
ordinal, ``C`` is the Space-Saving estimated count, and ``E`` is its
replacement error and maximum overcount; the event's lower bound is therefore
``C-E``.  The target emits the deterministic resident-table order and the
strict host decoder ranks records by descending ``C``, ascending ``E``, then
ascending ``CG``, ``CO``, ``TG``, and ``TS``.  Keeping ranking off-target and
using an eight-record summary limits both Hub RAM and dispatch overhead.
Same-loaded-group direct calls do not enter the summary.

Randomness contract
-------------------

``/dev/random`` and ``/dev/urandom`` use the P2 ``GETRND`` instruction.  The
hardware stream is Xoroshiro128** seeded from on-chip thermal noise at reset;
raw generator output is recoverable and is not exposed directly.  The driver
collects sixteen fresh 32-bit words and hashes each returned block with
BLAKE2s.  The final-link check requires both ``getrnd`` and the BLAKE2s call to
remain in ``p2_rng_read``.

This is BLAKE2s-conditioned hardware randomness, not a claim of certification
as a cryptographically secure random-number generator.  The HIL checks of
``os.urandom()`` and ``secrets.token_bytes()`` establish API operation,
nonzero output, and non-repetition within the tested boots.  They do not
constitute statistical qualification, entropy measurement, or a formal
security evaluation.

Reproducible build
------------------

Use an isolated NuttX worktree, its matching ``nuttx-apps`` worktree, an exact
P2 toolchain install, and the lock generated for those inputs.  Do not let a
persistent ``.p2-hil.env`` silently select a different apps tree or compiler;
the explicit variables below take precedence.

.. code-block:: console

   $ export NUTTX=/absolute/path/to/nuttx-p2-python
   $ export APPS=/absolute/path/to/apps-p2-python
   $ export P2LLVM_ROOT=/absolute/path/to/p2llvm-install
   $ export P2_TOOLCHAIN_LOCK=/absolute/path/to/p2-toolchain.lock
   $ export P2_ARTIFACTS=/absolute/new/path/p2-python-build
   $ cd "$NUTTX"
   $ NUTTX_APPS_DIR="$APPS" ./tools/p2/build.sh python

The wrapper fails if the locked compiler or source identity differs, either
compiler postcondition fails, required Python configuration is absent, the
legacy PSRAM driver is selected, target paths leak host staging directories,
``zlib`` is not builtin, unsafe Python modules appear, ``GETRND`` or BLAKE2s
is lost from the final link, external sections overlap, or resident Hub limits
are exceeded.  The Hub-overlay postcondition proves stable checkout identity,
namespace/variant separation, exact-duplicate rejection, dense four-byte
anchors, descriptor bytes, and the compiler-to-helper-to-LLD path.  The build
records that probe as ``hub-overlay-codegen.txt`` alongside the pre-build dirty
state, command, lock, maps, hashes, memory report, full/resident ELF evidence,
the exact apps archive used by the zlib overlay audit, container manifest, and
container listing under ``$P2_ARTIFACTS``.

Run the complete P2 host suite against the same apps tree:

.. code-block:: console

   $ cd "$NUTTX"
   $ NUTTX_APPS_DIR="$APPS" ./tools/p2/run-host-tests.sh

That suite covers the p2llvm unified-memory contract, Python source/build
contracts, container and resident-fingerprint validation, framed upload and
timeout behavior, buffered non-XIP ROM disk, the user-backed filesystem heap,
the P2 random driver, HIL marker ordering, stack bounds, restart stress, and
the concurrency guard.  Host tests cannot replace target execution.

Pre-qualification implementation evidence
-------------------------------------------

The latest source state includes two UART-cog corrections made after the last
complete host-suite run.  Branches inside the copied COGEXEC RX worker now use
explicit cog-long targets instead of Hub byte-relative encodings, and its
launcher preserves the p2llvm ABI's callee-saved ``r0`` and ``r1``.  The
focused Python board-source suite passes 24 of 24 tests, including source
contracts for both corrections.  A complete host-suite rerun for this exact
tree is still pending and is required before qualification.

The latest working-tree diagnostic artifact is
``/private/tmp/p2-python-uart-abi-build-r1``.  Its resident ELF passed the
final layout and residency checks.  The relevant inputs and outputs are:

.. list-table:: Diagnostic build identities
   :header-rows: 1

   * - Item
     - SHA-256 or value
   * - Build fingerprint
     - ``38ab88596971b27996f43e0348aee2bc5d42a1266309abc3ad74ef44123666c6``
   * - Resident ``nuttx`` ELF
     - ``d7cf844e315f8a50d34fa9259c5f7393a3483b185c83a17a34d5703803ea6adb``
   * - RAM-loadable ``nuttx.bin``
     - ``d059d7b492ec60c459ba38d7cb26c31262ec5ca1a6261070c46147e9943f5ee5``
       (331,680 bytes; 96 bytes remain in the 331,776-byte loader staging
       capacity)
   * - Packaging ``nuttx.full`` ELF
     - ``78808b5a57b1d8c40ce284f0ecef8d0208f97a89a097ac2fcd988dc6a42949b3``
   * - ``nuttx.p2py`` container
     - ``cb0ad2860b63583612b4bbc1b8b399ffc8fb31ceb6509abec8868ffd1ed98740``
       (11,727,200 bytes)

The strict unified-memory artifact
``/private/tmp/p2-stream-outb-offset22-full-fast-hil-r1`` independently wrote
and read all 33,554,432 PSRAM bytes and passed its streamer, boundary, scalar,
cache, bulk, geometry, concurrency, and heap gates with final FNV-1a
``B51C9DC5``.  Its exact hashes and limits are recorded in
:doc:`psram-service`.

The in-progress diagnostic smoke run
``/private/tmp/p2-python-uart-abi-smoke-hil-r1`` uses the artifact hashes in
the table above.  Protocol v3 acknowledged all 179 logical frames and all
11,727,200 bytes without a retransmission; the last frame was acknowledged at
runner elapsed time 73.292 seconds.  The target reported container CRC-32
``63DFC525``, ``RXDROPS=0``, in-place container initialization ``PASS``, and
runtime ``READY``.  CPython then passed early setup, runtime, preinitialization,
argument setup, all 113 traced static-type initializers, and the subsequent
``PYCORE_TYPES`` stage while overlay telemetry continued with ``ERR=0``.

That smoke record is deliberately partial: its ``status.json`` still says
``RUNNING`` in the first arithmetic worker, and it has not yet recorded a
Python banner, ``>>>`` prompt, ``P2PYREPL:EXPR=42``, interpreter exit, or a
return to NSH.  It is transport and initialization evidence only, not a claim
that the REPL works.

Both source trees were intentionally dirty while that diagnostic image was
built (NuttX base ``d4b647adc319be421e7cfc36f7c4870f1e3b5161`` and apps base
``b90825f5d3ce09d289ddb387605042945ce7448e``).  These results establish the
current build, transport, and partial initialization contracts, but they are
not the required clean source provenance and do not claim that a Python HIL
campaign passed.

HIL procedure
-------------

The HIL runner is dry-run by default.  This validates the artifact pair and
prints the plan without opening serial, resetting the board, loading firmware,
or writing PSRAM:

.. code-block:: console

   $ cd "$NUTTX"
   $ python3 tools/p2/test-python.py \
       --serial /dev/cu.usbserial-P97cvdxp \
       --image nuttx.bin \
       --resident-elf nuttx \
       --container nuttx.p2py \
       --artifact-dir /absolute/new/path/p2-python-hil-dry

An executing run resets the board, RAM-loads ``nuttx.bin``, and overwrites the
reserved PSRAM container window.  It does not request a flash or SD write.
Execution requires all three safety gates and the exact pinned ``loadp2``:

.. code-block:: console

   $ cd "$NUTTX"
   $ P2_HIL=1 P2_ALLOW_RESET=1 P2_ALLOW_PSRAM_WRITE=1 \
       python3 tools/p2/test-python.py --execute \
       --serial /dev/cu.usbserial-P97cvdxp \
       --loadp2 /absolute/path/to/pinned/loadp2 \
       --image nuttx.bin \
       --resident-elf nuttx \
       --container nuttx.p2py \
       --artifact-dir /absolute/new/path/p2-python-hil-01 \
       --upload-timeout 2400 \
       --test-timeout 1200

Use a new artifact directory for every run.  Qualification requires two
consecutive execute-mode ``PASS`` results from the same hashed ``nuttx.bin``,
resident ``nuttx`` ELF, and ``nuttx.p2py`` container.  Preserve each
``status.json``, ``loader.log``, ``serial.raw``, and ``serial-tx.raw``.

The target campaign checks:

* protocol-v3 recovery from deliberately corrupted full-frame CRC and header
  fields plus a malformed short-final-frame length, zero hardware RX drops,
  ordered upload, fingerprint, tmpfs, buffered ROMFS, and CPython startup
  markers;
* arithmetic, exceptions, standard-library imports, and builtin ``zlib``
  round trips for empty, small, larger-than-32-KiB, incompressible, and
  streaming payloads plus Adler-32 and CRC-32 checksums;
* frozen-loader origin for ``encodings``, ``encodings.aliases``, and
  ``encodings.utf_8``, followed by a ``latin1`` lookup which resolves the
  non-frozen ``iso8859-1`` implementation through the frozen alias table;
* normal, ``-E``, and ``-I`` path handling, default ``sys.flags.no_site == 1``,
  absence of an implicit ``site`` import, and successful explicit
  ``import site; site.main()`` processing with the user site still disabled;
* one marker-bounded arithmetic-worker ``os.stat("/tmp")`` probe with exactly
  three complete ``RAW``, ``FLOATDIDF``, ``FLOATUNSIDF``, ``MULDF3``,
  ``ADDDF3``, and ``PYFLOAT`` success sequences; zero automatic-site startup
  calls are valid, while every observed call remains sequence-checked;
* ``os.urandom()`` and ``secrets.token_bytes()``;
* an 8-MiB Python allocation plus collection, then bounded 256-KiB allocation
  pressure which must raise ``MemoryError`` after retaining at least 8 MiB,
  followed by collection and a successful fresh 1-MiB allocation;
* a 768-KiB tmpfs write/read/unlink/recreate cycle and ``tracemalloc``;
* an interactive ``python`` session with a Python 3 banner, two exact
  ``>>>`` prompts, the result marker ``P2PYREPL:EXPR=42``, a clean
  ``raise SystemExit``, and return to NSH;
* fresh module state and different string-hash seeds across interpreter
  restarts, then twenty additional start/stop/TLS/tracemalloc cycles;
* deep expression handling, the lock-only ``_thread`` compatibility contract,
  explicit absence of ``_interpreters``, rejection of a concurrent
  interpreter, and successful restart after contention;
* exactly 30 successful worker lifecycles: six checks which require a distinct
  worker for upload, process-flag, state-isolation, or setup semantics; one
  fail-closed worker containing the other 19 named assertions; one interactive
  REPL; twenty restart-stress workers; and the contention holder plus
  post-contention restart; and
* raw overlay telemetry cross-checked against the structured result.  Every
  successful lifecycle must contain one ordered ``LAUNCH``, ``BEGIN``,
  ``END``, and quiescent ``FINAL`` record, a zero exit code, and colored
  worker-stack telemetry with at least 2 KiB free.  Overlay loads and bytes
  must make positive progress without an error, unbalanced entry/exit, or an
  unfinished transition.  Every named assertion marker must also occur once,
  in order, inside its assigned worker lifecycle; a marker from another
  lifecycle cannot receive credit.

Pending target evidence
-----------------------

The Python runtime remains **HIL-REQUIRED** in this document.

.. list-table:: Required qualification records
   :header-rows: 1

   * - Record
     - State
     - Evidence
   * - Exact clean build and host suite
     - **PENDING**
     - Record source commits, toolchain lock SHA-256, artifact hashes, build
       status, host-test total, and the clean residency audit.  The diagnostic
       identities above must be replaced rather than silently promoted.
   * - Execute-mode HIL run 1
     - **HIL-REQUIRED**
     - Record artifact directory, ``status.json`` SHA-256, elapsed time,
       minimum stack headroom, and decisive terminal marker.
   * - Execute-mode HIL run 2
     - **HIL-REQUIRED**
     - Repeat from the identical hashed resident image and container, using a
       new evidence directory.

Do not replace these rows with **HIL-VERIFIED** merely because the build,
container upload, or CPython startup marker succeeds.  Both complete runtime
campaigns must finish, their ``status.json`` files must say ``PASS``, and the
recorded input hashes must match.
