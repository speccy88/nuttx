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
  uint32_t lanes;
  unsigned int index;

  bytes[0] = 0x12;
  bytes[1] = 0x34;
  bytes[2] = 0x56;
  bytes[3] = 0x78;
  memset(decoded, 0, sizeof(decoded));

  assert(p2_psram_range_valid(0, P2_PSRAM_LOGICAL_SIZE));
  assert(p2_psram_range_valid(P2_PSRAM_LOGICAL_SIZE, 0));
  assert(!p2_psram_range_valid(P2_PSRAM_LOGICAL_SIZE, 1));
  assert(!p2_psram_range_valid(P2_PSRAM_LOGICAL_SIZE - 1, 2));

  for (index = 0; index < sizeof(addresses) / sizeof(addresses[0]); index++)
    {
      assert(p2_psram_chip_address(addresses[index]) ==
             addresses[index] / 4);
      assert(p2_psram_chip_index(addresses[index]) ==
             addresses[index] % 4);
    }

  assert(p2_psram_chip_address(P2_PSRAM_LOGICAL_SIZE - 1) ==
         P2_PSRAM_CHIP_ADDRESS_MASK);
  lanes = p2_psram_pack_lanes(bytes);
  assert(lanes == UINT32_C(0x86427531));
  p2_psram_unpack_lanes(lanes, decoded);
  for (index = 0; index < 4; index++)
    {
      assert(decoded[index] == bytes[index]);
    }

  return 0;
}
