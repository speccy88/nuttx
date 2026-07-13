/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_psram_service.S
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

#include "p2_ec32mb_psram_wire.h"

        .text

        /* Start one free cog in HUBEXEC_NEW mode.  The new cog installs a
         * dedicated guarded PTRA before it enters C; it never becomes a NuttX
         * scheduler CPU and never uses a task's stack.  Return the allocated
         * cog ID.  COGINIT writes 0xf to its D register when no cog is free,
         * which is outside the valid 0..7 range and is returned unchanged.
         */

        .globl p2_psram_cog_start
        .type p2_psram_cog_start,@function
p2_psram_cog_start:
        setq    #1
        wrlong  r0, ptra++
        mov     r0, #0x30
        augs    #0
        mov     r1, ##p2_psram_cog_entry
        coginit r0, r1                  wc
        mov     r31, r0
        setq    #1
        rdlong  r0, --ptra
        reta
        .size p2_psram_cog_start, .-p2_psram_cog_start

        .globl p2_psram_cog_entry
        .type p2_psram_cog_entry,@function
p2_psram_cog_entry:
        augs    #0
        mov     ptra, ##g_p2_psram_service_stack
        add     ptra, #4
        calla   #\p2_psram_service_worker
        cogid   r0
        cogstop r0
        .size p2_psram_cog_entry, .-p2_psram_cog_entry

        /* One output clock.  The caller supplies a replicated 16-bit nibble
         * value in the macro register.  P40 starts at OUTB bit 8.
         */

        .macro  p2_psram_clock_out value
        and     \value, ##0xffff
        shl     \value, #8
        mov     outb, \value
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        outh    #P2_PSRAM_CLOCK_PIN
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        outl    #P2_PSRAM_CLOCK_PIN
        .endm

        .macro  p2_psram_clock_dummy
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        outh    #P2_PSRAM_CLOCK_PIN
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        outl    #P2_PSRAM_CLOCK_PIN
        .endm

        /* PSRAM read data becomes valid after the falling clock edge.  Leave
         * a full low half-period before sampling the four parallel lanes.
         */

        .macro  p2_psram_clock_in result
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        outh    #P2_PSRAM_CLOCK_PIN
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        outl    #P2_PSRAM_CLOCK_PIN
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        mov     \result, inb
        shr     \result, #8
        zerox   \result, #15
        .endm

        /* Record one CE-low interval.  The C half rejects any interval above
         * 1,440 system clocks (8 us at the fixed 180-MHz board clock).
         */

        .macro  p2_psram_ce_end
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        outh    #P2_PSRAM_CE_PIN
        getct   r4
        sub     r4, r5
        cmp     r3, r4                    wc
  if_c  mov     r3, r4
        mov     dirb, r7
        .endm

        /* Fixed single-word QPI timing leaf.  A read keeps CE low for 15
         * clocks (command/address, five standalone wait clocks, two sampled
         * clocks).  The first sampled clock is also the sixth wait clock.  A
         * write uses ten clocks.  Splitting all transfers into four-byte
         * words deliberately trades throughput for a hard refresh bound.
         *
         * p2llvm treats r0-r29 as callee-saved.  Save every register used by
         * this hand-written leaf; r31 is the scalar return register and this
         * function returns void.
         */

        .globl p2_psram_timing_leaf
        .type p2_psram_timing_leaf,@function
p2_psram_timing_leaf:
        setq    #15
        wrlong  r0, ptra++

        augs    #0
        mov     r15, ##g_p2_psram_wire
        rdlong  r14, r15
        mov     r0, r15
        add     r0, #P2_PSRAM_WIRE_ADDRESS_OFFSET
        rdlong  r13, r0

        /* This service cog drives no other Port-B pins.  DIRB therefore
         * remains zero outside P40-P57, while OUTB may be replaced as one
         * register without touching another cog's pin contributions.
         */

        mov     r7, ##0x03000000          /* P56 CLK plus P57 CE */
        mov     r6, ##0x03ffff00          /* P40-P57 */
        mov     r3, #0                    /* max CE-low cycles */

        cmp     r14, #P2_PSRAM_WIRE_RECOVER wz
  if_z  jmp     #.Lrecover
        cmp     r14, #P2_PSRAM_WIRE_READ_WORD wz
  if_z  jmp     #.Lread_word
        cmp     r14, #P2_PSRAM_WIRE_WRITE_WORD wz
  if_z  jmp     #.Lwrite_word
        cmp     r14, #P2_PSRAM_WIRE_SAFE wz
  if_z  jmp     #.Lsafe
        mov     r10, ##-22                /* -EINVAL */
        jmp     #.Lfinish

.Lsafe:
        mov     dirb, #0
        mov     outb, #0
        mov     r10, #0
        jmp     #.Lfinish

.Lread_word:
        and     r13, ##0x007fffff
        or      r13, ##0xeb000000
        mov     r12, #8
        mov     dirb, r6
        outl    #P2_PSRAM_CLOCK_PIN
        outl    #P2_PSRAM_CE_PIN
        getct   r5
.Lread_command:
        getnib  r11, r13, #7
        shl     r13, #4
        mul     r11, ##0x1111
        p2_psram_clock_out r11
        djnz    r12, #.Lread_command

        mov     dirb, r7                  /* bus turnaround */
        /* Five standalone wait clocks are followed by two clock-and-sample
         * operations.  The first clock-and-sample is the sixth wait clock,
         * whose falling edge returns the first data high nibble.  A one-word
         * read therefore uses 8 + 5 + 2 = 15 clocks total.
         */

        mov     r12, #5
.Lread_dummy:
        p2_psram_clock_dummy
        djnz    r12, #.Lread_dummy

        p2_psram_clock_in r9
        p2_psram_clock_in r8
        p2_psram_ce_end
        shl     r8, #16
        or      r9, r8
        mov     r0, r15
        add     r0, #P2_PSRAM_WIRE_RX_LANES_OFFSET
        wrlong  r9, r0
        mov     r10, #0
        jmp     #.Lfinish

.Lwrite_word:
        and     r13, ##0x007fffff
        or      r13, ##0x02000000
        mov     r12, #8
        mov     dirb, r6
        outl    #P2_PSRAM_CLOCK_PIN
        outl    #P2_PSRAM_CE_PIN
        getct   r5
.Lwrite_command:
        getnib  r11, r13, #7
        shl     r13, #4
        mul     r11, ##0x1111
        p2_psram_clock_out r11
        djnz    r12, #.Lwrite_command

        mov     r0, r15
        add     r0, #P2_PSRAM_WIRE_TX_LANES_OFFSET
        rdlong  r9, r0
        mov     r8, r9
        zerox   r9, #15
        shr     r8, #16
        p2_psram_clock_out r9
        p2_psram_clock_out r8
        p2_psram_ce_end
        mov     r10, #0
        jmp     #.Lfinish

.Lrecover:
        /* Disable all Smart Pin modes before taking electrical control.
         * Enter a known state from either QPI or SPI: QPI F5 exit, serial
         * 66/99 reset, then serial 35 QPI entry.  CE is high and CLK low
         * between every command.
         */

        mov     r0, #P2_PSRAM_DATA_FIRST_PIN
        mov     r1, #(P2_PSRAM_CE_PIN-P2_PSRAM_DATA_FIRST_PIN+1)
.Ldisable_smartpins:
        wrpin   #0, r0
        add     r0, #1
        djnz    r1, #.Ldisable_smartpins

        mov     outb, #0
        outh    #P2_PSRAM_CE_PIN
        outl    #P2_PSRAM_CLOCK_PIN
        mov     dirb, r7
        waitx   #200

        mov     r13, ##0xf5000000         /* QPI exit command */
        mov     r12, #2
        mov     dirb, r6
        outl    #P2_PSRAM_CE_PIN
        getct   r5
.Lqpi_exit:
        getnib  r11, r13, #7
        shl     r13, #4
        mul     r11, ##0x1111
        p2_psram_clock_out r11
        djnz    r12, #.Lqpi_exit
        p2_psram_ce_end
        waitx   #200

        mov     r13, #0x66
        calla   #\.Lsend_spi_command
        mov     r13, #0x99
        calla   #\.Lsend_spi_command
        waitx   #200                      /* tRST is at least 50 ns */
        mov     r13, #0x35
        calla   #\.Lsend_spi_command
        mov     r10, #0
        jmp     #.Lfinish

        /* Serial command helper.  SIO0 is P40/P44/P48/P52, one pin per chip;
         * all other data pins remain inputs while the chips are in SPI mode.
         */

.Lsend_spi_command:
        mov     r2, ##0x00111100          /* four SIO0 pins in OUTB */
        or      r2, r7
        mov     dirb, r2
        outl    #P2_PSRAM_CLOCK_PIN
        outl    #P2_PSRAM_CE_PIN
        getct   r5
        mov     r12, #8
.Lsend_spi_bit:
        testb   r13, #7                   wc
        mov     r11, #0
  if_c  mov     r11, ##0x00111100
        mov     outb, r11
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        outh    #P2_PSRAM_CLOCK_PIN
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        outl    #P2_PSRAM_CLOCK_PIN
        shl     r13, #1
        djnz    r12, #.Lsend_spi_bit
        p2_psram_ce_end
        waitx   #200
        reta

.Lfinish:
        mov     r0, r15
        add     r0, #P2_PSRAM_WIRE_STATUS_OFFSET
        wrlong  r10, r0
        mov     r0, r15
        add     r0, #P2_PSRAM_WIRE_CE_CYCLES_OFFSET
        wrlong  r3, r0

        setq    #15
        rdlong  r0, --ptra
        reta
        .size p2_psram_timing_leaf, .-p2_psram_timing_leaf

        .section .note.GNU-stack, "", @progbits
