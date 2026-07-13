/****************************************************************************
 * arch/p2/src/common/p2_asserts.c
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

#include <stddef.h>
#include <stdint.h>

#include <nuttx/sched.h>

#include <arch/context.h>

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

/* The current board configurations are flat builds.  Protected/kernel
 * builds still need an architecture user-entry trampoline (up_task_start,
 * up_pthread_start, and signal return) before they can be enabled honestly.
 */

#ifndef CONFIG_BUILD_FLAT
#  error "P2 protected/kernel task-entry trampoline is not implemented"
#endif

/****************************************************************************
 * Private Data
 ****************************************************************************/

static_assert(sizeof(xcpt_reg_t) == 4,
              "P2 saved registers must be 32 bits");
static_assert(sizeof(uintptr_t) == sizeof(xcpt_reg_t),
              "P2 pointers must fit in a saved register");
static_assert(sizeof(start_t) == sizeof(xcpt_reg_t),
              "P2 start function must fit in a saved register");
static_assert(sizeof(((struct tcb_s *)0)->stack_base_ptr) ==
              sizeof(xcpt_reg_t),
              "P2 stack pointer must fit in a saved register");

static_assert(P2_XCPT_REGS == 37, "P2 context register count changed");
static_assert(P2_REG_R0 == 0 && P2_REG_R31 == 31,
              "P2 general-register range changed");
static_assert(P2_REG_PA_OFFSET == 128, "P2 PA offset changed");
static_assert(P2_REG_PB_OFFSET == 132, "P2 PB offset changed");
static_assert(P2_REG_PTRA_OFFSET == 136, "P2 PTRA offset changed");
static_assert(P2_REG_PTRB_OFFSET == 140, "P2 PTRB offset changed");
static_assert(P2_REG_IRQSTATE_OFFSET == 144,
              "P2 interrupt-state offset changed");
static_assert(P2_XCPT_SIZE == 148, "P2 context byte size changed");
static_assert(P2_CONTEXT_WORDS == 38,
              "P2 detached resume plus register count changed");
static_assert(P2_CONTEXT_SIZE == 152,
              "P2 detached resume plus register size changed");

static_assert(offsetof(struct xcptcontext, regs[P2_REG_PA]) -
              offsetof(struct xcptcontext, regs[P2_REG_R0]) ==
              P2_REG_PA_OFFSET,
              "P2 C and PASM2 PA layouts disagree");
static_assert(offsetof(struct xcptcontext, regs[P2_REG_PTRA]) -
              offsetof(struct xcptcontext, regs[P2_REG_R0]) ==
              P2_REG_PTRA_OFFSET,
              "P2 C and PASM2 PTRA layouts disagree");
static_assert(offsetof(struct xcptcontext, regs[P2_REG_IRQSTATE]) -
              offsetof(struct xcptcontext, regs[P2_REG_R0]) ==
              P2_REG_IRQSTATE_OFFSET,
              "P2 C and PASM2 IRQ-state layouts disagree");
static_assert(sizeof(((struct xcptcontext *)0)->regs) ==
              XCPTCONTEXT_SIZE,
              "P2 TCB context array has the wrong size");
static_assert(offsetof(struct xcptcontext, regs[P2_REG_RESUME]) -
              offsetof(struct xcptcontext, regs[P2_REG_R0]) ==
              P2_XCPT_SIZE,
              "P2 public resume does not follow architectural regs");
static_assert(XCPTCONTEXT_REGS == P2_CONTEXT_WORDS,
              "P2 public save buffer does not hold the 37+1 frame");
static_assert(XCPTCONTEXT_SIZE == P2_CONTEXT_SIZE,
              "P2 public save buffer has the wrong byte size");

static_assert(STACKFRAME_ALIGN == P2_REG_BYTES,
              "P2 stack and register alignment disagree");
static_assert((STACKFRAME_ALIGN & (STACKFRAME_ALIGN - 1)) == 0,
              "P2 stack alignment must be a power of two");
static_assert(CONFIG_IDLETHREAD_STACKSIZE >= P2_REG_BYTES,
              "P2 idle TCB stack cannot establish an aligned PTRA");
static_assert((CONFIG_IDLETHREAD_STACKSIZE &
               (STACKFRAME_ALIGN - 1)) == 0,
              "P2 idle TCB stack size is not aligned");
static_assert(P2_RESUME_STACK_OFFSET == -P2_REG_BYTES,
              "P2 logical-to-physical PTRA adjustment changed");
static_assert((P2_RESUME_PC_MASK | P2_RESUME_RESERVED_MASK |
               P2_RESUME_Z | P2_RESUME_C) == UINT32_MAX,
              "P2 resume fields do not cover one long");
static_assert((P2_RESUME_PC_MASK & (P2_RESUME_RESERVED_MASK |
               P2_RESUME_Z | P2_RESUME_C)) == 0,
              "P2 resume PC overlaps reserved or flag bits");
static_assert(CONFIG_RAM_SIZE <= P2_RESUME_PC_MASK + 1,
              "P2 Hub RAM exceeds CALLA/RETA PC range");

/****************************************************************************
 * Public Functions
 ****************************************************************************/
