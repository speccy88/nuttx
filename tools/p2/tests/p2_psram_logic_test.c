/****************************************************************************
 * tools/p2/tests/p2_psram_logic_test.c
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

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <assert.h>
#include <stdint.h>
#include <string.h>

#include "p2_ec32mb_psram_logic.h"

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int main(void)
{
  static const uint32_t addresses[] =
  {
    0,
    1,
    2,
    3,
    4,
    0x01fffffc,
    0x01ffffff,
  };

  uint8_t bytes[4];
  uint8_t decoded[4];
  uint8_t next_way[P2_PSRAM_CACHE_SET_COUNT] = {0};
  uint32_t tags[P2_PSRAM_CACHE_LINE_COUNT] = {0};
  uint32_t lanes;
  uint32_t address;
  uint32_t cache_length;
  uint32_t cache_offset;
  uint32_t remaining;
  unsigned int index;
  unsigned int lane_words;

  bytes[0] = 0x12;
  bytes[1] = 0x34;
  bytes[2] = 0x56;
  bytes[3] = 0x78;
  memset(decoded, 0, sizeof(decoded));

  assert(p2_psram_range_valid(0, P2_PSRAM_LOGICAL_SIZE));
  assert(p2_psram_range_valid(P2_PSRAM_LOGICAL_SIZE, 0));
  assert(!p2_psram_range_valid(P2_PSRAM_LOGICAL_SIZE, 1));
  assert(!p2_psram_range_valid(P2_PSRAM_LOGICAL_SIZE - 1, 2));

  /* Exercise the exact unified scalar-cache geometry and replacement
   * helpers.  Line zero's stored tag must be one so a BSS-zeroed cache is
   * invalid, not an accidental hit.
   */

  assert(P2_PSRAM_CACHE_LINE_SIZE == 32);
  assert(P2_PSRAM_CACHE_SET_COUNT == 4);
  assert(P2_PSRAM_CACHE_WAY_COUNT == 2);
  assert(P2_PSRAM_CACHE_LINE_COUNT == 8);
  assert(P2_PSRAM_CACHE_SCALAR_MAX == 8);
  assert(p2_psram_cache_line_address(0) == 0);
  assert(p2_psram_cache_line_address(31) == 0);
  assert(p2_psram_cache_line_address(32) == 32);
  assert(p2_psram_cache_line_address(P2_PSRAM_LOGICAL_SIZE - 1) ==
         P2_PSRAM_LOGICAL_SIZE - P2_PSRAM_CACHE_LINE_SIZE);
  assert(p2_psram_cache_tag(0) == 1);
  assert(p2_psram_cache_tag(P2_PSRAM_LOGICAL_SIZE - 1) ==
         P2_PSRAM_LOGICAL_SIZE / P2_PSRAM_CACHE_LINE_SIZE);
  assert(p2_psram_cache_line_offset(31) == 31);

  assert(p2_psram_cacheable_read(0, 1));
  assert(p2_psram_cacheable_read(0, 8));
  assert(p2_psram_cacheable_read(24, 8));
  assert(p2_psram_cacheable_read(31, 1));
  assert(!p2_psram_cacheable_read(25, 8));
  assert(!p2_psram_cacheable_read(31, 2));
  assert(!p2_psram_cacheable_read(0, 0));
  assert(!p2_psram_cacheable_read(0, 9));
  assert(p2_psram_cacheable_read(P2_PSRAM_LOGICAL_SIZE - 8, 8));
  assert(!p2_psram_cacheable_read(P2_PSRAM_LOGICAL_SIZE - 7, 8));

  for (cache_offset = 0;
       cache_offset < P2_PSRAM_CACHE_LINE_SIZE;
       cache_offset++)
    {
      for (cache_length = 0; cache_length <= 9; cache_length++)
        {
          bool expected = cache_length >= 1 && cache_length <= 8 &&
                          cache_length <=
                            P2_PSRAM_CACHE_LINE_SIZE - cache_offset;

          assert(p2_psram_cacheable_read(cache_offset, cache_length) ==
                 expected);
        }
    }

  assert(p2_psram_cache_set(0) == 0);
  assert(p2_psram_cache_set(32) == 1);
  assert(p2_psram_cache_set(64) == 2);
  assert(p2_psram_cache_set(96) == 3);
  assert(p2_psram_cache_set(128) == 0);
  assert(__p2_xmem_psram_cache_find(tags, 0) == -1);
  assert(__p2_xmem_psram_cache_select(tags, next_way, 0) == 0);

  tags[0] = p2_psram_cache_tag(0);
  __p2_xmem_psram_cache_touch(next_way, 0);
  assert(__p2_xmem_psram_cache_find(tags, 0) == 0);
  assert(next_way[0] == 1);
  assert(__p2_xmem_psram_cache_select(tags, next_way, 128) == 1);

  tags[1] = p2_psram_cache_tag(128);
  __p2_xmem_psram_cache_touch(next_way, 1);
  assert(__p2_xmem_psram_cache_find(tags, 128) == 1);
  assert(next_way[0] == 0);
  assert(__p2_xmem_psram_cache_find(tags, 0) == 0);
  __p2_xmem_psram_cache_touch(next_way, 0);
  assert(next_way[0] == 1);
  assert(__p2_xmem_psram_cache_select(tags, next_way, 256) == 1);

  /* Model a failed conflicting fill followed by a successful retry.  The
   * selected victim stays invalid on failure and line A, touched most
   * recently, remains resident.  Publishing C makes A the next victim.
   */

  tags[1] = 0;
  assert(__p2_xmem_psram_cache_find(tags, 128) == -1);
  assert(__p2_xmem_psram_cache_find(tags, 256) == -1);
  assert(__p2_xmem_psram_cache_find(tags, 0) == 0);
  assert(__p2_xmem_psram_cache_select(tags, next_way, 256) == 1);
  tags[1] = p2_psram_cache_tag(256);
  __p2_xmem_psram_cache_touch(next_way, 1);
  assert(__p2_xmem_psram_cache_find(tags, 256) == 1);
  assert(__p2_xmem_psram_cache_select(tags, next_way, 384) == 0);

  /* A failed fill leaves its selected tag at zero.  These overlap cases are
   * the scalar-to-bulk alias shapes used by write-through update and by
   * conservative invalidation after a partial/unknown write.
   */

  assert(__p2_xmem_psram_cache_find(tags, 32) == -1);
  assert(p2_psram_ranges_overlap(96, 32, 80, 64));
  assert(p2_psram_ranges_overlap(24, 8, 28, 16));
  assert(p2_psram_ranges_overlap(28, 16, 24, 8));
  assert(p2_psram_ranges_overlap(0, 32, 31, 2));
  assert(!p2_psram_ranges_overlap(0, 32, 32, 1));
  assert(!p2_psram_ranges_overlap(32, 1, 0, 32));
  assert(!p2_psram_ranges_overlap(0, 0, 0, 32));

  for (index = 0; index < sizeof(addresses) / sizeof(addresses[0]); index++)
    {
      assert(p2_psram_chip_address(addresses[index]) ==
             addresses[index] / 4);
      assert(p2_psram_chip_index(addresses[index]) ==
             addresses[index] % 4);
    }

  assert(p2_psram_chip_address(P2_PSRAM_LOGICAL_SIZE - 1) ==
         P2_PSRAM_CHIP_ADDRESS_MASK);
  lanes = p2_psram_pack_stream_word(bytes);
  assert(lanes == UINT32_C(0x78563412));
  p2_psram_unpack_stream_word(lanes, decoded);
  for (index = 0; index < 4; index++)
    {
      assert(decoded[index] == bytes[index]);
    }

  /* Exhaust every alignment and length around two adjacent physical pages.
   * A nonzero plan must be a 2..3-word aligned transfer wholly inside one
   * 4-KiB logical page and wholly inside the caller's remaining range.
   */

  for (address = 0; address < 2u * P2_PSRAM_LOGICAL_PAGE_SIZE; address++)
    {
      for (remaining = 0; remaining <= 64; remaining++)
        {
          lane_words = p2_psram_burst_lane_words(address, remaining, 3);
          assert(lane_words == 0 || (lane_words >= 2 && lane_words <= 3));
          if (lane_words != 0)
            {
              uint32_t final =
                address + lane_words * P2_PSRAM_LOGICAL_WORD_SIZE - 1u;

              assert(p2_psram_chip_index(address) == 0);
              assert(lane_words * P2_PSRAM_LOGICAL_WORD_SIZE <= remaining);
              assert(address / P2_PSRAM_LOGICAL_PAGE_SIZE ==
                     final / P2_PSRAM_LOGICAL_PAGE_SIZE);
            }
        }
    }

  assert(p2_psram_burst_lane_words(0, 7, 3) == 0);
  assert(p2_psram_burst_lane_words(0, 8, 3) == 2);
  assert(p2_psram_burst_lane_words(0, 15, 3) == 3);
  assert(p2_psram_burst_lane_words(0, 16, 3) == 3);
  assert(p2_psram_burst_lane_words(0, 4096, 3) == 3);
  assert(p2_psram_burst_lane_words(1, 64, 3) == 0);
  assert(p2_psram_burst_lane_words(4088, 64, 3) == 2);
  assert(p2_psram_burst_lane_words(4092, 64, 3) == 0);
  assert(p2_psram_burst_lane_words(P2_PSRAM_LOGICAL_SIZE - 16u,
                                   16, 3) == 3);
  assert(p2_psram_burst_lane_words(0, 64, 1) == 0);

  return 0;
}
