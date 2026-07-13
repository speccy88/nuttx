/****************************************************************************
 * arch/p2/src/common/p2_irq.c
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
#include <errno.h>
#include <stddef.h>
#include <stdint.h>

#include <nuttx/arch.h>
#include <nuttx/irq.h>
#include <nuttx/sched.h>

#include <arch/context.h>
#include <arch/irq.h>

#include "sched/sched.h"
#include "p2_internal.h"

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#ifndef CONFIG_BUILD_FLAT
#  error "P2 interrupt/context switching currently supports flat UP builds"
#endif

#ifdef CONFIG_SMP
#  error "P2 interrupt/context switching is currently UP-only"
#endif

/* Keep these guard and stack offsets synchronized with p2_context.S. */

#define P2_IRQ_AREA_GUARD_LONGS    4
#define P2_IRQ_AREA_FRAME_LONG     P2_IRQ_AREA_GUARD_LONGS
#define P2_IRQ_AREA_LONGS          (P2_CONTEXT_WORDS + \
                                    2 * P2_IRQ_AREA_GUARD_LONGS)
#define P2_IRQ_STACK_GUARD_LONGS  16
#define P2_IRQ_STACK_USABLE_LONGS 512
#define P2_IRQ_STACK_LONGS         (P2_IRQ_STACK_USABLE_LONGS + \
                                    2 * P2_IRQ_STACK_GUARD_LONGS)

#define P2_IRQ_GUARD_LOW           0x1a51cafeu
#define P2_IRQ_GUARD_HIGH          0xe71d5afeu

/****************************************************************************
 * Public Data
 ****************************************************************************/

volatile xcpt_reg_t *g_current_regs;

/* Interrupt entry cannot use task PTRA even briefly.  These fixed per-cog
 * (one cog in the currently supported UP build) areas are aligned so each
 * absolute Hub access has the same form as the standalone HIL proof.
 */

volatile uint32_t g_p2_irq_area[P2_IRQ_AREA_LONGS]
  aligned_data(512);
volatile uint32_t g_p2_irq_stack[P2_IRQ_STACK_LONGS]
  aligned_data(512);

/* Thread-mode save has a separate scratch frame so INT1 cannot overwrite a
 * partly captured software context before up_saveusercontext executes
 * STALLI.  It has the same resume-then-regs layout as interrupt scratch.
 */

volatile uint32_t g_p2_switch_scratch[P2_CONTEXT_WORDS]
  aligned_data(512);
volatile uint32_t g_p2_thread_restore_pending;

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

extern void p2_irqinitialize_asm(void);
extern void p2_irq_timer_enable(void);
extern void p2_irq_timer_disable(void);
extern void p2_context_trigger_restore(void) noreturn_function;

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static inline struct xcptcontext *p2_xcp_from_regs(xcpt_reg_t *regs)
{
  return (struct xcptcontext *)
    ((uintptr_t)regs - offsetof(struct xcptcontext, regs));
}

static void p2_irq_storage_initialize(void)
{
  unsigned int i;

  for (i = 0; i < P2_IRQ_AREA_GUARD_LONGS; i++)
    {
      g_p2_irq_area[i] = P2_IRQ_GUARD_LOW ^ i;
      g_p2_irq_area[P2_IRQ_AREA_LONGS - 1 - i] =
        P2_IRQ_GUARD_HIGH ^ i;
    }

  for (i = 0; i < P2_IRQ_STACK_GUARD_LONGS; i++)
    {
      g_p2_irq_stack[i] = P2_IRQ_GUARD_LOW ^ (0x100u + i);
      g_p2_irq_stack[P2_IRQ_STACK_LONGS - 1 - i] =
        P2_IRQ_GUARD_HIGH ^ (0x100u + i);
    }

  for (i = 0; i < P2_CONTEXT_WORDS; i++)
    {
      g_p2_irq_area[P2_IRQ_AREA_FRAME_LONG + i] = 0;
      g_p2_switch_scratch[i] = 0;
    }
}

static bool p2_irq_storage_valid(void)
{
  unsigned int i;

  for (i = 0; i < P2_IRQ_AREA_GUARD_LONGS; i++)
    {
      if (g_p2_irq_area[i] != (P2_IRQ_GUARD_LOW ^ i) ||
          g_p2_irq_area[P2_IRQ_AREA_LONGS - 1 - i] !=
            (P2_IRQ_GUARD_HIGH ^ i))
        {
          return false;
        }
    }

  for (i = 0; i < P2_IRQ_STACK_GUARD_LONGS; i++)
    {
      if (g_p2_irq_stack[i] !=
            (P2_IRQ_GUARD_LOW ^ (0x100u + i)) ||
          g_p2_irq_stack[P2_IRQ_STACK_LONGS - 1 - i] !=
            (P2_IRQ_GUARD_HIGH ^ (0x100u + i)))
        {
          return false;
        }
    }

  return true;
}

static void p2_scratch_to_xcp(struct xcptcontext *xcp)
{
  volatile uint32_t *scratch =
    &g_p2_irq_area[P2_IRQ_AREA_FRAME_LONG];
  unsigned int i;

  xcp->regs[P2_REG_RESUME] = *scratch++;
  for (i = 0; i < P2_XCPT_REGS; i++)
    {
      xcp->regs[i] = *scratch++;
    }
}

static void p2_xcp_to_scratch(const struct xcptcontext *xcp)
{
  volatile uint32_t *scratch =
    &g_p2_irq_area[P2_IRQ_AREA_FRAME_LONG];
  unsigned int i;

  *scratch++ = xcp->regs[P2_REG_RESUME];
  for (i = 0; i < P2_XCPT_REGS; i++)
    {
      *scratch++ = xcp->regs[i];
    }
}

static void p2_xcp_to_switch_scratch(const struct xcptcontext *xcp)
{
  volatile uint32_t *scratch = g_p2_switch_scratch;
  unsigned int i;

  *scratch++ = xcp->regs[P2_REG_RESUME];
  for (i = 0; i < P2_XCPT_REGS; i++)
    {
      *scratch++ = xcp->regs[i];
    }
}

static void p2_switch_to_irq_scratch(void)
{
  volatile uint32_t *dest =
    &g_p2_irq_area[P2_IRQ_AREA_FRAME_LONG];
  volatile uint32_t *src = g_p2_switch_scratch;
  unsigned int i;

  for (i = 0; i < P2_CONTEXT_WORDS; i++)
    {
      *dest++ = *src++;
    }
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

void up_irqinitialize(void)
{
  p2_boot_trace("P2K:IRQ:ENTER");
  up_irq_disable();
  g_current_regs = NULL;
  p2_irq_storage_initialize();
  g_p2_thread_restore_pending = 0;
  p2_irqinitialize_asm();
#ifndef CONFIG_SUPPRESS_INTERRUPTS
  /* Match NuttX's interrupt-controller contract before nx_start later saves
   * the live CPU0 idle frame.  Leaving STALLI set here would preserve a
   * permanently masked state when that frame first resumes.
   */

  up_irq_enable();
#endif
  p2_boot_trace("P2K:IRQ:OK");
}

void up_disable_irq(int irq)
{
  if (irq == P2_IRQ_TIMER0)
    {
      p2_irq_timer_disable();
    }
  else
    {
      /* No other P2 event-to-channel mapping is implemented yet. */

      PANIC();
    }
}

void up_enable_irq(int irq)
{
  if (irq == P2_IRQ_TIMER0)
    {
      p2_irq_timer_enable();
    }
  else
    {
      /* No other P2 event-to-channel mapping is implemented yet. */

      PANIC();
    }
}

int up_prioritize_irq(int irq, int priority)
{
  (void)irq;
  (void)priority;
  return -ENOSYS;
}

int up_set_irq_type(int irq, int mode)
{
  (void)irq;
  (void)mode;
  return -ENOSYS;
}

/* Called from p2_int1 on the dedicated interrupt stack.  INT1 is currently
 * bound only to CT1, so its NuttX vector is unambiguous.
 */

void p2_int1_dispatch(void)
{
  struct tcb_s **running_task = &g_running_tasks[0];
  struct tcb_s *rtcb = *running_task;
  struct tcb_s *tcb;
  struct xcptcontext *selected;
  xcpt_reg_t *entry_regs;
  xcpt_reg_t *return_regs;

  DEBUGASSERT(rtcb != NULL);
  DEBUGASSERT(up_current_regs() == NULL);

  if (rtcb == NULL || up_current_regs() != NULL ||
      !p2_irq_storage_valid())
    {
      PANIC();
    }

  /* A thread-mode restore deliberately triggers INT1 while STALLI is set.
   * Discard the old live snapshot (it was committed before the scheduler
   * selected its replacement), move the selected detached frame into fixed
   * restore scratch, and return through the normal busy-channel veneer.
   */

  if (g_p2_thread_restore_pending != 0)
    {
      g_p2_thread_restore_pending = 0;
      p2_switch_to_irq_scratch();
      return;
    }

  p2_scratch_to_xcp(&rtcb->xcp);
  entry_regs = rtcb->xcp.regs;
  up_set_current_regs(entry_regs);

  irq_dispatch(P2_IRQ_TIMER0, entry_regs);

  return_regs = up_current_regs();
  if (return_regs == NULL)
    {
      PANIC();
    }

  if (return_regs != entry_regs)
    {
      tcb = this_task();
      nxsched_switch_context(rtcb, tcb);
      *running_task = tcb;
    }

  selected = p2_xcp_from_regs(return_regs);
  p2_xcp_to_scratch(selected);
  up_set_current_regs(NULL);

  if (!p2_irq_storage_valid())
    {
      PANIC();
    }
}

/* Copy a TCB context into the absolute restore scratch.  The caller has
 * stalled interrupts and deliberately enters INT1.  The selected context is
 * restored only after the channel is busy, making its final ALLOWI/RETI1
 * sequence safe even when CT1 is pending.
 */

void p2_prepare_context_restore(xcpt_reg_t *regs)
{
  DEBUGASSERT(regs != NULL);
  DEBUGASSERT(!up_interrupt_context());

  if (regs == NULL || up_interrupt_context() ||
      !p2_irq_storage_valid())
    {
      PANIC();
    }

  p2_xcp_to_switch_scratch(p2_xcp_from_regs(regs));
  g_p2_thread_restore_pending = 1;
  p2_context_trigger_restore();
}
