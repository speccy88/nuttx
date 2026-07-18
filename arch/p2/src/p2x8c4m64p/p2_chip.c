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

/* Construct the documented P2 PLL mode for a 20 MHz input, XDIV=1,
 * XDIVP=1, and an integer XMUL.  180 MHz remains the board-qualified normal
 * operating point.  Higher rates require an explicit experimental Kconfig
 * opt-in and are never presented as production-safe.
 */

#if CONFIG_P2_XTAL_HZ != 20000000
#  error "P2 PLL sequence requires the P2-EC32MB 20 MHz TCXO"
#endif

#if CONFIG_P2_SYSCLK_HZ % CONFIG_P2_XTAL_HZ != 0
#  error "P2 system clock must be an integer multiple of the 20 MHz TCXO"
#endif

#if CONFIG_P2_SYSCLK_HZ < CONFIG_P2_XTAL_HZ || \
    CONFIG_P2_SYSCLK_HZ > 360000000
#  error "P2 PLL integer multiplier is outside the supported 1..18 range"
#endif

#if CONFIG_P2_SYSCLK_HZ > 180000000 && \
    !defined(CONFIG_P2_EXPERIMENTAL_OVERCLOCK)
#  error "P2 clocks above 180 MHz require the experimental overclock opt-in"
#endif

#define P2_RCFAST_MODE             0x000000f0u
#define P2_PLL_MULTIPLIER          \
  (CONFIG_P2_SYSCLK_HZ / CONFIG_P2_XTAL_HZ)
#define P2_CLOCK_SETUP             \
  (0x010000f4u | ((P2_PLL_MULTIPLIER - 1u) << 8))
#define P2_CLOCK_FINAL             (P2_CLOCK_SETUP | 3u)
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
