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
#define P2_PSRAM_CHIP_ADDRESS_MASK  UINT32_C(0x007fffff)

/****************************************************************************
 * Inline Functions
 ****************************************************************************/

static inline bool p2_psram_range_valid(uint32_t address, size_t length)
{
  return address <= P2_PSRAM_LOGICAL_SIZE &&
         length <= P2_PSRAM_LOGICAL_SIZE - address;
}

static inline uint32_t p2_psram_chip_address(uint32_t logical_address)
{
  return logical_address >> 2;
}

static inline unsigned int p2_psram_chip_index(uint32_t logical_address)
{
  return logical_address & 3u;
}

/* The four chips share CLK and CE.  Each clock transfers one nibble on each
 * chip's four-pin lane.  Pack the high nibble from logical bytes 0..3 into
 * bits 0..15 and the low nibbles into bits 16..31.
 */

static inline uint32_t p2_psram_pack_lanes(const uint8_t bytes[4])
{
  uint32_t high = 0;
  uint32_t low = 0;
  unsigned int chip;

  for (chip = 0; chip < 4; chip++)
    {
      high |= (uint32_t)(bytes[chip] >> 4) << (chip * 4);
      low |= (uint32_t)(bytes[chip] & 15u) << (chip * 4);
    }

  return high | low << 16;
}

static inline void p2_psram_unpack_lanes(uint32_t lanes, uint8_t bytes[4])
{
  uint32_t high = lanes & UINT32_C(0xffff);
  uint32_t low = lanes >> 16;
  unsigned int chip;

  for (chip = 0; chip < 4; chip++)
    {
      bytes[chip] = (uint8_t)(((high >> (chip * 4)) & 15u) << 4 |
                              ((low >> (chip * 4)) & 15u));
    }
}

#endif /* __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_PSRAM_LOGIC_H */
