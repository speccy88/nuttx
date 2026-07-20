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
        calla   #\__p2_xmem_psram_service_worker
        cogid   r0
        cogstop r0
        .size p2_psram_cog_entry, .-p2_psram_cog_entry

        /* Load the nibble replication table into LUT $000..$00f and the
         * exact-board aligned bulk engine into reserved cog RAM $040 onward.
         * Keeping the time-critical engine in cog RAM also makes its fixed
         * instruction addresses explicit.  SETQ2 selects LUT for the table;
         * ordinary SETQ selects cog RAM for the executable image.
         */

        .globl p2_psram_stream_install
        .type p2_psram_stream_install,@function
p2_psram_stream_install:
        setq    #1
        wrlong  r0, ptra++
        augs    #0
        mov     r1, ##p2_psram_stream_lut_table
        setq2   #(P2_PSRAM_STREAM_LUT_TABLE_LONGS-1)
        rdlong  $0, r1
        augs    #0
        mov     r1, ##p2_psram_stream_cog_image
        setq    #(P2_PSRAM_STREAM_COG_IMAGE_LONGS-1)
        rdlong  $64, r1
        setq    #1
        rdlong  r0, --ptra
        reta
        .size p2_psram_stream_install, .-p2_psram_stream_install

        /* C-callable wrapper for the LUT engine.  p2llvm treats r0..r29 as
         * callee-saved and r31 as its scalar return register.  Preserve the
         * scratch set in the guarded service stack, then cross from HUBEXEC
         * to the fixed cog-RAM entry.  Arguments are operation, logical byte
         * address, Hub buffer, and byte count in r0..r3.
         */

        .globl p2_psram_stream_transfer
        .type p2_psram_stream_transfer,@function
p2_psram_stream_transfer:
        setq    #15
        wrlong  r0, ptra++
        calla   #\P2_PSRAM_STREAM_COG_ENTRY
        setq    #15
        rdlong  r0, --ptra
        reta
        .size p2_psram_stream_transfer, .-p2_psram_stream_transfer

        .p2align 2
p2_psram_stream_lut_table:
        .long   0x00000000, 0x00001111, 0x00002222, 0x00003333
        .long   0x00004444, 0x00005555, 0x00006666, 0x00007777
        .long   0x00008888, 0x00009999, 0x0000aaaa, 0x0000bbbb
        .long   0x0000cccc, 0x0000dddd, 0x0000eeee, 0x0000ffff

        .if ((. - p2_psram_stream_lut_table) != \
             (P2_PSRAM_STREAM_LUT_TABLE_LONGS * 4))
        .error "P2 PSRAM streamer LUT table must contain exactly 16 longs"
        .endif

        .p2align 2
p2_psram_stream_cog_image:
        /* This image is assembled in Hub address space and copied to cog
         * RAM.  Absolute branch expressions translate image byte offsets to
         * cog long addresses so control flow remains correct after relocation.
         * The protocol follows Chip Gracey's P2-EC32MB streamer design, but
         * bounds CE at every 32-long interleaved Wrap32 fragment.
         */

.Lstream_cog_entry:
        mov     r6, r0                  /* preserve operation */
        cmp     r6, #P2_PSRAM_STREAM_READ wz
  if_z  jmp     #\(P2_PSRAM_STREAM_COG_ENTRY + \
                    (.Lstream_operation_valid - .Lstream_cog_entry) / 4)
        cmp     r6, #P2_PSRAM_STREAM_WRITE wz
  if_nz jmp     #\(P2_PSRAM_STREAM_COG_ENTRY + \
                    (.Lstream_invalid - .Lstream_cog_entry) / 4)
.Lstream_operation_valid:
        mov     r0, r1
        or      r0, r2
        or      r0, r3
        test    r0, #3                  wz
  if_nz jmp     #\(P2_PSRAM_STREAM_COG_ENTRY + \
                    (.Lstream_invalid - .Lstream_cog_entry) / 4)
        cmp     r3, #P2_PSRAM_STREAM_MIN_BYTES wc
  if_c  jmp     #\(P2_PSRAM_STREAM_COG_ENTRY + \
                    (.Lstream_invalid - .Lstream_cog_entry) / 4)

        shr     r1, #2                  /* logical byte to chip byte */
        shr     r3, #2                  /* bytes to logical longs */
        mov     r7, ##0x03000000        /* P56 clock plus P57 CE */
        mov     r8, ##0x00ffff00        /* P40..P55 data lanes */
        mov     r12, #0                 /* maximum CE-low cycles */
        augs    #0
        mov     r15, ##g_p2_psram_wire

        outh    #P2_PSRAM_CE_PIN
        outl    #P2_PSRAM_CLOCK_PIN
        andn    outb, r8                 /* streamer output is ORed with OUTB */
        mov     dirb, r7
        dirl    #P2_PSRAM_CLOCK_PIN
        wrpin   #0x4a, #P2_PSRAM_CLOCK_PIN
        wxpin   #1, #P2_PSRAM_CLOCK_PIN
        drvl    #P2_PSRAM_CLOCK_PIN
        or      dirb, r8

.Lstream_fragment:
        mov     r4, #P2_PSRAM_STREAM_FRAGMENT_LONGS
        mov     r5, r1
        and     r5, #(P2_PSRAM_STREAM_FRAGMENT_LONGS-1)
        neg     r5, r5
        add     r5, #P2_PSRAM_STREAM_FRAGMENT_LONGS
        cmp     r5, r4                  wc
  if_c  mov     r4, r5
        cmp     r3, r4                  wc
  if_c  mov     r4, r3

        mov     r0, ##0x80000000
        cmp     r6, #P2_PSRAM_STREAM_READ wz
  if_z  jmp     #\(P2_PSRAM_STREAM_COG_ENTRY + \
                    (.Lstream_read - .Lstream_cog_entry) / 4)

        rdfast  r0, r2
        mov     r9, #0x20               /* nibble-reversed QPI write 02h */
        mov     r10, ##0xb0d00000       /* RFWORD to P40..P55 */
        mov     r11, #8                 /* command/address clocks */
        jmp     #\(P2_PSRAM_STREAM_COG_ENTRY + \
                    (.Lstream_command - .Lstream_cog_entry) / 4)

.Lstream_read:
        wrfast  r0, r2
        mov     r9, #0xbe               /* nibble-reversed QPI read EBh */
        mov     r10, ##0xf0d00000       /* P40..P55 to WFWORD */
        mov     r11, #13                /* command/address plus latency */

.Lstream_command:
        rolnib  r9, r1, #0
        rolnib  r9, r1, #1
        rolnib  r9, r1, #2
        rolnib  r9, r1, #3
        rolnib  r9, r1, #4
        rolnib  r9, r1, #5
        rol     r9, #8

        mov     r14, r4
        shl     r14, #1                 /* 16-bit streamer words */
        setword r10, r14, #0
        add     r11, r14
        shl     r11, #1                 /* Smart Pin transitions */

        cmp     r6, #P2_PSRAM_STREAM_READ wz
  if_z  jmp     #\(P2_PSRAM_STREAM_COG_ENTRY + \
                    (.Lstream_read_launch - .Lstream_cog_entry) / 4)

        /* Queue the write data command on the instruction immediately after
         * WYPIN, matching the reference driver's streamer-buffer deadline.
         * Any augmented setup instruction belongs before XINIT starts the
         * eight-rollover command phase.
         */

        pollxfi                         /* clear any stale completion */
        getct   r13
        drvl    #P2_PSRAM_CE_PIN
        setq    ##0x40000000             /* one streamer step / 2 clocks */
        xinit   ##0x20d00008, r9         /* 8 nibbles through LUT */
        wypin   r11, #P2_PSRAM_CLOCK_PIN
        xcont   r10, #0
        jmp     #\(P2_PSRAM_STREAM_COG_ENTRY + \
                    (.Lstream_wait - .Lstream_cog_entry) / 4)

        /* Preload the augmented delay command before the timed launch.  The
         * post-WYPIN SETQ/XCONT pair is then exactly four system clocks, as
         * in Chip Gracey's driver, instead of racing the final rollover of
         * the 16-clock command phase.
         */

.Lstream_read_launch:
        mov     r5, ##P2_PSRAM_STREAM_READ_DELAY_COMMAND
        mov     r14, ##0x40000000        /* two clocks per streamer step */
        pollxfi                         /* clear any stale completion */
        getct   r13
        drvl    #P2_PSRAM_CE_PIN
        setq    ##0x40000000             /* one streamer step / 2 clocks */
        xinit   ##0x20d00008, r9         /* 8 nibbles through LUT */
        wypin   r11, #P2_PSRAM_CLOCK_PIN
        setq    r0                       /* precise one-clock read delay */
        xcont   r5, #0                   /* sysclk-specific capture offset */
        setq    r14                      /* one instruction: turnaround timing */
        xcont   r10, #0
        andn    dirb, r8                 /* input before first data nibble */
        jmp     #\(P2_PSRAM_STREAM_COG_ENTRY + \
                    (.Lstream_wait - .Lstream_cog_entry) / 4)

.Lstream_wait:
        pollxfi                         wc
  if_nc jmp     #\(P2_PSRAM_STREAM_COG_ENTRY + \
                    (.Lstream_wait - .Lstream_cog_entry) / 4)
        outh    #P2_PSRAM_CE_PIN
        getct   r14
        sub     r14, r13
        cmp     r12, r14                wc
  if_c  mov     r12, r14
        or      dirb, r8

        add     r1, r4
        mov     r14, r4
        shl     r14, #2
        add     r2, r14
        sub     r3, r4                  wz
  if_nz jmp     #\(P2_PSRAM_STREAM_COG_ENTRY + \
                    (.Lstream_fragment - .Lstream_cog_entry) / 4)

        outh    #P2_PSRAM_CE_PIN
        wrpin   #0, #P2_PSRAM_CLOCK_PIN
        outl    #P2_PSRAM_CLOCK_PIN
        mov     dirb, r7
        mov     r0, r15
        add     r0, #P2_PSRAM_WIRE_CE_CYCLES_OFFSET
        wrlong  r12, r0
        mov     r31, #0
        reta

.Lstream_invalid:
        mov     r31, ##-22              /* -EINVAL */
        reta

        .if ((. - p2_psram_stream_cog_image) > \
             (P2_PSRAM_STREAM_COG_IMAGE_LONGS * 4))
        .error "P2 PSRAM streamer cog image exceeds its fixed load size"
        .endif
        .space  (P2_PSRAM_STREAM_COG_IMAGE_LONGS * 4) - \
                (. - p2_psram_stream_cog_image)
p2_psram_stream_cog_image_end:

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

        /* Fixed bounded QPI timing leaf.  A single-word read keeps CE low for
         * 15 clocks (command/address, five standalone wait clocks, two
         * sampled clocks).  The first sampled clock is also the sixth wait
         * clock.  This leaf handles recovery and scalar edges; aligned bulk
         * traffic uses the separately bounded LUT streamer above.  C measures
         * every interval and rejects anything beyond tCEM.
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

.Linvalid:
        mov     r10, ##-22
        jmp     #.Lfinish

.Lrecover:
        /* Disable all Smart Pin modes before taking electrical control.
         * Enter a known state from either QPI or SPI: QPI F5 exit, serial
         * 66/99 reset, serial 35 QPI entry, then exactly one QPI C0 toggle.
         * Reset always restores linear burst, so that final toggle
         * deterministically selects the 32-byte wrapped mode required by
         * the 90-MHz streamer.  CE is high and CLK low between commands.
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
        calla   #\.Lsend_qpi_command

        mov     r13, #0x66
        calla   #\.Lsend_spi_command
        mov     r13, #0x99
        calla   #\.Lsend_spi_command
        waitx   #200                      /* tRST is at least 50 ns */
        mov     r13, #0x35
        calla   #\.Lsend_spi_command
        mov     r13, ##0xc0000000         /* select 32-byte wrapped burst */
        calla   #\.Lsend_qpi_command
        mov     r10, #0
        jmp     #.Lfinish

        /* A QPI command is one byte emitted as two replicated nibbles across
         * all four chips.  Call only with the command in bits 31:24.  C0 has
         * no address or data phase; issuing it exactly once after reset
         * toggles the APS6404L default linear mode to 32-byte wrapped mode.
         */

.Lsend_qpi_command:
        mov     r12, #2
        mov     dirb, r6
        outl    #P2_PSRAM_CLOCK_PIN
        outl    #P2_PSRAM_CE_PIN
        getct   r5
.Lsend_qpi_nibble:
        getnib  r11, r13, #7
        shl     r13, #4
        mul     r11, ##0x1111
        p2_psram_clock_out r11
        djnz    r12, #.Lsend_qpi_nibble
        p2_psram_ce_end
        waitx   #200
        reta

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
