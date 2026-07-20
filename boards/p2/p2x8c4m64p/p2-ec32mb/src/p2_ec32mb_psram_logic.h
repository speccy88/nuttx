/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_psram_logic.h
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
 ****************************************************************************/

#ifndef __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_PSRAM_LOGIC_H
#define __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_PSRAM_LOGIC_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_PSRAM_LOGICAL_SIZE       UINT32_C(33554432)
#define P2_PSRAM_LOGICAL_WORD_SIZE  4u
#define P2_PSRAM_LOGICAL_PAGE_SIZE  4096u
#define P2_PSRAM_CHIP_ADDRESS_MASK  UINT32_C(0x007fffff)

/* The unified scalar cache is deliberately small and deterministic.  It
 * only retains clean copies: every write reaches PSRAM before cached bytes
 * are changed.  A stored tag is the line number plus one so that the
 * zero-initialized state is unambiguously invalid, including line zero.
 */

#define P2_PSRAM_CACHE_LINE_SHIFT    5u
#define P2_PSRAM_CACHE_LINE_SIZE     \
  (1u << P2_PSRAM_CACHE_LINE_SHIFT)
#define P2_PSRAM_CACHE_SET_COUNT     4u
#define P2_PSRAM_CACHE_WAY_COUNT     2u
#define P2_PSRAM_CACHE_LINE_COUNT    \
  (P2_PSRAM_CACHE_SET_COUNT * P2_PSRAM_CACHE_WAY_COUNT)
#define P2_PSRAM_CACHE_SCALAR_MAX    8u

/****************************************************************************
 * Inline Functions
 ****************************************************************************/

static inline bool p2_psram_range_valid(uint32_t address, size_t length)
{
  return address <= P2_PSRAM_LOGICAL_SIZE &&
         length <= P2_PSRAM_LOGICAL_SIZE - address;
}

static inline uint32_t p2_psram_cache_line_address(uint32_t address)
{
  return address & ~(P2_PSRAM_CACHE_LINE_SIZE - 1u);
}

static inline uint32_t p2_psram_cache_line_number(uint32_t address)
{
  return address >> P2_PSRAM_CACHE_LINE_SHIFT;
}

static inline uint32_t p2_psram_cache_tag(uint32_t address)
{
  return p2_psram_cache_line_number(address) + 1u;
}

static inline unsigned int p2_psram_cache_line_offset(uint32_t address)
{
  return address & (P2_PSRAM_CACHE_LINE_SIZE - 1u);
}

static inline unsigned int p2_psram_cache_set(uint32_t address)
{
  return p2_psram_cache_line_number(address) &
         (P2_PSRAM_CACHE_SET_COUNT - 1u);
}

static inline unsigned int p2_psram_cache_index(unsigned int set,
                                                 unsigned int way)
{
  return set * P2_PSRAM_CACHE_WAY_COUNT + way;
}

static inline bool p2_psram_cacheable_read(uint32_t address,
                                            uint32_t length)
{
  unsigned int offset = p2_psram_cache_line_offset(address);

  return length != 0 && length <= P2_PSRAM_CACHE_SCALAR_MAX &&
         p2_psram_range_valid(address, length) &&
         length <= P2_PSRAM_CACHE_LINE_SIZE - offset;
}

static inline int __p2_xmem_psram_cache_find(
  const uint32_t tags[P2_PSRAM_CACHE_LINE_COUNT], uint32_t address)
{
  unsigned int set = p2_psram_cache_set(address);
  uint32_t tag = p2_psram_cache_tag(address);
  unsigned int way;

  for (way = 0; way < P2_PSRAM_CACHE_WAY_COUNT; way++)
    {
      unsigned int index = p2_psram_cache_index(set, way);

      if (tags[index] == tag)
        {
          return (int)index;
        }
    }

  return -1;
}

static inline unsigned int __p2_xmem_psram_cache_select(
  const uint32_t tags[P2_PSRAM_CACHE_LINE_COUNT],
  const uint8_t next_way[P2_PSRAM_CACHE_SET_COUNT], uint32_t address)
{
  unsigned int set = p2_psram_cache_set(address);
  unsigned int way;

  for (way = 0; way < P2_PSRAM_CACHE_WAY_COUNT; way++)
    {
      unsigned int index = p2_psram_cache_index(set, way);

      if (tags[index] == 0)
        {
          return index;
        }
    }

  way = next_way[set] % P2_PSRAM_CACHE_WAY_COUNT;
  return p2_psram_cache_index(set, way);
}

static inline void __p2_xmem_psram_cache_touch(
  uint8_t next_way[P2_PSRAM_CACHE_SET_COUNT], unsigned int index)
{
  unsigned int set = index / P2_PSRAM_CACHE_WAY_COUNT;
  unsigned int way = index % P2_PSRAM_CACHE_WAY_COUNT;

  next_way[set] = (uint8_t)((way + 1u) % P2_PSRAM_CACHE_WAY_COUNT);
}

static inline bool p2_psram_ranges_overlap(uint32_t address_a,
                                            uint32_t length_a,
                                            uint32_t address_b,
                                            uint32_t length_b)
{
  return length_a != 0 && length_b != 0 &&
         address_a < address_b + length_b &&
         address_b < address_a + length_a;
}

static inline uint32_t p2_psram_chip_address(uint32_t logical_address)
{
  return logical_address >> 2;
}

static inline unsigned int p2_psram_chip_index(uint32_t logical_address)
{
  return logical_address & 3u;
}

/* Choose a whole-lane burst without crossing a physical chip's 1-KiB page.
 * Four chips are byte-interleaved, so that page is one 4-KiB logical window.
 * The APS6404L burst address sequence wraps at the page boundary unless its
 * separate cross-page mode is selected; returning zero leaves unaligned,
 * short, and one-word pieces to the scalar path.
 */

static inline unsigned int p2_psram_burst_lane_words(
  uint32_t logical_address, uint32_t remaining,
  unsigned int maximum_lane_words)
{
  unsigned int lane_words;
  unsigned int page_words;

  if (p2_psram_chip_index(logical_address) != 0 ||
      remaining < 2u * P2_PSRAM_LOGICAL_WORD_SIZE ||
      maximum_lane_words < 2)
    {
      return 0;
    }

  lane_words = remaining / P2_PSRAM_LOGICAL_WORD_SIZE;
  if (lane_words > maximum_lane_words)
    {
      lane_words = maximum_lane_words;
    }

  page_words =
    (P2_PSRAM_LOGICAL_PAGE_SIZE -
     (logical_address & (P2_PSRAM_LOGICAL_PAGE_SIZE - 1u))) /
    P2_PSRAM_LOGICAL_WORD_SIZE;
  if (lane_words > page_words)
    {
      lane_words = page_words;
    }

  return lane_words >= 2 ? lane_words : 0;
}

/* Keep scalar words in the streamer's native 16-pin representation.  The
 * first QPI clock transfers the low Hub halfword and the second transfers the
 * high halfword.  The four physical chips form their bytes from those two
 * pin samples; using the little-endian Hub word directly makes scalar
 * read/modify/write operations coherent with RFWORD/WFWORD bulk traffic at
 * zero cost on the bulk path.
 */

static inline uint32_t p2_psram_pack_stream_word(const uint8_t bytes[4])
{
  return (uint32_t)bytes[0] |
         (uint32_t)bytes[1] << 8 |
         (uint32_t)bytes[2] << 16 |
         (uint32_t)bytes[3] << 24;
}

static inline void p2_psram_unpack_stream_word(uint32_t word,
                                                uint8_t bytes[4])
{
  bytes[0] = (uint8_t)word;
  bytes[1] = (uint8_t)(word >> 8);
  bytes[2] = (uint8_t)(word >> 16);
  bytes[3] = (uint8_t)(word >> 24);
}

#endif /* __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_PSRAM_LOGIC_H */
