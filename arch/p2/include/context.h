/****************************************************************************
 * arch/p2/include/context.h
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

#ifndef __ARCH_P2_INCLUDE_CONTEXT_H
#define __ARCH_P2_INCLUDE_CONTEXT_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#ifndef __ASSEMBLY__
#  include <assert.h>
#endif

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

/* P2 interrupt and task context layout.
 *
 * This header is the source of truth shared by C and PASM2.  Keep all frame
 * constants valid integer expressions for an assembly source preprocessed
 * with __ASSEMBLY__ defined.
 *
 * CALLA writes the packed C/Z/PC resume long to Hub RAM at PTRA and advances
 * PTRA by four.  RETA consumes that long from [PTRA - 4].  Therefore the
 * packed resume long is part of the upward-growing task stack, not a slot in
 * the register array.  P2_REG_PTRA always records the post-CALLA value.
 */

#define P2_REG_R0                    0
#define P2_REG_R1                    1
#define P2_REG_R2                    2
#define P2_REG_R3                    3
#define P2_REG_R4                    4
#define P2_REG_R5                    5
#define P2_REG_R6                    6
#define P2_REG_R7                    7
#define P2_REG_R8                    8
#define P2_REG_R9                    9
#define P2_REG_R10                  10
#define P2_REG_R11                  11
#define P2_REG_R12                  12
#define P2_REG_R13                  13
#define P2_REG_R14                  14
#define P2_REG_R15                  15
#define P2_REG_R16                  16
#define P2_REG_R17                  17
#define P2_REG_R18                  18
#define P2_REG_R19                  19
#define P2_REG_R20                  20
#define P2_REG_R21                  21
#define P2_REG_R22                  22
#define P2_REG_R23                  23
#define P2_REG_R24                  24
#define P2_REG_R25                  25
#define P2_REG_R26                  26
#define P2_REG_R27                  27
#define P2_REG_R28                  28
#define P2_REG_R29                  29
#define P2_REG_R30                  30
#define P2_REG_R31                  31
#define P2_REG_PA                   32
#define P2_REG_PB                   33
#define P2_REG_PTRA                 34
#define P2_REG_PTRB                 35
#define P2_REG_IRQSTATE             36

#define P2_XCPT_REGS                37
#define XCPTCONTEXT_REGS            P2_XCPT_REGS

#define P2_REG_BYTES                 4
#define P2_REG_OFFSET(r)            ((r) * P2_REG_BYTES)

#define P2_REG_R0_OFFSET            P2_REG_OFFSET(P2_REG_R0)
#define P2_REG_R1_OFFSET            P2_REG_OFFSET(P2_REG_R1)
#define P2_REG_R2_OFFSET            P2_REG_OFFSET(P2_REG_R2)
#define P2_REG_R3_OFFSET            P2_REG_OFFSET(P2_REG_R3)
#define P2_REG_R4_OFFSET            P2_REG_OFFSET(P2_REG_R4)
#define P2_REG_R5_OFFSET            P2_REG_OFFSET(P2_REG_R5)
#define P2_REG_R6_OFFSET            P2_REG_OFFSET(P2_REG_R6)
#define P2_REG_R7_OFFSET            P2_REG_OFFSET(P2_REG_R7)
#define P2_REG_R8_OFFSET            P2_REG_OFFSET(P2_REG_R8)
#define P2_REG_R9_OFFSET            P2_REG_OFFSET(P2_REG_R9)
#define P2_REG_R10_OFFSET           P2_REG_OFFSET(P2_REG_R10)
#define P2_REG_R11_OFFSET           P2_REG_OFFSET(P2_REG_R11)
#define P2_REG_R12_OFFSET           P2_REG_OFFSET(P2_REG_R12)
#define P2_REG_R13_OFFSET           P2_REG_OFFSET(P2_REG_R13)
#define P2_REG_R14_OFFSET           P2_REG_OFFSET(P2_REG_R14)
#define P2_REG_R15_OFFSET           P2_REG_OFFSET(P2_REG_R15)
#define P2_REG_R16_OFFSET           P2_REG_OFFSET(P2_REG_R16)
#define P2_REG_R17_OFFSET           P2_REG_OFFSET(P2_REG_R17)
#define P2_REG_R18_OFFSET           P2_REG_OFFSET(P2_REG_R18)
#define P2_REG_R19_OFFSET           P2_REG_OFFSET(P2_REG_R19)
#define P2_REG_R20_OFFSET           P2_REG_OFFSET(P2_REG_R20)
#define P2_REG_R21_OFFSET           P2_REG_OFFSET(P2_REG_R21)
#define P2_REG_R22_OFFSET           P2_REG_OFFSET(P2_REG_R22)
#define P2_REG_R23_OFFSET           P2_REG_OFFSET(P2_REG_R23)
#define P2_REG_R24_OFFSET           P2_REG_OFFSET(P2_REG_R24)
#define P2_REG_R25_OFFSET           P2_REG_OFFSET(P2_REG_R25)
#define P2_REG_R26_OFFSET           P2_REG_OFFSET(P2_REG_R26)
#define P2_REG_R27_OFFSET           P2_REG_OFFSET(P2_REG_R27)
#define P2_REG_R28_OFFSET           P2_REG_OFFSET(P2_REG_R28)
#define P2_REG_R29_OFFSET           P2_REG_OFFSET(P2_REG_R29)
#define P2_REG_R30_OFFSET           P2_REG_OFFSET(P2_REG_R30)
#define P2_REG_R31_OFFSET           P2_REG_OFFSET(P2_REG_R31)
#define P2_REG_PA_OFFSET            P2_REG_OFFSET(P2_REG_PA)
#define P2_REG_PB_OFFSET            P2_REG_OFFSET(P2_REG_PB)
#define P2_REG_PTRA_OFFSET          P2_REG_OFFSET(P2_REG_PTRA)
#define P2_REG_PTRB_OFFSET          P2_REG_OFFSET(P2_REG_PTRB)
#define P2_REG_IRQSTATE_OFFSET      P2_REG_OFFSET(P2_REG_IRQSTATE)

#define P2_XCPT_SIZE                (P2_XCPT_REGS * P2_REG_BYTES)
#define XCPTCONTEXT_SIZE            P2_XCPT_SIZE

/* The p2llvm ABI requires long alignment for the upward-growing stack. */

#define STACKFRAME_ALIGN             4

/* GETBRK with WC reports the current STALLI state through C.  The interrupt
 * entry code normalizes that C result into bit 1 of P2_REG_IRQSTATE.
 * Restore code tests this bit to select STALLI or ALLOWI; a second frame
 * slot is not necessary.
 */

#define P2_IRQSTATE_STALLED          (1 << 1)

/* CALLA/RETA packed resume-long format. */

#define P2_RESUME_STACK_OFFSET       (-P2_REG_BYTES)
#define P2_RESUME_PC_MASK            0x000fffff
#define P2_RESUME_RESERVED_MASK      0x3ff00000
#define P2_RESUME_Z                  (1 << 30)
#define P2_RESUME_C                  (1 << 31)

#define P2_RESUME_PACK(c, z, pc) \
  (((c) ? P2_RESUME_C : 0) | ((z) ? P2_RESUME_Z : 0) | \
   ((pc) & P2_RESUME_PC_MASK))

/* A synthetic new-task context follows the same RETA contract: startup code
 * places P2_RESUME_PACK(0, 0, entry_pc) in the last allocated stack long
 * and records the next free address in P2_REG_PTRA.  Context restore can
 * then use the normal RETA path for both interrupted and never-before-run
 * tasks.
 */

#ifndef __ASSEMBLY__
static_assert(P2_REG_R31 == 31, "P2 general-register layout changed");
static_assert(P2_REG_PA_OFFSET == 128, "P2 PA offset changed");
static_assert(P2_REG_PTRA_OFFSET == 136, "P2 PTRA offset changed");
static_assert(P2_REG_IRQSTATE_OFFSET == 144,
              "P2 interrupt-state offset changed");
static_assert(P2_XCPT_SIZE == 148, "P2 context size changed");
static_assert((P2_XCPT_SIZE % STACKFRAME_ALIGN) == 0,
              "P2 context is not stack aligned");
#endif

#endif /* __ARCH_P2_INCLUDE_CONTEXT_H */
