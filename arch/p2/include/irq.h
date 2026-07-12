/****************************************************************************
 * arch/p2/include/irq.h
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

/* This file should never be included directly but, rather, only indirectly
 * through nuttx/irq.h.
 */

#ifndef __ARCH_P2_INCLUDE_IRQ_H
#define __ARCH_P2_INCLUDE_IRQ_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <arch/context.h>

#ifndef __ASSEMBLY__
#  include <sys/types.h>
#  include <stdbool.h>
#  include <stddef.h>
#  include <stdint.h>

#  include <arch/types.h>
#endif

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

/* P2 events are routed to the three cog interrupt channels by SETINT1/2/3.
 * The channel number is not itself a NuttX IRQ number.  The first two
 * logical vectors are allocated below.  Slots 2 through 15 are reserved for
 * explicit architecture event allocations as lower halves are implemented.
 * Keeping those slots reserved avoids pretending that all Smart Pin/event
 * selectors already have working interrupt-controller mappings.
 */

#define P2_IRQ_TIMER0                   0
#define P2_IRQ_UART0                    1
#define P2_IRQ_FIRST_RESERVED           2
#define P2_IRQ_NIRQS                   16
#define NR_IRQS                 P2_IRQ_NIRQS

/****************************************************************************
 * Public Types
 ****************************************************************************/

#ifndef __ASSEMBLY__

/* This structure is included in the TCB and holds the complete fixed P2
 * context.  Unlike ports whose exception frames live on a downward-growing
 * stack, this UP port keeps the 37-long register array inline.
 */

struct xcptcontext
{
#ifdef CONFIG_ENABLE_ALL_SIGNALS
  /* Preserve a complete context while the signal-delivery trampoline owns
   * regs.  There is one save area per TCB, so only one signal handler may be
   * active for a task at a time.  The packed C/Z/PC resume long is outside
   * the register array at [saved PTRA - 4] and must be saved separately.
   */

  xcpt_reg_t saved_regs[P2_XCPT_REGS];
  xcpt_reg_t saved_resume;

#ifndef CONFIG_BUILD_FLAT
  /* User-space signal return address for protected/kernel builds.  Those
   * build modes are not selected by the current P2 board configurations.
   */

  uintptr_t sigreturn;
#endif
#endif /* CONFIG_ENABLE_ALL_SIGNALS */

  xcpt_reg_t regs[P2_XCPT_REGS];
};

static_assert(sizeof(((struct xcptcontext *)0)->regs) == XCPTCONTEXT_SIZE,
              "P2 TCB register area does not match the assembly frame");
#  ifdef CONFIG_ENABLE_ALL_SIGNALS
static_assert(sizeof(((struct xcptcontext *)0)->saved_regs) ==
              XCPTCONTEXT_SIZE,
              "P2 signal save area does not hold a complete frame");
#  endif

/****************************************************************************
 * Public Data
 ****************************************************************************/

#ifdef __cplusplus
#  define EXTERN extern "C"
extern "C"
{
#else
#  define EXTERN extern
#endif

/* g_current_regs is non-NULL only while this UP port is processing an
 * interrupt.  Access it through up_current_regs()/up_set_current_regs().
 */

EXTERN volatile xcpt_reg_t *g_current_regs;

/****************************************************************************
 * Public Function Prototypes
 ****************************************************************************/

irqstate_t up_irq_save(void) noinstrument_function;
void up_irq_restore(irqstate_t flags) noinstrument_function;
void up_irq_enable(void) noinstrument_function;
void up_irq_disable(void) noinstrument_function;

/****************************************************************************
 * Inline Functions
 ****************************************************************************/

noinstrument_function
static inline_function xcpt_reg_t *up_current_regs(void)
{
  return (xcpt_reg_t *)g_current_regs;
}

noinstrument_function
static inline_function void up_set_current_regs(xcpt_reg_t *regs)
{
  g_current_regs = regs;
}

noinstrument_function
static inline_function bool up_interrupt_context(void)
{
  return up_current_regs() != NULL;
}

/* Return the current upward-growing p2llvm stack pointer. */

noinstrument_function
static inline_function uintptr_t up_getsp(void)
{
  uintptr_t sp;

  __asm__ __volatile__("mov %0, ptra" : "=r" (sp));
  return sp;
}

/* Resolve an explicit register array, or the current interrupt array when
 * the caller passes NULL.  As on other NuttX ports, NULL is valid only in an
 * interrupt context where g_current_regs is non-NULL.
 */

noinstrument_function
static inline_function xcpt_reg_t *p2_get_context_regs(void *regs)
{
  return regs != NULL ? (xcpt_reg_t *)regs : up_current_regs();
}

/* Return the PC encoded in the CALLA/RETA resume long.  There is
 * deliberately no P2_REG_PC index: the resume long is stored at
 * [saved PTRA - 4].
 */

noinstrument_function
static inline_function uintptr_t up_getusrpc(void *regs)
{
  xcpt_reg_t *context = p2_get_context_regs(regs);
  uintptr_t resume_addr;

  resume_addr = (uintptr_t)context[P2_REG_PTRA] +
                P2_RESUME_STACK_OFFSET;
  return (uintptr_t)(*(xcpt_reg_t *)resume_addr & P2_RESUME_PC_MASK);
}

noinstrument_function
static inline_function uintptr_t up_getusrsp(void *regs)
{
  xcpt_reg_t *context = p2_get_context_regs(regs);

  return (uintptr_t)context[P2_REG_PTRA];
}

#undef EXTERN
#ifdef __cplusplus
}
#endif
#endif /* __ASSEMBLY__ */

#endif /* __ARCH_P2_INCLUDE_IRQ_H */
