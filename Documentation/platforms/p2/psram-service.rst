PSRAM service-cog design
========================

Status: the explicit PSRAM service and ``/dev/psram0`` interface are
**HIL-VERIFIED** for two consecutive full-device starts.  PSRAM remains
external storage, never native Hub address space.

Interface and geometry
----------------------

``CONFIG_P2_EC32MB_PSRAM`` starts one non-scheduler cog and registers a
seekable character device at ``/dev/psram0``.  Applications use ``read()``,
``write()``, and ``lseek()``, or the board ``p2_psram_transfer()`` API, with a
Hub-RAM buffer.  The logical geometry is 33,554,432 bytes made from four
8,388,608-byte APS6404L devices.  Consecutive logical byte lanes are
interleaved across the four chips, giving a natural four-byte wire word.

This interface is intentionally not a heap, stack, executable-memory, mmap,
or ordinary C-pointer facility.  Request buffers and all synchronization
objects stay in coherent Hub RAM.

Service protocol
----------------

The NuttX scheduler cog is the single descriptor producer and the service cog
is the single consumer.  A Hub descriptor records sequence, operation,
external address, Hub buffer, length, timeout, status, completion state, and
completion sequence.  Both cogs use a P2 hardware lock for publication and
completion.  Character-device transfers are split into requests no larger
than 65,536 bytes.

P40-P55 carry the four QPI data banks, P56 is the shared clock, and P57 is the
shared chip enable.  The PASM2 timing leaf uses a configured 5-MHz QPI target;
that value is a software timing target, not an externally measured clock.  A
four-byte read uses 15 clocks and a write uses 10, bounding each CE-low window
well below the configured 1,440-cycle limit.  The service checks stack guards,
range limits, sequence ownership, timeout cancellation, and CE timing.  If a
request does not cancel during its grace period, the parent stops the service
cog, floats P40-P57, and permanently fails the instance rather than permitting
late completion against a reused descriptor.

Hardware evidence and limits
----------------------------

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
