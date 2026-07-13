/****************************************************************************
 * arch/p2/src/common/p2_switchcontext.c
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
#include <stdint.h>
#include <string.h>

#include <nuttx/arch.h>
#include <nuttx/irq.h>
#include <nuttx/sched.h>

#include <arch/context.h>
#include <arch/irq.h>

#include "sched/sched.h"
#include "p2_internal.h"

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

extern void p2_prepare_context_restore(xcpt_reg_t *regs) noreturn_function;

#ifdef CONFIG_ENABLE_ALL_SIGNALS
static void p2_sigdeliver(void) noreturn_function;
#endif

/****************************************************************************
 * Private Functions
 ****************************************************************************/

#ifdef CONFIG_ENABLE_ALL_SIGNALS
static void p2_sigdeliver(void)
{
  struct tcb_s *rtcb = this_task();
  sig_deliver_t sigdeliver;

  DEBUGASSERT(rtcb != NULL && rtcb->sigdeliver != NULL);
  sigdeliver = rtcb->sigdeliver;

#ifndef CONFIG_SUPPRESS_INTERRUPTS
  up_irq_enable();
#endif

  sigdeliver(rtcb);

#ifndef CONFIG_SUPPRESS_INTERRUPTS
  up_irq_disable();
#endif

  rtcb->sigdeliver = NULL;
  memcpy(rtcb->xcp.regs, rtcb->xcp.saved_regs, XCPTCONTEXT_SIZE);
  up_fullcontextrestore(rtcb->xcp.regs);
}
#endif

/****************************************************************************
 * Public Functions
 ****************************************************************************/

void up_switch_context(struct tcb_s *tcb, struct tcb_s *rtcb)
{
  DEBUGASSERT(tcb != NULL && rtcb != NULL);

  if (up_interrupt_context())
    {
      /* Interrupt entry already copied the complete detached frame to the
       * old TCB.  Select the new TCB.  p2_int1_dispatch performs scheduler
       * bookkeeping and copies this resume+regs back to restore scratch.
       */

      up_set_current_regs(tcb->xcp.regs);
    }
  else if (!up_saveusercontext(rtcb->xcp.regs))
    {
      nxsched_switch_context(rtcb, tcb);
      g_running_tasks[0] = tcb;
      up_fullcontextrestore(tcb->xcp.regs);
    }
}

void up_fullcontextrestore(void *restoreregs)
{
  DEBUGASSERT(restoreregs != NULL);

  up_irq_disable();
  p2_prepare_context_restore((xcpt_reg_t *)restoreregs);
}

void up_copyfullstate(void *dest, const void *src)
{
  xcpt_reg_t *dregs = dest;
  const xcpt_reg_t *sregs = src;

  DEBUGASSERT(dregs != NULL && sregs != NULL);
  memcpy(dregs, sregs, XCPTCONTEXT_SIZE);
}

#ifdef CONFIG_ENABLE_ALL_SIGNALS
void up_schedule_sigaction(struct tcb_s *tcb)
{
  uintptr_t trampoline = (uintptr_t)p2_sigdeliver;

  DEBUGASSERT(tcb != NULL && tcb->sigdeliver != NULL);

  if (tcb == this_task() && !up_interrupt_context())
    {
      /* NuttX explicitly permits immediate delivery for the running task
       * outside interrupt context.  No saved TCB image is live in this case.
       */

      tcb->sigdeliver(tcb);
      tcb->sigdeliver = NULL;
      return;
    }

  DEBUGASSERT((trampoline & ~P2_RESUME_PC_MASK) == 0);
  memcpy(tcb->xcp.saved_regs, tcb->xcp.regs, XCPTCONTEXT_SIZE);

  tcb->xcp.regs[P2_REG_RESUME] = P2_RESUME_PACK(0, 0, trampoline);
  tcb->xcp.regs[P2_REG_IRQSTATE] = P2_IRQSTATE_STALLED;
}
#endif
