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

        /* Replace this service cog with the self-contained COGEXEC bank
         * loader below.  SETQ passes the address of the two-word boot
         * descriptor in PTRA.  On success COGINIT restarts this cog and does
         * not execute the following instruction; retain a real -EIO return
         * if a specific-cog restart is ever rejected.
         */

        .section .text.p2_psram_bank_cog_start, "ax", @progbits
        .p2align 2
        .globl p2_psram_bank_cog_start
        .type p2_psram_bank_cog_start,@function
p2_psram_bank_cog_start:
        setq    #2
        wrlong  r0, ptra++
        cogid   r0
        augs    #0
        mov     r1, ##p2_psram_bank_loader_image
        augs    #0
        mov     r2, ##g_p2_psram_bank_boot
        setq    r2
        coginit r0, r1                  wc
  if_c  jmp     #.Lbank_start_failed
.Lbank_start_park:
        jmp     #.Lbank_start_park
.Lbank_start_failed:
        mov     r31, ##-5               /* -EIO */
        setq    #2
        rdlong  r0, --ptra
        reta
        .size p2_psram_bank_cog_start, .-p2_psram_bank_cog_start

        .text

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

        /* This image is copied by COGINIT into cog registers starting at
         * register zero.  It must not reference Hub code or data after its
         * first two RDLONGs: the loop deliberately overwrites Hub address
         * zero first.  Data registers live at $180 and above, safely beyond
         * the loader instructions but below the COGINIT load limit.
         */

#define P2_BANK_DESC          $384
#define P2_BANK_SELF          $385
#define P2_BANK_SOURCE        $386
#define P2_BANK_WORDS         $387
#define P2_BANK_HUB           $388
#define P2_BANK_TARGET        $389
#define P2_BANK_COGS          $390
#define P2_BANK_COMMAND       $391
#define P2_BANK_CLOCKS        $392
#define P2_BANK_TEMP          $393
#define P2_BANK_HIGH          $394
#define P2_BANK_LOW           $395
#define P2_BANK_DATA          $396
#define P2_BANK_CONTROLS      $397
#define P2_BANK_ALL_PINS      $398

        .macro p2_bank_clock_out value
        and     \value, ##0xffff
        shl     \value, #8
        mov     outb, \value
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        outh    #P2_PSRAM_CLOCK_PIN
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        outl    #P2_PSRAM_CLOCK_PIN
        .endm

        .macro p2_bank_clock_in result
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        outh    #P2_PSRAM_CLOCK_PIN
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        outl    #P2_PSRAM_CLOCK_PIN
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        mov     \result, inb
        shr     \result, #8
        zerox   \result, #15
        .endm

        .section .text.p2_psram_bank_loader_image, "ax", @progbits
        .p2align 2
        .org 0
        .globl p2_psram_bank_loader_image
        .type p2_psram_bank_loader_image,@function
p2_psram_bank_loader_image:
        mov     P2_BANK_DESC, ptra
        rdlong  P2_BANK_SOURCE, P2_BANK_DESC
        add     P2_BANK_DESC, #4
        rdlong  P2_BANK_WORDS, P2_BANK_DESC
        cogid   P2_BANK_SELF

        /* No scheduler, interrupt, UART, or peripheral cog may remain live
         * while its Hub code is replaced.  Keep only this loader cog.
         */

        mov     P2_BANK_TARGET, #0
        mov     P2_BANK_COGS, #8
.Lbank_stop_cogs:
        cmp     P2_BANK_TARGET, P2_BANK_SELF wz
  if_nz cogstop P2_BANK_TARGET
        add     P2_BANK_TARGET, #1
        djnz    P2_BANK_COGS, #.Lbank_stop_cogs

        /* The source is four-byte aligned.  Four parallel PSRAM chips map
         * each logical long to one common 23-bit chip address.
         */

        shr     P2_BANK_SOURCE, #2
        add     P2_BANK_WORDS, #3
        shr     P2_BANK_WORDS, #2
        mov     P2_BANK_HUB, #0
        mov     P2_BANK_CONTROLS, ##0x03000000
        mov     P2_BANK_ALL_PINS, ##0x03ffff00
        mov     outb, #0
        outh    #P2_PSRAM_CE_PIN
        outl    #P2_PSRAM_CLOCK_PIN
        mov     dirb, P2_BANK_CONTROLS
        waitx   #200

.Lbank_read_word:
        mov     P2_BANK_COMMAND, P2_BANK_SOURCE
        and     P2_BANK_COMMAND, ##0x007fffff
        or      P2_BANK_COMMAND, ##0xeb000000
        mov     P2_BANK_CLOCKS, #8
        mov     dirb, P2_BANK_ALL_PINS
        outl    #P2_PSRAM_CLOCK_PIN
        outl    #P2_PSRAM_CE_PIN
.Lbank_read_command:
        getnib  P2_BANK_TEMP, P2_BANK_COMMAND, #7
        shl     P2_BANK_COMMAND, #4
        mul     P2_BANK_TEMP, ##0x1111
        p2_bank_clock_out P2_BANK_TEMP
        djnz    P2_BANK_CLOCKS, #.Lbank_read_command

        mov     dirb, P2_BANK_CONTROLS
        mov     P2_BANK_CLOCKS, #5
.Lbank_read_dummy:
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        outh    #P2_PSRAM_CLOCK_PIN
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        outl    #P2_PSRAM_CLOCK_PIN
        djnz    P2_BANK_CLOCKS, #.Lbank_read_dummy

        p2_bank_clock_in P2_BANK_HIGH
        p2_bank_clock_in P2_BANK_LOW
        waitx   #P2_PSRAM_HALF_PERIOD_TICKS
        outh    #P2_PSRAM_CE_PIN
        mov     dirb, P2_BANK_CONTROLS

        /* Convert parallel-chip nibble lanes into one little-endian Hub
         * long: high/low nibbles for chip N become byte N.
         */

        mov     P2_BANK_DATA, #0
        getnib  P2_BANK_TEMP, P2_BANK_HIGH, #0
        setnib  P2_BANK_DATA, P2_BANK_TEMP, #1
        getnib  P2_BANK_TEMP, P2_BANK_LOW, #0
        setnib  P2_BANK_DATA, P2_BANK_TEMP, #0
        getnib  P2_BANK_TEMP, P2_BANK_HIGH, #1
        setnib  P2_BANK_DATA, P2_BANK_TEMP, #3
        getnib  P2_BANK_TEMP, P2_BANK_LOW, #1
        setnib  P2_BANK_DATA, P2_BANK_TEMP, #2
        getnib  P2_BANK_TEMP, P2_BANK_HIGH, #2
        setnib  P2_BANK_DATA, P2_BANK_TEMP, #5
        getnib  P2_BANK_TEMP, P2_BANK_LOW, #2
        setnib  P2_BANK_DATA, P2_BANK_TEMP, #4
        getnib  P2_BANK_TEMP, P2_BANK_HIGH, #3
        setnib  P2_BANK_DATA, P2_BANK_TEMP, #7
        getnib  P2_BANK_TEMP, P2_BANK_LOW, #3
        setnib  P2_BANK_DATA, P2_BANK_TEMP, #6

        wrlong  P2_BANK_DATA, P2_BANK_HUB
        add     P2_BANK_SOURCE, #1
        add     P2_BANK_HUB, #4
        djnz    P2_BANK_WORDS, #.Lbank_read_word

        /* Leave P40-P57 electrically safe before starting the replacement
         * image.  The bank's normal board initialization recovers PSRAM.
         */

        waitx   #200
        mov     dirb, #0
        mov     outb, #0
        coginit #0, #0
        cogid   P2_BANK_SELF
        cogstop P2_BANK_SELF
.Lbank_loader_park:
        jmp     #.Lbank_loader_park
p2_psram_bank_loader_image_end:
        .size p2_psram_bank_loader_image, \
              p2_psram_bank_loader_image_end-p2_psram_bank_loader_image

        /* Cog data begins at register $180.  A larger loader would overwrite
         * its own state and is therefore a build-time error.
         */

        .if ((p2_psram_bank_loader_image_end - \
              p2_psram_bank_loader_image) / 4) > 384
        .error "P2 PSRAM bank loader overlaps cog data registers"
        .endif

        .section .note.GNU-stack, "", @progbits
