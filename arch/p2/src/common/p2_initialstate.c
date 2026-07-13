/****************************************************************************
 * arch/p2/src/common/p2_initialstate.c
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

#include <nuttx/sched.h>

#include <arch/context.h>

#include "p2_internal.h"

/****************************************************************************
 * Private Data
 ****************************************************************************/

/* The board linker script reserves one contiguous CPU0 allocation between
 * these symbols.  Its low prefix holds idle TLS; its upper subrange is the
 * initial stack containing the live nx_start() CALLA chain.  The first
 * runtime context save replaces this function's synthetic idle PTRA with a
 * PTRA inside that same allocation.  High-water reporting may conservatively
 * include the unused portion between TLS and the live initial stack.
 */

extern uint8_t _sidle_stack[];
extern uint8_t _eidle_stack[];

/****************************************************************************
 * Private Functions
 ****************************************************************************/

/****************************************************************************
 * Name: p2_task_start
 *
 * Description:
 *   Enter a never-before-run NuttX thread without using the temporary PTRA
 *   from its synthetic RETA frame.
 *
 *   NuttX calls up_initial_state() before it allocates TLS and argv at the
 *   low end of the stack.  The final upward-growing stack base is therefore
 *   not available while the initial context is built.  R0 points to the
 *   TCB's stack_base_ptr field and R1 contains tcb->start.  This naked
 *   trampoline loads the final base only after TLS/argv setup is complete,
 *   and then calls the NuttX void start_t routine.
 *
 *   The CALLA also gives an accidentally returning start routine a valid
 *   return slot.  Such a return is fatal, so stop the current cog.
 *
 ****************************************************************************/

static void p2_task_start(void) naked_function;
static void p2_task_start(void)
{
  __asm__ __volatile__(
    "rdlong ptra, r0\n"
    "calla r1\n"
    "cogid r0\n"
    "cogstop r0\n");
}

/****************************************************************************
 * Name: p2_initialstate_fail
 *
 * Description:
 *   Fail before publishing a context that RETA cannot safely consume.
 *
 ****************************************************************************/

static void p2_initialstate_fail(void)
{
  p2_boot_trace("P2K:INITIAL:FAIL");
  PANIC();
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

/****************************************************************************
 * Name: up_initial_state
 *
 * Description:
 *   Initialize a new task's detached packed resume long and 37-long saved
 *   register array.  No architecture frame is written to the task stack.
 *
 ****************************************************************************/

void up_initial_state(FAR struct tcb_s *tcb)
{
  FAR struct xcptcontext *xcp;
  uintptr_t alloc;
  uintptr_t base;
  uintptr_t startup;
  uintptr_t start;
  uintptr_t base_field;
  uintptr_t idle_start;
  uintptr_t idle_end;
  size_t idle_reserved;

  DEBUGASSERT(tcb != NULL);
  if (tcb == NULL)
    {
      p2_initialstate_fail();
      return;
    }

  /* CPU0's contiguous allocation is established by the linker/startup
   * contract rather than by up_create_stack().  Other tasks arrive here with
   * an allocated stack.  The live CALLA chain occupies the upper initial-
   * stack subrange so that low-end idle TLS setup cannot overwrite it.
   */

  if (tcb->pid == IDLE_PROCESS_ID && tcb->stack_alloc_ptr == NULL)
    {
      idle_start = (uintptr_t)_sidle_stack;
      idle_end   = (uintptr_t)_eidle_stack;

      if (idle_end <= idle_start)
        {
          p2_initialstate_fail();
          return;
        }

      idle_reserved = (size_t)(idle_end - idle_start);
      if (idle_reserved < CONFIG_IDLETHREAD_STACKSIZE)
        {
          p2_initialstate_fail();
          return;
        }

      tcb->stack_alloc_ptr = (FAR void *)idle_start;
      tcb->stack_base_ptr  = (FAR void *)idle_start;
      tcb->adj_stack_size  = idle_reserved;
    }

  alloc      = (uintptr_t)tcb->stack_alloc_ptr;
  base       = (uintptr_t)tcb->stack_base_ptr;
  startup    = (uintptr_t)p2_task_start;
  start      = (uintptr_t)tcb->start;
  base_field = (uintptr_t)&tcb->stack_base_ptr;

  if (alloc == 0 || start == 0 || startup == 0 || base < alloc ||
      (alloc & (STACKFRAME_ALIGN - 1)) != 0 ||
      (base & (STACKFRAME_ALIGN - 1)) != 0 ||
      (base_field & (P2_REG_BYTES - 1)) != 0 ||
      (startup & (P2_REG_BYTES - 1)) != 0 ||
      (start & (P2_REG_BYTES - 1)) != 0 ||
      (startup & ~P2_RESUME_PC_MASK) != 0 ||
      (start & ~P2_RESUME_PC_MASK) != 0 ||
      tcb->adj_stack_size < P2_REG_BYTES ||
      base + tcb->adj_stack_size < base)
    {
      p2_initialstate_fail();
      return;
    }

  xcp = &tcb->xcp;
  memset(xcp, 0, sizeof(*xcp));

  xcp->regs[P2_REG_RESUME] = P2_RESUME_PACK(0, 0, startup);

  /* start_t takes no ABI arguments.  nxtask_start()/pthread_start() recover
   * the user entry and argc/argv from the running TCB/TLS.  R0 and R1 are
   * private inputs to p2_task_start and are consumed before that C code.
   */

  xcp->regs[P2_REG_R0]       = (xcpt_reg_t)base_field;
  xcp->regs[P2_REG_R1]       = (xcpt_reg_t)start;
  xcp->regs[P2_REG_PA]       = 0;
  xcp->regs[P2_REG_PB]       = 0;
  /* p2_context_restore subtracts one long before RETI1.  The physical PTRA
   * on entry to p2_task_start is therefore base.  The trampoline reloads it
   * from tcb->stack_base_ptr after NuttX has finalized TLS and argv.
   */

  xcp->regs[P2_REG_PTRA]     = (xcpt_reg_t)(base + P2_REG_BYTES);
  xcp->regs[P2_REG_PTRB]     = 0;
  xcp->regs[P2_REG_IRQSTATE] = 0;
}
