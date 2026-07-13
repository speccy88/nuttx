/****************************************************************************
 * arch/p2/src/p2x8c4m64p/p2_chip.c
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

#include <nuttx/board.h>

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

/* These values are the hardware-qualified 20 MHz to 180 MHz PLL sequence for
 * the P2-EC32MB Rev-B module.  Keep the compile-time checks adjacent to the
 * constants: silently using this sequence for another crystal or target rate
 * would make all Smart Pin timing wrong.
 */

#if CONFIG_P2_XTAL_HZ != 20000000
#  error "P2 PLL sequence requires the P2-EC32MB 20 MHz TCXO"
#endif

#if CONFIG_P2_SYSCLK_HZ != 180000000
#  error "P2 PLL sequence is qualified only at 180 MHz"
#endif

#define P2_RCFAST_MODE             0x000000f0u
#define P2_CLOCK_SETUP             0x010008f4u
#define P2_CLOCK_FINAL             0x010008f7u
#define P2_CLOCK_LOCK_WAIT_CYCLES  300000u

#define P2_LOADER_CLKFREQ          (*(volatile uint32_t *)0x14u)
#define P2_LOADER_CLKMODE          (*(volatile uint32_t *)0x18u)

/****************************************************************************
 * Public Functions
 ****************************************************************************/

void p2_clockconfig(void)
{
  uint32_t delay = P2_CLOCK_LOCK_WAIT_CYCLES;

  /* Return to RCFAST before changing PLL fields.  P2_CLOCK_SETUP keeps
   * RCFAST selected while the PLL locks; P2_CLOCK_FINAL selects the PLL only
   * after the documented settling interval.  The metadata words are also
   * kept current for diagnostics and loader-compatible restart paths.
   */

  __asm__ __volatile__("hubset %0" : : "ri" (P2_RCFAST_MODE));
  P2_LOADER_CLKFREQ = CONFIG_P2_SYSCLK_HZ;
  P2_LOADER_CLKMODE = P2_CLOCK_FINAL;
  __asm__ __volatile__("hubset %0" : : "ri" (P2_CLOCK_SETUP));
  __asm__ __volatile__("waitx %0" : : "r" (delay));
  __asm__ __volatile__("hubset %0" : : "ri" (P2_CLOCK_FINAL));
}

int board_reset(int status)
{
  (void)status;

  /* HUBSET bit 28 requests a chip reset. */

  __asm__ __volatile__("hubset %0" : : "ri" (0x10000000u));
  for (; ; )
    {
      __asm__ __volatile__("nop");
    }
}
