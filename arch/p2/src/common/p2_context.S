/****************************************************************************
 * arch/p2/src/common/p2_context.S
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

#define __ASSEMBLY__ 1
#include <arch/context.h>

#if P2_XCPT_REGS != 37 || P2_CONTEXT_WORDS != 38
#  error "P2 context veneer requires the hardware-proven 37+1 frame"
#endif

        .text

        /* The pinned assembler does not provide these control mnemonics. */

#define P2_RAW_ALLOWI          0xfd604024
#define P2_RAW_STALLI          0xfd604224
#define P2_RAW_SETINT1_CT1     0xfd640225
#define P2_RAW_SETINT1_OFF     0xfd640025
#define P2_RAW_TRGINT1         0xfd604424
#define P2_RAW_RETI1           0xfb3bfff5

        /* Guarded fixed Hub scratch layouts supplied by p2_irq.c. */

#define P2_IRQ_AREA_FRAME          16
#define P2_IRQ_FRAME_RESUME         0
#define P2_IRQ_FRAME_R0             4
#define P2_IRQ_FRAME_PA           132
#define P2_IRQ_FRAME_PB           136
#define P2_IRQ_FRAME_PTRA         140
#define P2_IRQ_FRAME_PTRB         144
#define P2_IRQ_FRAME_IRQSTATE     148
#define P2_IRQ_STACK_BASE          64

        /* Configure IJMP1 while keeping the source masked. */

        .globl p2_irqinitialize_asm
        .type p2_irqinitialize_asm,@function
p2_irqinitialize_asm:
        augs    #0
        mov     ijmp1, ##p2_int1
        .long   P2_RAW_SETINT1_OFF
        reta
        .size p2_irqinitialize_asm, .-p2_irqinitialize_asm

        .globl p2_irq_timer_enable
        .type p2_irq_timer_enable,@function
p2_irq_timer_enable:
        .long   P2_RAW_SETINT1_CT1
        reta
        .size p2_irq_timer_enable, .-p2_irq_timer_enable

        .globl p2_irq_timer_disable
        .type p2_irq_timer_disable,@function
p2_irq_timer_disable:
        .long   P2_RAW_SETINT1_OFF
        reta
        .size p2_irq_timer_disable, .-p2_irq_timer_disable

        /* Program CT1 to first argument + second argument.  ADDCT1 writes
         * the sum back to its D operand, so use caller-clobbered r31 rather
         * than corrupting ABI-preserved argument register r0.
         */

        .globl p2_timer_program
        .type p2_timer_program,@function
p2_timer_program:
        mov     r31, r0
        addct1  r31, r1
        reta
        .size p2_timer_program, .-p2_timer_program

        /* TRGINT1 records a waiting INT1 even while STALLI is active, but the
         * branch itself remains stalled until ALLOWI.  Publish the complete
         * restore scratch and pending flag before reaching this veneer, latch
         * INT1, then allow the already-waiting branch.  Whether the software
         * trigger or a simultaneously pending CT1 wins, p2_int1_dispatch sees
         * the pending restore and replaces IRET1 before returning.  The spin
         * is therefore reachable only if the interrupt setup is defective.
         */

        .globl p2_context_trigger_restore
        .type p2_context_trigger_restore,@function
p2_context_trigger_restore:
        .long   P2_RAW_TRGINT1
        .long   P2_RAW_ALLOWI
.Ltrigger_wait:
        jmp     #\.Ltrigger_wait
        .size p2_context_trigger_restore, .-p2_context_trigger_restore

        /* Real global interrupt-state primitives.  GETBRK D bit 1 and C
         * both report STALLI.  Observe it before changing delivery state.
         * p2llvm returns scalar values in r31 and passes the first argument
         * in r0.
         */

        .globl up_irq_save
        .type up_irq_save,@function
up_irq_save:
        getbrk  r31                    wcz
        and     r31, #P2_IRQSTATE_STALLED
        .long   P2_RAW_STALLI
        reta
        .size up_irq_save, .-up_irq_save

        .globl up_irq_restore
        .type up_irq_restore,@function
up_irq_restore:
        testb   r0, #1                 wc
  if_c  jmp     #\.Lirq_restore_stalled
        .long   P2_RAW_ALLOWI
        reta
.Lirq_restore_stalled:
        .long   P2_RAW_STALLI
        reta
        .size up_irq_restore, .-up_irq_restore

        .globl up_irq_enable
        .type up_irq_enable,@function
up_irq_enable:
        .long   P2_RAW_ALLOWI
        reta
        .size up_irq_enable, .-up_irq_enable

        .globl up_irq_disable
        .type up_irq_disable,@function
up_irq_disable:
        .long   P2_RAW_STALLI
        reta
        .size up_irq_disable, .-up_irq_disable

        /* INT1 entry.  Save every task-visible register through absolute Hub
         * addresses before clobbering one.  Task PTRA is never used as frame
         * storage because outgoing ABI arguments may be live around it.
         */

        .section .text.p2_int1,"ax",@progbits
        .globl p2_int1
        .type p2_int1,@function
p2_int1:
        augs    #0
        wrlong  iret1, ##(g_p2_irq_area + P2_IRQ_AREA_FRAME + \
                          P2_IRQ_FRAME_RESUME)
        setq    #31
        augs    #0
        wrlong  r0, ##(g_p2_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_R0)
        augs    #0
        wrlong  pa, ##(g_p2_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_PA)
        augs    #0
        wrlong  pb, ##(g_p2_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_PB)
        augs    #0
        wrlong  ptra, ##(g_p2_irq_area + P2_IRQ_AREA_FRAME + \
                         P2_IRQ_FRAME_PTRA)
        augs    #0
        wrlong  ptrb, ##(g_p2_irq_area + P2_IRQ_AREA_FRAME + \
                         P2_IRQ_FRAME_PTRB)

        /* Record the logical post-resume PTRA used by the detached context.
         * RETI1 itself does not pop the CALLA-style resume long, so restore
         * subtracts the long after loading this value.
         */

        mov     r0, ptra
        add     r0, #4
        augs    #0
        wrlong  r0, ##(g_p2_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_PTRA)

        getbrk  r0                     wcz
        and     r0, #P2_IRQSTATE_STALLED
        augs    #0
        wrlong  r0, ##(g_p2_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_IRQSTATE)

        augs    #0
        mov     ptra, ##(g_p2_irq_stack + P2_IRQ_STACK_BASE)
        calla   #\p2_int1_dispatch

        /* p2_int1_dispatch leaves the selected TCB context in fixed restore
         * scratch.  Fall through to the one common absolute restore path.
         */

        jmp     #\p2_context_restore
        .size p2_int1, .-p2_int1

        /* Restore a detached context from fixed scratch.  This entry is also
         * used by thread-mode full context restore after C has copied the
         * selected TCB's resume+regs into scratch with interrupts stalled.
         */

        .globl p2_context_restore
        .type p2_context_restore,@function
p2_context_restore:
        augs    #0
        rdlong  r0, ##(g_p2_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_IRQSTATE)
        testb   r0, #1                 wc
        augs    #0
        rdlong  ptrb, ##(g_p2_irq_area + P2_IRQ_AREA_FRAME + \
                         P2_IRQ_FRAME_PTRB)
        augs    #0
        rdlong  pb, ##(g_p2_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_PB)
        augs    #0
        rdlong  pa, ##(g_p2_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_PA)
        setq    #31
        augs    #0
        rdlong  r0, ##(g_p2_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_R0)
        augs    #0
        rdlong  iret1, ##(g_p2_irq_area + P2_IRQ_AREA_FRAME + \
                          P2_IRQ_FRAME_RESUME)
        augs    #0
        rdlong  ptra, ##(g_p2_irq_area + P2_IRQ_AREA_FRAME + \
                         P2_IRQ_FRAME_PTRA)
        sub     ptra, #4
  if_c  jmp     #\.Lcontext_stalled
        .long   P2_RAW_ALLOWI
        jmp     #\.Lcontext_irqstate_done
.Lcontext_stalled:
        .long   P2_RAW_STALLI
.Lcontext_irqstate_done:
        .long   P2_RAW_RETI1
        .size p2_context_restore, .-p2_context_restore

        /* Save a thread-mode CALLA context into a detached TCB frame.  A
         * separate scratch area avoids racing INT1 during the initial
         * register snapshot.  Only after the snapshot is complete may this
         * routine inspect and stall interrupt delivery.
         */

        .section .text.p2_context,"ax",@progbits
        .globl up_saveusercontext
        .type up_saveusercontext,@function
up_saveusercontext:
        /* IRET3 is unused by this INT1-only UP port.  Capture STALLI in C
         * without clobbering a task-visible register, then close the tiny
         * observation window before touching shared software-save scratch.
         * If INT1 lands in that window, RETI1 restores C and execution
         * resumes at STALLI with scratch still untouched.
         */

        getbrk  iret3                   wcz
        .long   P2_RAW_STALLI
        setq    #31
        augs    #0
        wrlong  r0, ##(g_p2_switch_scratch + P2_IRQ_FRAME_R0)
        augs    #0
        wrlong  pa, ##(g_p2_switch_scratch + P2_IRQ_FRAME_PA)
        augs    #0
        wrlong  pb, ##(g_p2_switch_scratch + P2_IRQ_FRAME_PB)
        augs    #0
        wrlong  ptra, ##(g_p2_switch_scratch + P2_IRQ_FRAME_PTRA)
        augs    #0
        wrlong  ptrb, ##(g_p2_switch_scratch + P2_IRQ_FRAME_PTRB)

        sub     ptra, #4
        rdlong  r0, ptra
        add     ptra, #4
        augs    #0
        wrlong  r0, ##(g_p2_switch_scratch + P2_IRQ_FRAME_RESUME)
        mov     r0, #0
  if_c  or      r0, #P2_IRQSTATE_STALLED
        augs    #0
        wrlong  r0, ##(g_p2_switch_scratch + P2_IRQ_FRAME_IRQSTATE)

        /* A restored invocation returns nonzero.  The immediate invocation
         * restores r31 to zero after committing this detached image.
         */

        mov     r0, #1
        augs    #0
        wrlong  r0, ##(g_p2_switch_scratch + P2_IRQ_FRAME_R0 + \
                       P2_REG_R31_OFFSET)

        /* saveregs was the entry value of r0.  Public NuttX contexts keep
         * regs[0..36] first and resume at index 37, while hardware scratch
         * is resume first.  Translate explicitly and stay within the caller's
         * XCPTCONTEXT_SIZE buffer.
         */

        augs    #0
        rdlong  ptrb, ##(g_p2_switch_scratch + P2_IRQ_FRAME_R0)
        augs    #0
        mov     ptra, ##(g_p2_switch_scratch + P2_IRQ_FRAME_R0)
        mov     r1, #P2_XCPT_REGS
.Lsave_copy:
        rdlong  r0, ptra++
        wrlong  r0, ptrb++
        sub     r1, #1                  wz
  if_nz jmp     #\.Lsave_copy
        augs    #0
        rdlong  r0, ##(g_p2_switch_scratch + P2_IRQ_FRAME_RESUME)
        wrlong  r0, ptrb

        /* Return normally from this first save while reproducing all saved
         * state.  RETA necessarily consumes this function's ordinary CALLA
         * word; subsequent context restores use detached IRET1 exclusively.
         */

        augs    #0
        rdlong  r0, ##(g_p2_switch_scratch + P2_IRQ_FRAME_IRQSTATE)
        testb   r0, #1                 wc
        augs    #0
        rdlong  ptrb, ##(g_p2_switch_scratch + P2_IRQ_FRAME_PTRB)
        augs    #0
        rdlong  pb, ##(g_p2_switch_scratch + P2_IRQ_FRAME_PB)
        augs    #0
        rdlong  pa, ##(g_p2_switch_scratch + P2_IRQ_FRAME_PA)
        setq    #31
        augs    #0
        rdlong  r0, ##(g_p2_switch_scratch + P2_IRQ_FRAME_R0)
        mov     r31, #0
        augs    #0
        rdlong  ptra, ##(g_p2_switch_scratch + P2_IRQ_FRAME_PTRA)
  if_c  jmp     #\.Lsave_stalled
        .long   P2_RAW_ALLOWI
        reta
.Lsave_stalled:
        .long   P2_RAW_STALLI
        reta
        .size up_saveusercontext, .-up_saveusercontext
