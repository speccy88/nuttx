PSRAM service-cog design
========================

Status: the earlier 5-MHz explicit PSRAM service and ``/dev/psram0``
interface are **HIL-VERIFIED** for two consecutive full-device starts.  The
current implementation adds a 90-MHz wrapped-burst streamer and is
**DRAFTED**, **HOST-TESTED**, **STATICALLY-VERIFIED**, and **HIL-VERIFIED**
through one strict unified-memory full-device campaign.  That run qualifies
the current streamer and unified ABI path for its exact image; it does not
replace a fresh driver-specific timeout and concurrency campaign for the
legacy character-device profile.  PSRAM remains external to native Hub
address space in both profiles.

Interface and geometry
----------------------

The legacy ``CONFIG_P2_EC32MB_PSRAM`` interface starts one non-scheduler cog
and registers a seekable character device at ``/dev/psram0``.  Applications
use ``read()``, ``write()``, and ``lseek()``, or the board
``p2_psram_transfer()`` API, with a Hub-RAM buffer.  The logical geometry is
33,554,432 bytes made from four
8,388,608-byte APS6404L devices.  Consecutive logical byte lanes are
interleaved across the four chips, giving a natural four-byte wire word.

This interface is intentionally not a heap, stack, executable-memory, mmap,
or ordinary C-pointer facility.  Request buffers and all synchronization
objects stay in coherent Hub RAM.

The ``p2-ec32mb:unified`` profile reuses the internal transfer service but
does not register this character device.  It exposes ordinary dynamically
allocated user objects through a compiler/runtime pointer ABI instead; see
:doc:`unified-memory`.  The legacy hardware evidence below and the separate
unified-profile evidence cannot be transferred between those ABIs.

Service protocol
----------------

The NuttX scheduler cog is the single descriptor producer and the service cog
is the single consumer.  A Hub descriptor records sequence, operation,
external address, Hub buffer, length, timeout, status, completion state, and
completion sequence.  Both cogs use a P2 hardware lock for publication and
completion.  Character-device transfers are split into requests no larger
than 65,536 bytes.

P40-P55 carry the four QPI data banks, P56 is the shared clock, and P57 is the
shared chip enable.  Recovery uses the conservative 5-MHz PASM2 timing leaf:
it exits QPI, sends ``66``/``99`` reset and ``35`` QPI-entry commands, then
sends exactly one QPI ``C0`` command.  Reset restores the APS6404L default
linear burst mode; ``C0`` toggles it to 32-byte wrapped mode.  Aligned
requests of at least 32 bytes then use the Hub-FIFO streamer at 90 MHz and
raise CE before each per-chip 32-byte wrap boundary, corresponding to 128
interleaved bytes.  Unaligned and short edges stay on the 5-MHz scalar path.

The bulk clock remains below the device's 109-MHz wrapped-mode rating at
3.3 V; it would exceed the 84-MHz linear-mode rating, which is why the reset,
single-toggle, and fragmentation sequence is a hard contract.  A conservative
streamed-read fragment is bounded to 218 system cycles against the configured
1,440-cycle CE-low limit.  These are software timing targets, not externally
measured clocks.  The service checks stack guards, range limits, sequence
ownership, timeout cancellation, and CE timing.  If a request does not cancel
during its grace period, the parent stops the service cog, floats P40-P57, and
permanently fails the instance rather than permitting late completion against
a reused descriptor.

The streamer pin output is combined with the cog's ``OUTB`` latch.  The first
hardware implementation left P40--P55 holding the final scalar transaction,
so set bits in that stale latch forced streamer data lanes high even when the
streamer supplied a zero.  Stream setup now clears exactly those data bits
with ``andn outb, r8`` while CE is high and before enabling data direction or
starting ``XINIT``.  The fix does not disturb the P56 clock or P57 CE bits.
The 180-MHz board's read-capture phase is fixed at the hardware-tested offset
22, and the capture ``XCONT`` is queued before the data pins become inputs.
Source tests lock the latch-clear ordering, capture offset, turnaround, and
fragment bounds.

The destructive ``psram`` HIL profile enables
``CONFIG_P2_EC32MB_PSRAM_FAULT_INJECT_TIMEOUT``.  Its board API arms exactly
one accepted request to wait cooperatively, without holding the descriptor
lock or driving the PSRAM bus, until the normal one-tick deadline publishes
cancellation.  The app requires ``-ETIMEDOUT`` and then performs a real
write/read comparison at the end of PSRAM.  This remains deterministic when
the 90-MHz streamer finishes an ordinary 32-KiB transfer in less than one
scheduler tick.  The production deadline, cancellation, grace-period, and
failed-cog paths are not shortened or bypassed by the hook, and the option is
disabled outside that dedicated legacy-interface HIL image.

The concurrency stage likewise does not infer elapsed time from one fast
transfer and a 10-ms tick.  It submits 64 real 32-KiB requests (2 MiB total),
measures the batch and a no-request baseline with the P2 ``GETCT`` counter,
and reports both work counts and both cycle counts.  CPU availability is the
ratio of those normalized work rates on the NuttX scheduler cog; its reported
occupancy complement is not a claim about utilization of all eight hardware
cogs.  Both intervals are capped at five seconds in the exact 180-MHz HIL
profile.

Current unified-memory hardware evidence
----------------------------------------

On 2026-07-19 the strict execute-mode artifact
``/private/tmp/p2-stream-outb-offset22-full-fast-hil-r1`` completed one
destructive cycle on ``/dev/cu.usbserial-P97cvdxp``.  Its ``status.json`` says
``PASS`` and has SHA-256
``48337993f7f1df23db903ecdf711c57c28d943f6ea239eef5d74637df7d0a19c``.
The preserved resident image SHA-256 is
``ebc416d1e00225c6796d1de0caebe61784aad845dc3aae8f56cf9b2a047de096``.
The run started at ``2026-07-19T21:20:21.425Z`` and ended at
``2026-07-19T21:20:54.850Z``.

The fail-closed marker parser observed exactly one ordered pass for streamer
write and read, boundary handling, no-character-device operation, scalar
fallback, cache accounting, bulk operations, geometry, concurrent access,
and the unified user heap.  The target wrote and then read all 33,554,432
bytes, reported every 4-MiB progress boundary in both directions, and ended
with ``P2XMEM:FULL:PASS:FNV=B51C9DC5`` followed by ``P2XMEM:PASS``.  This is
full-range functional evidence for the exact binary, not a long-duration,
temperature, signal-integrity, or externally instrumented timing campaign.

Historical 5-MHz hardware evidence and limits
----------------------------------------------

The consecutive runs
``artifacts/hil/20260713T100106.997809Z-psram`` and
``artifacts/hil/20260713T100735.645104Z-psram`` used the same image and each
passed:

* walking-bit, 23 address-line, boundary, and 1,024-record random checks;
* a destructive write/read of all 33,554,432 bytes with FNV-1a ``634C9DC5``;
* reported throughput of 327,680 B/s write and 273,066 B/s read;
* concurrent scheduler work with 879 permille reported CPU availability;
* forced timeout result 110 followed by successful recovery; and
* a maximum measured-in-code CE-low duration of 982 cycles against the
  1,440-cycle software limit.

These are two consecutive complete starts, not a long-duration endurance or
temperature campaign.  The QPI clock and CE waveform have not been measured
with external instrumentation.

The results above belong to the earlier scalar-only binaries and are not
silently promoted to the current wrapped-burst streamer.  Conversely, the
current unified-memory run does not exercise the legacy ``/dev/psram0``
read/write/seek ABI or its deterministic forced-timeout hook.  A fresh
wrapped-streamer legacy campaign still has to record throughput and maximum
CE, forced-timeout cancellation followed by successful post-timeout
transfers, and its driver-specific concurrency result.  Python has its own
separate runtime qualification in :doc:`python`.
