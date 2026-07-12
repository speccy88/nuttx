PSRAM service-cog design
========================

Status: DRAFTED.

PSRAM must be accessed through explicit services, not normal C pointers. A future service cog should consume Hub-resident request rings containing external PSRAM address, Hub buffer, transfer size, direction, sequence, status, timeout, and memory-ordering protocol using proven locks. Possible NuttX abstractions are block device, character device, handle allocator, RAM-disk-like bulk store, or explicit bulk-buffer service.
