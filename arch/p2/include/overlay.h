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

/* Metadata emitted by the packer must use these exact input sections.  Stub
 * bodies are emitted in assembly because every public function stub must be
 * exactly one unconditional four-byte CALLA to __p2_overlay_enter.
 */

#define P2_OVERLAY_ENTRY_SECTION               ".p2.overlay.entries"
#define P2_OVERLAY_GROUP_SECTION               ".p2.overlay.groups"
#define P2_OVERLAY_STUB_SECTION                ".p2.overlay.stubs"

#define P2_OVERLAY_ENTRY_ATTR                  \
  __attribute__((section(P2_OVERLAY_ENTRY_SECTION), used, aligned(4)))
#define P2_OVERLAY_GROUP_ATTR                  \
  __attribute__((section(P2_OVERLAY_GROUP_SECTION), used, aligned(4)))

/****************************************************************************
 * Public Types
 ****************************************************************************/

/* One entry record corresponds to one four-byte stub at the same zero-based
 * index.  Offset is relative to the fixed Hub execution slot.
 */

struct p2_overlay_entry_s
{
  uint32_t group;
  uint32_t offset;
};

/* Before relocation, source is an offset in the packed overlay backing
 * image.  p2_overlay_relocate_groups() converts every nonzero group's source
 * to a tagged PSRAM pointer in one validated publish operation.  Relocation
 * readiness is resident runtime state rather than a packed flag bit, so this
 * structure stays byte-for-byte compatible with the container table.  The
 * four version-1 flags mean required, read-only, executable, fixed-address.
 * image_crc32 is CRC-32/ISO-HDLC, matching Python zlib.crc32().
 */

struct p2_overlay_group_s
{
  uintptr_t source;
  uint32_t image_size;
  uint32_t image_crc32;
  uint32_t flags;
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

#endif /* CONFIG_P2_HUB_OVERLAYS */
#endif /* __ARCH_P2_INCLUDE_OVERLAY_H */
