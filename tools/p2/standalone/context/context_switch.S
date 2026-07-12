/****************************************************************************
 * tools/p2/standalone/context/context_switch.S
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Native Propeller 2 INT1 save/switch/restore veneer.
 *
 ****************************************************************************/

#define __ASSEMBLY__ 1
#include <arch/p2/include/context.h>

#if P2_XCPT_REGS != 37
#  error "standalone context proof requires the 37-long P2 contract"
#endif

        .text

        /* The pinned assembler lacks these control mnemonics. */

#define P2_RAW_ALLOWI          0xfd604024
#define P2_RAW_STALLI          0xfd604224
#define P2_RAW_SETINT1_CT1     0xfd640225
#define P2_RAW_SETINT1_OFF     0xfd640025
#define P2_RAW_RETI1           0xfb3bfff5

        /*
         * Configure IJMP1 and select CT1 as the INT1 source.  r0 is the Hub
         * address of p2_context_int1.  CT1 itself is programmed by C before
         * this function is called.  Keep global delivery stalled until
         * p2_context_start has selected a synthetic task stack.
         */

        .section .text.p2_context_control,"ax",@progbits
        .globl p2_context_timer_enable
        .type p2_context_timer_enable,@function
p2_context_timer_enable:
        mov     ijmp1, r0
        .long   P2_RAW_SETINT1_CT1
        reta
        .size p2_context_timer_enable, .-p2_context_timer_enable

        /* Mask the CT1 source from the dispatcher at exactly switch 1M. */

        .globl p2_context_timer_mask
        .type p2_context_timer_mask,@function
p2_context_timer_mask:
        .long   P2_RAW_SETINT1_OFF
        reta
        .size p2_context_timer_mask, .-p2_context_timer_mask

        /* Permanently quiesce interrupt delivery before final reporting. */

        .globl p2_context_timer_quiesce
        .type p2_context_timer_quiesce,@function
p2_context_timer_quiesce:
        .long   P2_RAW_STALLI
        .long   P2_RAW_SETINT1_OFF
        reta
        .size p2_context_timer_quiesce, .-p2_context_timer_quiesce

        /*
         * Start a synthetic context outside interrupt state.  r0 points one
         * long beyond the 37-entry context.  RETA consumes the separate
         * packed resume word at [saved PTRA - 4].
         */

        .globl p2_context_start
        .type p2_context_start,@function
p2_context_start:
        mov     ptra, r0
        rdlong  r0, --ptra             /* P2_REG_IRQSTATE */
        testb   r0, #1                 wc
        rdlong  ptrb, --ptra
        sub     ptra, #4               /* P2_REG_PTRA: RETA owns its pop */
        rdlong  pb, --ptra
        rdlong  pa, --ptra
        setq    #31
        rdlong  r0, --ptra
  if_c  jmp     #\.Lstart_stalled
        .long   P2_RAW_ALLOWI
        reta
.Lstart_stalled:
        .long   P2_RAW_STALLI
        reta
        .size p2_context_start, .-p2_context_start

        /* Fixed guarded Hub scratch layout.  The task PTRA is never used for
         * interrupt storage: an interrupt may arrive while outgoing stack
         * arguments are live below an as-yet-unadvanced PTRA.
         */

#define P2_IRQ_AREA_FRAME          16
#define P2_IRQ_FRAME_RESUME         0
#define P2_IRQ_FRAME_R0             4
#define P2_IRQ_FRAME_PA           132
#define P2_IRQ_FRAME_PB           136
#define P2_IRQ_FRAME_PTRA         140
#define P2_IRQ_FRAME_PTRB         144
#define P2_IRQ_FRAME_IRQSTATE     148
#define P2_IRQ_STACK_BASE          64

        .section .text.p2_context_isr,"ax",@progbits
        .globl p2_context_int1
        .type p2_context_int1,@function
p2_context_int1:
        augs    #0
        wrlong  iret1, ##(g_p2_context_irq_area + P2_IRQ_AREA_FRAME + \
                          P2_IRQ_FRAME_RESUME)
        setq    #31
        augs    #0
        wrlong  r0, ##(g_p2_context_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_R0)
        augs    #0
        wrlong  pa, ##(g_p2_context_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_PA)
        augs    #0
        wrlong  pb, ##(g_p2_context_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_PB)
        augs    #0
        wrlong  ptra, ##(g_p2_context_irq_area + P2_IRQ_AREA_FRAME + \
                         P2_IRQ_FRAME_PTRA)
        augs    #0
        wrlong  ptrb, ##(g_p2_context_irq_area + P2_IRQ_AREA_FRAME + \
                         P2_IRQ_FRAME_PTRB)

        /* All task-visible registers are now safe in fixed Hub scratch. */

        mov     r0, ptra
        add     r0, #4
        augs    #0
        wrlong  r0, ##(g_p2_context_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_PTRA)

        /* GETBRK r0 WCZ.  D bit 1 and C both report STALLI state. */

        getbrk  r0                     wcz
        and     r0, #P2_IRQSTATE_STALLED
        augs    #0
        wrlong  r0, ##(g_p2_context_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_IRQSTATE)

        augs    #0
        mov     ptra, ##(g_p2_context_irq_stack + P2_IRQ_STACK_BASE)
        calla   #\p2_context_dispatch

        /* The dispatcher has copied the selected detached frame back into
         * fixed restore scratch.  Preserve its interrupt-state decision in C
         * while restoring every task-visible register by absolute Hub loads.
         */

        augs    #0
        rdlong  r0, ##(g_p2_context_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_IRQSTATE)
        testb   r0, #1                 wc
        augs    #0
        rdlong  ptrb, ##(g_p2_context_irq_area + P2_IRQ_AREA_FRAME + \
                         P2_IRQ_FRAME_PTRB)
        augs    #0
        rdlong  pb, ##(g_p2_context_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_PB)
        augs    #0
        rdlong  pa, ##(g_p2_context_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_PA)
        setq    #31
        augs    #0
        rdlong  r0, ##(g_p2_context_irq_area + P2_IRQ_AREA_FRAME + \
                       P2_IRQ_FRAME_R0)
        augs    #0
        rdlong  iret1, ##(g_p2_context_irq_area + P2_IRQ_AREA_FRAME + \
                          P2_IRQ_FRAME_RESUME)
        augs    #0
        rdlong  ptra, ##(g_p2_context_irq_area + P2_IRQ_AREA_FRAME + \
                         P2_IRQ_FRAME_PTRA)
        sub     ptra, #4
  if_c  jmp     #\.Lisr_stalled
        .long   P2_RAW_ALLOWI
        jmp     #\.Lisr_irqstate_done
.Lisr_stalled:
        .long   P2_RAW_STALLI
.Lisr_irqstate_done:
        .long   P2_RAW_RETI1
        .size p2_context_int1, .-p2_context_int1

        /*
         * Hold distinct fixed values in r4-r29 across at least eight real
         * CT1 switches.  The entry ABI supplies task id in r0 and the shared
         * switch-counter address in r1.  Preserve every callee-saved r0-r29
         * value owned by the C caller; return zero/one in r31.
         */

        .section .text.p2_context_control,"ax",@progbits
        .globl p2_context_register_window
        .type p2_context_register_window,@function
p2_context_register_window:
        setq    #29
        wrlong  r0, ptra++
        tjnz    r0, #.Lpattern_task1

        mov     r4, #260
        mov     r5, #261
        mov     r6, #262
        mov     r7, #263
        mov     r8, #264
        mov     r9, #265
        mov     r10, #266
        mov     r11, #267
        mov     r12, #268
        mov     r13, #269
        mov     r14, #270
        mov     r15, #271
        mov     r16, #272
        mov     r17, #273
        mov     r18, #274
        mov     r19, #275
        mov     r20, #276
        mov     r21, #277
        mov     r22, #278
        mov     r23, #279
        mov     r24, #280
        mov     r25, #281
        mov     r26, #282
        mov     r27, #283
        mov     r28, #284
        mov     r29, #285
        jmp     #\.Lpattern_wait

.Lpattern_task1:
        mov     r4, #388
        mov     r5, #389
        mov     r6, #390
        mov     r7, #391
        mov     r8, #392
        mov     r9, #393
        mov     r10, #394
        mov     r11, #395
        mov     r12, #396
        mov     r13, #397
        mov     r14, #398
        mov     r15, #399
        mov     r16, #400
        mov     r17, #401
        mov     r18, #402
        mov     r19, #403
        mov     r20, #404
        mov     r21, #405
        mov     r22, #406
        mov     r23, #407
        mov     r24, #408
        mov     r25, #409
        mov     r26, #410
        mov     r27, #411
        mov     r28, #412
        mov     r29, #413

.Lpattern_wait:
        rdlong  r2, r1
        add     r2, #8
        mov     r0, #0                   /* full eight-switch window */
        cmp     r2, ##1000000            wcz
  if_c_or_z jmp #\.Lpattern_wait_loop
        mov     r2, ##1000000
        mov     r0, #2                   /* terminal clamp, not a proof */
.Lpattern_wait_loop:
        rdlong  r3, r1
        cmp     r3, r2                  wcz
  if_c  jmp     #\.Lpattern_wait_loop

        cmp     r4, #260                wcz
  if_z  jmp     #\.Lpattern_check_task0
        cmp     r4, #388                wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r5, #389                wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r6, #390                wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r7, #391                wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r8, #392                wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r9, #393                wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r10, #394               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r11, #395               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r12, #396               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r13, #397               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r14, #398               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r15, #399               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r16, #400               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r17, #401               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r18, #402               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r19, #403               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r20, #404               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r21, #405               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r22, #406               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r23, #407               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r24, #408               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r25, #409               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r26, #410               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r27, #411               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r28, #412               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r29, #413               wcz
  if_nz jmp     #\.Lpattern_fail
        jmp     #\.Lpattern_pass

.Lpattern_check_task0:
        cmp     r5, #261                wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r6, #262                wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r7, #263                wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r8, #264                wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r9, #265                wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r10, #266               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r11, #267               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r12, #268               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r13, #269               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r14, #270               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r15, #271               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r16, #272               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r17, #273               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r18, #274               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r19, #275               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r20, #276               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r21, #277               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r22, #278               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r23, #279               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r24, #280               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r25, #281               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r26, #282               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r27, #283               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r28, #284               wcz
  if_nz jmp     #\.Lpattern_fail
        cmp     r29, #285               wcz
  if_nz jmp     #\.Lpattern_fail

.Lpattern_pass:
        mov     r31, r0
        jmp     #\.Lpattern_restore
.Lpattern_fail:
        mov     r31, #1
.Lpattern_restore:
        setq    #29
        rdlong  r0, --ptra
        reta
        .size p2_context_register_window, .-p2_context_register_window
