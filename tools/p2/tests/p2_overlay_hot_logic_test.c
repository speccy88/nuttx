/****************************************************************************
 * tools/p2/tests/p2_overlay_hot_logic_test.c
 *
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

#include <assert.h>
#include <stdint.h>

#include "p2_overlay_hot_logic.h"

static struct p2_overlay_hot_entry_s key(uint32_t value)
{
  struct p2_overlay_hot_entry_s entry;

  entry.caller_group = value & 7u;
  entry.caller_offset = UINT32_C(0x1000) + value * 4u;
  entry.target_group = 1u + (value & 15u);
  entry.target_stub = value;
  entry.count = 0;
  entry.error = 0;
  return entry;
}

int main(void)
{
  struct p2_overlay_hot_entry_s table[P2_OVERLAY_HOT_CAPACITY];
  struct p2_overlay_hot_entry_s candidate;
  uint64_t total;
  uint32_t caller_group;
  uint32_t caller_offset;
  uint32_t used;
  uint32_t index;

  assert(P2_OVERLAY_HOT_CAPACITY == 8);
  assert(sizeof(table) == 256);

  /* A resident helper can run while group seven remains loaded for its
   * eventual return.  Its absolute Hub callsite still belongs to telemetry
   * caller group zero.  A resume in the slot instead belongs to group seven.
   */

  assert(p2_overlay_hot_decode_callsite(
           7, UINT32_C(0x2404), UINT32_C(0x60000), 0,
           &caller_group, &caller_offset) == 0);
  assert(caller_group == P2_OVERLAY_RESIDENT_GROUP);
  assert(caller_offset == UINT32_C(0x2400));
  assert(p2_overlay_hot_decode_callsite(
           7, UINT32_C(0x60014), UINT32_C(0x60000), 0x100,
           &caller_group, &caller_offset) == 0);
  assert(caller_group == 7);
  assert(caller_offset == UINT32_C(0x10));
  assert(p2_overlay_hot_decode_callsite(
           P2_OVERLAY_RESIDENT_GROUP, UINT32_C(0x60004),
           UINT32_C(0x60000), 0, &caller_group, &caller_offset) == -EINVAL);
  assert(p2_overlay_hot_decode_callsite(
           7, UINT32_C(0x60104), UINT32_C(0x60000), 0x100,
           &caller_group, &caller_offset) == -EINVAL);
  assert(p2_overlay_hot_decode_callsite(
           7, UINT32_C(0x2402), UINT32_C(0x60000), 0,
           &caller_group, &caller_offset) == -EINVAL);

  p2_overlay_hot_reset(table, &used, &total);
  assert(used == 0);
  assert(total == 0);
  for (index = 0; index < P2_OVERLAY_HOT_CAPACITY; index++)
    {
      assert(table[index].count == 0);
      assert(table[index].error == 0);
    }

  candidate = key(7);
  p2_overlay_hot_update(table, &used, &total, &candidate);
  p2_overlay_hot_update(table, &used, &total, &candidate);
  assert(used == 1);
  assert(total == 2);
  assert(p2_overlay_hot_key_equal(&table[0], &candidate));
  assert(table[0].count == 2);
  assert(table[0].error == 0);

  p2_overlay_hot_reset(table, &used, &total);
  for (index = 0; index < P2_OVERLAY_HOT_CAPACITY; index++)
    {
      candidate = key(index);
      p2_overlay_hot_update(table, &used, &total, &candidate);
    }

  assert(used == P2_OVERLAY_HOT_CAPACITY);
  assert(total == P2_OVERLAY_HOT_CAPACITY);

  /* Make slot zero non-minimal.  The next unseen key must deterministically
   * replace slot one, the first remaining minimum, and carry error one.
   */

  candidate = key(0);
  p2_overlay_hot_update(table, &used, &total, &candidate);
  p2_overlay_hot_update(table, &used, &total, &candidate);
  assert(table[0].count == 3);

  candidate = key(1000);
  p2_overlay_hot_update(table, &used, &total, &candidate);
  assert(used == P2_OVERLAY_HOT_CAPACITY);
  assert(total == P2_OVERLAY_HOT_CAPACITY + 3u);
  assert(p2_overlay_hot_key_equal(&table[1], &candidate));
  assert(table[1].count == 2);
  assert(table[1].error == 1);
  assert(table[1].count - table[1].error == 1);

  /* Saturation cannot wrap a long-running diagnostic counter to zero. */

  total = UINT64_MAX;
  table[1].count = UINT64_MAX;
  p2_overlay_hot_update(table, &used, &total, &candidate);
  assert(total == UINT64_MAX);
  assert(table[1].count == UINT64_MAX);

  return 0;
}
