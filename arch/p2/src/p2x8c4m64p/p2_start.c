/****************************************************************************
 * arch/p2/src/p2x8c4m64p/p2_start.c
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

#include <nuttx/init.h>

#include "p2_internal.h"

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_START_DATA_COOKIE 0x50324e58u

/****************************************************************************
 * Public Function Prototypes
 ****************************************************************************/

/* These are the early hardware handoff points.  Neither function may depend
 * on initialized NuttX services.  The clock must be stable before low-level
 * serial configures the console smart pins.
 */

void p2_clockconfig(void);
void p2_lowsetup(void);

/****************************************************************************
 * Private Data
 ****************************************************************************/

static volatile uint32_t g_p2_start_data = P2_START_DATA_COOKIE;
static volatile uint32_t g_p2_start_bss;

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static void p2_early_puts(const char *text)
{
  while (*text != '\0')
    {
      p2_lowputc(*text++);
    }
}

/****************************************************************************
 * Public Data
 ****************************************************************************/

extern const uint32_t _sidata[];
extern uint32_t _sdata[];
extern uint32_t _edata[];
extern uint32_t _sbss[];
extern uint32_t _ebss[];

/****************************************************************************
 * Public Functions
 ****************************************************************************/

/****************************************************************************
 * Name: p2_start
 *
 * Description:
 *   Complete the C-visible part of P2 startup.  p2_head.S has already
 *   restarted cog 0 in Hub-execution mode and selected the bottom of the
 *   upward-growing initial stack as PTRA.
 *
 ****************************************************************************/

void p2_start(void) noreturn_function;
void p2_start(void)
{
  const uint32_t *src;
  uint32_t *dest;

  /* The RAM-load layout currently gives .data identical load and execution
   * addresses.  Retain a real copy path so a later nonvolatile load image
   * can use a distinct LMA without replacing the startup contract.
   */

  src = _sidata;
  for (dest = _sdata; dest < _edata; )
    {
      *dest++ = *src++;
    }

  /* Clear .bss inline.  Calling memset before global state exists would make
   * startup depend on a selected compiler or C runtime implementation.
   */

  for (dest = _sbss; dest < _ebss; )
    {
      *dest++ = 0;
    }

  p2_clockconfig();
  p2_lowsetup();

  p2_early_puts("P2BOOT:ENTRY\r\n");
  if (g_p2_start_data == P2_START_DATA_COOKIE)
    {
      p2_early_puts("P2BOOT:DATA=OK\r\n");
    }
  else
    {
      p2_early_puts("P2BOOT:DATA=FAIL\r\n");
    }

  if (g_p2_start_bss == 0u)
    {
      g_p2_start_bss = P2_START_DATA_COOKIE;
      p2_early_puts("P2BOOT:BSS=OK\r\n");
    }
  else
    {
      p2_early_puts("P2BOOT:BSS=FAIL\r\n");
    }

  p2_early_puts("P2BOOT:NX_START\r\n");
  nx_start();

  /* nx_start() is not expected to return. */

  for (; ; )
    {
      __asm__ __volatile__("nop");
    }
}
