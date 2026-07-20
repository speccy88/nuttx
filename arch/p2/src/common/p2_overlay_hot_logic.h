/****************************************************************************
 * arch/p2/src/common/p2_overlay_hot_logic.h
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

#ifndef __ARCH_P2_SRC_COMMON_P2_OVERLAY_HOT_LOGIC_H
#define __ARCH_P2_SRC_COMMON_P2_OVERLAY_HOT_LOGIC_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <errno.h>
#include <stddef.h>
#include <stdint.h>

#include <arch/overlay.h>

/****************************************************************************
 * Inline Functions
 ****************************************************************************/

/* Decode a validated CALLA resume PC into its telemetry identity without
 * changing the pageable group that the shadow record must restore.  A
 * resident helper may call an overlay while RELOAD_GROUP remains loaded;
 * its Hub PC still identifies telemetry caller group zero.  Only a resume in
 * the execution slot is relative to RELOAD_GROUP's image.
 */

static inline int p2_overlay_hot_decode_callsite(
  uint32_t reload_group, uintptr_t pc, uintptr_t slot_start,
  size_t reload_image_size, uint32_t *hot_caller_group,
  uint32_t *hot_caller_offset)
{
  uintptr_t offset;

  if (hot_caller_group == NULL || hot_caller_offset == NULL ||
      (pc & (P2_OVERLAY_STUB_BYTES - 1)) != 0)
    {
      return -EINVAL;
    }

  if (pc <= slot_start)
    {
      if (pc < UINT32_C(0x404))
        {
          return -EINVAL;
        }

      *hot_caller_group = P2_OVERLAY_RESIDENT_GROUP;
      *hot_caller_offset = (uint32_t)(pc - P2_OVERLAY_STUB_BYTES);
      return 0;
    }

  if (reload_group == P2_OVERLAY_RESIDENT_GROUP || reload_image_size == 0 ||
      reload_image_size > UINTPTR_MAX - slot_start ||
      pc > slot_start + reload_image_size)
    {
      return -EINVAL;
    }

  offset = pc - slot_start - P2_OVERLAY_STUB_BYTES;
  if (offset >= reload_image_size ||
      (offset & (P2_OVERLAY_STUB_BYTES - 1)) != 0)
    {
      return -EINVAL;
    }

  *hot_caller_group = reload_group;
  *hot_caller_offset = (uint32_t)offset;
  return 0;
}

static inline int p2_overlay_hot_key_equal(
  const struct p2_overlay_hot_entry_s *entry,
  const struct p2_overlay_hot_entry_s *key)
{
  return entry->caller_group == key->caller_group &&
         entry->caller_offset == key->caller_offset &&
         entry->target_group == key->target_group &&
         entry->target_stub == key->target_stub;
}

static inline uint64_t p2_overlay_hot_increment(uint64_t value)
{
  return value == UINT64_MAX ? UINT64_MAX : value + UINT64_C(1);
}

static inline void p2_overlay_hot_reset(
  struct p2_overlay_hot_entry_s table[P2_OVERLAY_HOT_CAPACITY],
  uint32_t *used, uint64_t *total_count)
{
  uint32_t index;

  for (index = 0; index < P2_OVERLAY_HOT_CAPACITY; index++)
    {
      table[index].caller_group = 0;
      table[index].caller_offset = 0;
      table[index].target_group = 0;
      table[index].target_stub = 0;
      table[index].count = 0;
      table[index].error = 0;
    }

  *used = 0;
  *total_count = 0;
}

/* Update a fixed-capacity Space-Saving summary.  A matching record costs one
 * bounded linear scan.  If the table is full, replace the first least-count
 * record, preserving its old count as the new key's maximum-overcount error.
 * First-index tie breaking makes identical streams byte-for-byte repeatable.
 */

static inline void p2_overlay_hot_update(
  struct p2_overlay_hot_entry_s table[P2_OVERLAY_HOT_CAPACITY],
  uint32_t *used, uint64_t *total_count,
  const struct p2_overlay_hot_entry_s *key)
{
  struct p2_overlay_hot_entry_s *empty = NULL;
  struct p2_overlay_hot_entry_s *minimum = NULL;
  uint32_t index;

  *total_count = p2_overlay_hot_increment(*total_count);

  for (index = 0; index < P2_OVERLAY_HOT_CAPACITY; index++)
    {
      struct p2_overlay_hot_entry_s *entry = &table[index];

      if (entry->count == 0)
        {
          if (empty == NULL)
            {
              empty = entry;
            }

          continue;
        }

      if (p2_overlay_hot_key_equal(entry, key))
        {
          entry->count = p2_overlay_hot_increment(entry->count);
          return;
        }

      if (minimum == NULL || entry->count < minimum->count)
        {
          minimum = entry;
        }
    }

  if (empty != NULL)
    {
      empty->caller_group = key->caller_group;
      empty->caller_offset = key->caller_offset;
      empty->target_group = key->target_group;
      empty->target_stub = key->target_stub;
      empty->count = UINT64_C(1);
      empty->error = 0;
      if (*used < P2_OVERLAY_HOT_CAPACITY)
        {
          (*used)++;
        }

      return;
    }

  /* A capacity of zero is forbidden by the public ABI. */

  if (minimum != NULL)
    {
      uint64_t replacement_error = minimum->count;

      minimum->caller_group = key->caller_group;
      minimum->caller_offset = key->caller_offset;
      minimum->target_group = key->target_group;
      minimum->target_stub = key->target_stub;
      minimum->count = p2_overlay_hot_increment(replacement_error);
      minimum->error = replacement_error;
    }
}

#endif /* __ARCH_P2_SRC_COMMON_P2_OVERLAY_HOT_LOGIC_H */
