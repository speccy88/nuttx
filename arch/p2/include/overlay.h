/****************************************************************************
 * arch/p2/include/overlay.h
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.  The
 * ASF licenses this file to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance with the
 * License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
 * WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the
 * License for the specific language governing permissions and limitations
 * under the License.
 *
 ****************************************************************************/

#ifndef __ARCH_P2_INCLUDE_OVERLAY_H
#define __ARCH_P2_INCLUDE_OVERLAY_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <nuttx/compiler.h>

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

/* On-disk/in-PSRAM metadata ABI shared with the overlay packer.  Group zero
 * is permanently reserved for resident Hub code.  Real overlay group IDs
 * therefore start at one and index the group table directly.
 */

#define P2_OVERLAY_ABI_VERSION                 UINT32_C(1)
#define P2_OVERLAY_RESIDENT_GROUP              UINT32_C(0)

#define P2_OVERLAY_GROUP_FLAG_REQUIRED         UINT32_C(0x00000001)
#define P2_OVERLAY_GROUP_FLAG_READ_ONLY        UINT32_C(0x00000002)
#define P2_OVERLAY_GROUP_FLAG_EXECUTABLE       UINT32_C(0x00000004)
#define P2_OVERLAY_GROUP_FLAG_FIXED_ADDRESS    UINT32_C(0x00000008)
#define P2_OVERLAY_GROUP_FLAG_MASK             UINT32_C(0x0000000f)
#define P2_OVERLAY_GROUP_FLAGS_V1              P2_OVERLAY_GROUP_FLAG_MASK
#define P2_OVERLAY_GROUP_FLAGS_PACKED_V1       P2_OVERLAY_GROUP_FLAGS_V1

#define P2_OVERLAY_STUB_BYTES                  UINT32_C(4)
#define P2_OVERLAY_ENTRY_BYTES                 UINT32_C(8)
#define P2_OVERLAY_GROUP_BYTES                 UINT32_C(16)
#define P2_OVERLAY_HOT_CAPACITY                8u

/* Metadata emitted by the packer must use these exact input sections.  Stub
 * bodies are emitted in assembly because every public function stub must be
 * exactly one unconditional four-byte CALLA to __p2_overlay_enter.
 */

#define P2_OVERLAY_ENTRY_SECTION               \
  ".p2.xdata.ro.overlay.entries"
#define P2_OVERLAY_GROUP_SECTION               ".p2.overlay.groups"
#define P2_OVERLAY_STUB_SECTION                ".p2.overlay.stubs"

#define P2_OVERLAY_ENTRY_ATTR                  \
  __attribute__((section(P2_OVERLAY_ENTRY_SECTION), used, aligned(8)))
#define P2_OVERLAY_GROUP_ATTR                  \
  __attribute__((section(P2_OVERLAY_GROUP_SECTION), used, aligned(4)))

/****************************************************************************
 * Public Types
 ****************************************************************************/

/* One immutable external entry record corresponds to one four-byte resident
 * stub at the same zero-based index.  Offset is relative to the fixed Hub
 * execution slot.  The container installs the records as part of .p2.xdata;
 * the resident runtime validates the complete table before publishing it.
 */

struct p2_overlay_entry_s
{
  uint32_t group;
  uint32_t offset;
};

/* Before publication, source is an offset in the packed overlay backing
 * image.  p2_overlay_install_groups() copies a complete packer table into
 * the writable resident table and converts every nonzero group's source to
 * a tagged PSRAM pointer in one validated operation.  The older
 * p2_overlay_relocate_groups() entry point remains available for linkers
 * that pre-populate the resident table.  Readiness is resident runtime state
 * rather than a packed flag bit, so this structure stays byte-for-byte
 * compatible with the container table.  The four version-1 flags mean
 * required, read-only, executable, fixed-address.  image_crc32 is
 * CRC-32/ISO-HDLC, matching Python zlib.crc32().
 */

struct p2_overlay_group_s
{
  uintptr_t source;
  uint32_t image_size;
  uint32_t image_crc32;
  uint32_t flags;
};

/* Resident progress counters for a single-slot overlay domain.  The runtime
 * snapshots these fields under its dispatcher critical section, so a
 * resident observer may report progress while the owning task is executing
 * overlay code.  Counters are diagnostic only and never affect dispatch.
 */

struct p2_overlay_stats_s
{
  uint64_t entry_count;
  uint64_t exit_count;
  uint64_t direct_count;
  uint64_t load_attempt_count;
  uint64_t load_count;
  uint64_t load_bytes;
  uint32_t current_depth;
  uint32_t maximum_depth;
  uint32_t loaded_group;
  uint32_t loading_group;
  uint32_t loading_bytes;
  uint32_t last_requested_group;
  uint32_t last_stub_index;
  int32_t last_error;
  bool transition;
  bool ready;
};

/* One resident Space-Saving record identifies a non-direct overlay entry.
 * caller_offset is the CALLA instruction offset relative to caller_group;
 * group zero uses its absolute Hub address because the Hub origin is zero.
 * target_stub is the zero-based resident veneer index.  count is the
 * Space-Saving estimate and error is its maximum overcount, so the observed
 * frequency is in the inclusive range [count - error, count].
 */

struct p2_overlay_hot_entry_s
{
  uint32_t caller_group;
  uint32_t caller_offset;
  uint32_t target_group;
  uint32_t target_stub;
  uint64_t count;
  uint64_t error;
};

/* Fixed-size coherent copy of the resident top-K table.  Only entries below
 * used are populated.  total_count counts all valid non-direct cross-group
 * entries represented by the Space-Saving stream.  The table itself is
 * exactly 256 bytes and never resides in PSRAM.
 */

struct p2_overlay_hot_snapshot_s
{
  uint64_t total_count;
  uint32_t used;
  uint32_t capacity;
  struct p2_overlay_hot_entry_s entries[P2_OVERLAY_HOT_CAPACITY];
};

/* The callback must copy exactly image_size bytes from the already-validated
 * tagged source into destination and return zero only after the copy has
 * completed.  The resident runtime independently verifies image_crc32 before
 * publishing the group as executable.  It never asks this callback to open,
 * mount, seek, or read a filesystem object.
 */

typedef int (*p2_overlay_loader_t)(FAR void *arg, uint32_t group,
                                   uintptr_t source, FAR void *destination,
                                   size_t image_size);

/****************************************************************************
 * Public Function Prototypes
 ****************************************************************************/

#ifdef CONFIG_P2_HUB_OVERLAYS

/* Install a complete packer-generated group table into the zero-initialized
 * writable resident table.  GROUPS must point to native Hub memory; callers
 * reading metadata from PSRAM must stage it first.  COUNT includes reserved
 * group zero.  All records and ranges are checked before the first resident
 * record changes, and the operation is rejected after any overlay state has
 * been published.
 */

int p2_overlay_install_groups(
  FAR const struct p2_overlay_group_s *groups, size_t count,
  uintptr_t tagged_base, size_t backing_size);

/* Copy one installed, relocated group descriptor into native Hub memory.
 * The snapshot remains available while an overlay load transition is active,
 * allowing a registered loader to validate its callback arguments without
 * rereading mutable backing-image metadata.  Group zero, an uninstalled
 * table, and malformed resident metadata are rejected.
 */

int p2_overlay_get_group(uint32_t group,
                         FAR struct p2_overlay_group_s *descriptor);

/* Roll back an installed group table before loader publication.  This is the
 * failure half of the container install transaction: it is accepted only
 * while no loader, entry table, owner, transition, or executable overlay has
 * been published.  A successful rollback restores the exact pristine state
 * required for a later upload attempt.
 */

int p2_overlay_uninstall_groups(void);

/* Relocate the complete writable group table from packed-image offsets to a
 * tagged PSRAM range.  This is a two-pass operation: no record is changed if
 * any source range or metadata flag is invalid.
 */

int p2_overlay_relocate_groups(uintptr_t tagged_base, size_t backing_size);

/* Register the copy callback and atomically publish the already-relocated
 * metadata table.  Registration is rejected while an overlay root call is
 * active.  No overlay stub may execute successfully before this returns 0.
 */

int p2_overlay_register_loader(p2_overlay_loader_t loader, FAR void *arg);

/* First fatal dispatcher error, or zero before a failure.  Dispatcher errors
 * are fail-closed and normally do not return to a caller after setting this.
 */

int p2_overlay_last_error(void);

/* Copy one coherent progress snapshot into native Hub memory. */

int p2_overlay_get_stats(FAR struct p2_overlay_stats_s *stats);

/* Copy one coherent top-K transition snapshot into native Hub memory. */

int p2_overlay_get_hot_snapshot(
  FAR struct p2_overlay_hot_snapshot_s *snapshot);

#endif /* CONFIG_P2_HUB_OVERLAYS */
#endif /* __ARCH_P2_INCLUDE_OVERLAY_H */
