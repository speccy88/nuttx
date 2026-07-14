/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/include/p2_ec32mb_bank.h
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

#ifndef __BOARDS_P2_P2X8C4M64P_P2_EC32MB_INCLUDE_P2_EC32MB_BANK_H
#define __BOARDS_P2_P2X8C4M64P_P2_EC32MB_INCLUDE_P2_EC32MB_BANK_H

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

/* Raw P2 bank images occupy only the Hub range initialized by the pinned
 * loadp2/flash loader.  The physical top 16 KiB remains outside every linked
 * NuttX image and carries the warm-bank handoff.
 */

#define P2_BANK_HUB_IMAGE_LIMIT       UINT32_C(0x0007c000)
#define P2_BANK_HANDOFF_ADDRESS       UINT32_C(0x0007c000)
#define P2_BANK_HANDOFF_MAGIC         UINT32_C(0x4b423250) /* "P2BK" */
#define P2_BANK_HANDOFF_VERSION       1u
#define P2_BANK_SCRIPT_PATH_MAX       192u

/* Reserve the top P2_BANK_HUB_IMAGE_LIMIT bytes of the 32-MiB explicit
 * PSRAM store as the destructive-loader staging window.  PSRAM is not Hub
 * address space; this value is used only with p2_psram_transfer().
 */

#define P2_BANK_PSRAM_STAGE_ADDRESS   \
  (UINT32_C(33554432) - P2_BANK_HUB_IMAGE_LIMIT)

/****************************************************************************
 * Public Types
 ****************************************************************************/

struct p2_bank_handoff_s
{
  uint32_t magic;
  uint16_t version;
  uint16_t header_size;
  uint32_t bank_size;
  uint32_t bank_crc32;
  uint32_t handoff_crc32;
  char script_path[P2_BANK_SCRIPT_PATH_MAX];
};

_Static_assert(sizeof(struct p2_bank_handoff_s) <= 256,
               "P2 bank handoff must remain a small reserved-Hub record");

/****************************************************************************
 * Inline Functions
 ****************************************************************************/

/* Bank files and handoffs use the conventional reflected CRC-32/ISO-HDLC
 * representation (polynomial 0xedb88320, initial/final xor 0xffffffff).
 */

static inline uint32_t p2_bank_crc32_byte(uint32_t state, uint8_t value)
{
  unsigned int bit;

  state ^= value;
  for (bit = 0; bit < 8; bit++)
    {
      uint32_t mask = (uint32_t)-(int32_t)(state & 1u);
      state = (state >> 1) ^ (UINT32_C(0xedb88320) & mask);
    }

  return state;
}

static inline uint32_t
p2_bank_handoff_crc32(FAR const struct p2_bank_handoff_s *handoff)
{
  FAR const uint8_t *bytes = (FAR const uint8_t *)handoff;
  const size_t crc_offset = offsetof(struct p2_bank_handoff_s,
                                     handoff_crc32);
  uint32_t state = UINT32_MAX;
  size_t offset;

  for (offset = 0; offset < sizeof(*handoff); offset++)
    {
      uint8_t value = bytes[offset];

      if (offset >= crc_offset && offset < crc_offset + sizeof(uint32_t))
        {
          value = 0;
        }

      state = p2_bank_crc32_byte(state, value);
    }

  return state ^ UINT32_MAX;
}

static inline bool
p2_bank_handoff_valid(FAR const struct p2_bank_handoff_s *handoff)
{
  size_t offset;

  if (handoff == NULL ||
      handoff->magic != P2_BANK_HANDOFF_MAGIC ||
      handoff->version != P2_BANK_HANDOFF_VERSION ||
      handoff->header_size != sizeof(*handoff) ||
      handoff->bank_size == 0 ||
      handoff->bank_size > P2_BANK_HUB_IMAGE_LIMIT ||
      handoff->handoff_crc32 != p2_bank_handoff_crc32(handoff))
    {
      return false;
    }

  for (offset = 0; offset < P2_BANK_SCRIPT_PATH_MAX; offset++)
    {
      if (handoff->script_path[offset] == '\0')
        {
          return true;
        }
    }

  return false;
}

#endif /* __BOARDS_P2_P2X8C4M64P_P2_EC32MB_INCLUDE_P2_EC32MB_BANK_H */
