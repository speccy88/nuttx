/****************************************************************************
 * arch/p2/src/common/p2_delay.c
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

#include <nuttx/config.h>

#include <stdint.h>

#include <nuttx/arch.h>

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_CYCLES_PER_USEC (CONFIG_P2_SYSCLK_HZ / 1000000u)
#define P2_DELAY_CHUNK_USEC 1000u

#if (CONFIG_P2_SYSCLK_HZ % 1000000) != 0
#  error "P2 cycle delay requires an integral cycles-per-microsecond rate"
#endif

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static inline uint32_t p2_delay_counter(void)
{
  uint32_t value;

  __asm__ __volatile__("getct %0" : "=r" (value));
  return value;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

void up_udelay(useconds_t microseconds)
{
  while (microseconds != 0)
    {
      useconds_t chunk = microseconds > P2_DELAY_CHUNK_USEC ?
                         P2_DELAY_CHUNK_USEC : microseconds;
      uint32_t deadline = p2_delay_counter() +
                          (uint32_t)chunk * P2_CYCLES_PER_USEC;

      do
        {
        }
      while ((int32_t)(p2_delay_counter() - deadline) < 0);

      microseconds -= chunk;
    }
}
