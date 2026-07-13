/****************************************************************************
 * arch/p2/src/common/p2_timer.c
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

#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <time.h>

#include <nuttx/arch.h>
#include <nuttx/irq.h>

#include <arch/irq.h>

#include "clock/clock.h"
#include "p2_clock.h"
#include "p2_internal.h"

/****************************************************************************
 * Private Data
 ****************************************************************************/

static uint32_t g_p2_timer_deadline;

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

extern void p2_timer_program(uint32_t base, uint32_t delta);
uint32_t p2_timer_interval(void);

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static uint32_t p2_counter(void)
{
  uint32_t value;

  __asm__ __volatile__("getct %0" : "=r" (value));
  return value;
}

static int p2_timer_isr(int irq, void *context, void *arg)
{
  uint32_t interval = p2_timer_interval();
  uint32_t deadline = g_p2_timer_deadline;

  /* Preserve absolute phase as the hardware-proven stress image does. */

  p2_timer_program(deadline, interval);
  g_p2_timer_deadline = deadline + interval;

  /* The console RX smart pin is drained into a Hub ring by a dedicated
   * cog.  Service that ring from the timer interrupt as well as the idle
   * loop so a CPU-bound foreground command cannot starve input or Ctrl-C.
   */

  p2_serialinterrupt(irq, context, arg);
  nxsched_process_timer();
  return OK;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

uint32_t p2_timer_interval(void)
{
  return p2_tick_cycles(CONFIG_P2_SYSCLK_HZ, CLOCKS_PER_SEC);
}

void up_timer_initialize(void)
{
  uint32_t interval = p2_timer_interval();
  uint32_t now;
  int ret;

  p2_boot_trace("P2K:TIMER:ENTER");

  DEBUGASSERT(interval != 0);
  if (interval == 0)
    {
      PANIC();
    }

  ret = irq_attach(P2_IRQ_TIMER0, p2_timer_isr, NULL);
  if (ret < 0)
    {
      PANIC();
    }

  now = p2_counter();
  p2_timer_program(now, interval);
  g_p2_timer_deadline = now + interval;
  up_enable_irq(P2_IRQ_TIMER0);
  p2_boot_trace("P2K:TIMER:OK");
}
