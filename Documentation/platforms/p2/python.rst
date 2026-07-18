CPython on unified PSRAM
========================

Status: **DRAFTED** and **HOST-TESTED**.  The ``p2-ec32mb:python`` profile,
packager, container validator, and fail-closed HIL runner have host coverage.
Target Python execution is **HIL-REQUIRED** until the pending evidence section
below records passing runs from the exact resident image and container being
qualified.  A successful build or dry run is not runtime evidence.

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
time.

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

The first ``python`` command after reset enters binary upload protocol v2 at
exactly 230400 baud.  A 1024-byte logical frame has an offset, length, and
CRC-32.  The host sends only one logical frame at a time, split into 224-byte
wire writes separated by 10-ms quiet intervals.  This pacing is derived from
the P2 console's 256-byte first-stage RX ring and remains bounded if one
10-ms service tick is missed.  The target replies ``P2AK`` with the new
committed offset only after validation and the PSRAM write.  ``P2NK`` with the
unchanged offset authorizes retransmission of that exact frame, with at most
three retransmissions.  Any timeout, malformed response, or wrong offset
fails closed; an ambiguous lost ACK is never retried.

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
site.  Normal, ``python -E``, and ``python -I`` startup must all find the same
target-native standard library; host staging paths are rejected during the
build.

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

The wrapper fails if the locked compiler or source identity differs, the
unified lowering probe fails, required Python configuration is absent, the
legacy PSRAM driver is selected, target paths leak host staging directories,
``zlib`` is not builtin, unsafe Python modules appear, ``GETRND`` or BLAKE2s
is lost from the final link, external sections overlap, or resident Hub limits
are exceeded.  It records the pre-build dirty state, command, lock, maps,
hashes, memory report, full/resident ELF evidence, the exact apps archive used
by the zlib overlay audit, container manifest, and container listing under
``$P2_ARTIFACTS``.

Run the complete P2 host suite against the same apps tree:

.. code-block:: console

   $ cd "$NUTTX"
   $ NUTTX_APPS_DIR="$APPS" ./tools/p2/run-host-tests.sh

That suite covers the p2llvm unified-memory contract, Python source/build
contracts, container and resident-fingerprint validation, framed upload and
timeout behavior, buffered non-XIP ROM disk, the user-backed filesystem heap,
the P2 random driver, HIL marker ordering, stack bounds, restart stress, and
the concurrency guard.  Host tests cannot replace target execution.

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
       --upload-timeout 1800 \
       --test-timeout 180

Use a new artifact directory for every run.  Qualification requires two
consecutive execute-mode ``PASS`` results from the same hashed ``nuttx.bin``,
resident ``nuttx`` ELF, and ``nuttx.p2py`` container.  Preserve each
``status.json``, ``loader.log``, ``serial.raw``, and ``serial-tx.raw``.

The target campaign checks:

* protocol-v2 recovery from deliberately corrupted full-frame CRC and header
  fields plus a malformed short-final-frame length, zero hardware RX drops,
  ordered upload, fingerprint, tmpfs, buffered ROMFS, and CPython startup
  markers;
* arithmetic, exceptions, standard-library imports, and builtin ``zlib``
  round trips for empty, small, larger-than-32-KiB, incompressible, and
  streaming payloads plus Adler-32 and CRC-32 checksums;
* normal, ``-E``, and ``-I`` path handling;
* ``os.urandom()`` and ``secrets.token_bytes()``;
* an 8-MiB Python allocation plus collection, a 768-KiB tmpfs
  write/read/unlink/recreate cycle, and ``tracemalloc``;
* fresh module state and different string-hash seeds across interpreter
  restarts, then twenty additional start/stop/TLS/tracemalloc cycles;
* deep expression handling, explicit absence of ``_thread`` and
  ``_interpreters``, rejection of a concurrent interpreter, and successful
  restart after contention; and
* colored worker-stack telemetry after every command, with at least 2 KiB
  free.

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
       status, and host-test total.
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
