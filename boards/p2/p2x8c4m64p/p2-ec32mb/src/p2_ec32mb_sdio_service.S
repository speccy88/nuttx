/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_sdio_service.S
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

#include "p2_ec32mb_sdio_wire.h"

/* P2LLVM's assembler accepts arbitrary cog-register addresses as numeric
 * register operands, but it does not resolve .set aliases while parsing an
 * instruction.  Its $NNN register-token syntax is decimal (unlike PASM's
 * hexadecimal $ literals).  Preprocessor aliases keep the image readable
 * and leave the final operands numeric.
 */

#define mb           $224
#define lastseq      $225
#define sequence     $226
#define operation    $227
#define addr         $228
#define value        $229
#define buffer       $230
#define blocklen     $231
#define blocks       $232
#define clockx       $233
#define xfrq         $234
#define scanhalf     $235
#define startlimit   $236
#define nibbles      $237
#define divisor      $238
#define transferred  $239
#define scans        $240
#define streamcmd    $241
#define waitcount    $242
#define trailers     $243
#define status       $244
#define inputmode    $245
#define rxlag        $246
#define crcptr       $247
#define crc0         $248
#define crc1         $249
#define crc2         $250
#define crc3         $251
#define aligncmd     $252

#define P2_SDIO_ALIGN_BASE 8

        .text

        /* Start a free cog by loading the image below into cog RAM.  The
         * streamer cannot write through WRFAST while executing from HUB RAM,
         * because both facilities use the same FIFO.  SETQ passes the HUB
         * mailbox address in the new cog's PTRA.
         */

        .globl p2_sdio_service_start
        .type p2_sdio_service_start,@function
p2_sdio_service_start:
        setq    #2
        wrlong  r0, ptra++
        mov     r0, #0x10               /* load a free cog, then run at $000 */
        augs    #0
        mov     r1, ##p2_sdio_service_image
        augs    #0
        mov     r2, ##g_p2_sdio_wire
        setq    r2
        coginit r0, r1                  wc
        mov     r31, r0
        setq    #2
        rdlong  r0, --ptra
        reta
        .size p2_sdio_service_start, .-p2_sdio_service_start

        /* The image below executes from cog register RAM.  Code occupies the
         * low registers; scratch state starts at cog register 224, safely
         * above the image.  Every branch is local/PC-relative and PTRA
         * remains the HUB mailbox pointer supplied by COGINIT.
         *
         * A CMD18 request remains one mailbox operation and one WRFAST stream.
         * The cog consumes each native-SD block frame itself: bounded start
         * scan, payload capture, four received CRC16 values, and a checked
         * end bit.  The C side may compare those CRCs after the timed stream;
         * record mode deliberately reports when that comparison is disabled.
         */

        .p2align 2
        .globl p2_sdio_service_image
        .type p2_sdio_service_image,@object
p2_sdio_service_image:
        mov     mb, ptra
        mov     lastseq, #0
        mov     value, #1
        mov     addr, mb
        add     addr, #P2_SDIO_WIRE_READY_OFFSET
        wrlong  value, addr

.Lidle:
        rdlong  sequence, mb
        cmp     sequence, lastseq        wz
  if_nz jmp     #.Lrequest
        waitx   #100
        jmp     #.Lidle

.Lrequest:
        mov     lastseq, sequence
        mov     addr, mb
        add     addr, #P2_SDIO_WIRE_OPERATION_OFFSET
        rdlong  operation, addr
        cmp     operation, #P2_SDIO_WIRE_OP_READ_BLOCKS wz
  if_nz jmp     #.Linvalid

        add     addr, #4
        rdlong  buffer, addr
        add     addr, #4
        rdlong  blocklen, addr
        add     addr, #4
        rdlong  blocks, addr
        add     addr, #4
        rdlong  clockx, addr
        add     addr, #4
        rdlong  xfrq, addr
        add     addr, #4
        rdlong  scanhalf, addr
        add     addr, #4
        rdlong  startlimit, addr
        mov     addr, mb
        add     addr, #P2_SDIO_WIRE_INPUT_MODE_OFFSET
        rdlong  inputmode, addr
        add     addr, #4
        rdlong  rxlag, addr
        add     addr, #4
        rdlong  crcptr, addr
        add     addr, #4
        rdlong  operation, addr

        cmp     buffer, #0               wz
  if_z  jmp     #.Linvalid
        cmp     blocklen, #0             wz
  if_z  jmp     #.Linvalid
        cmp     blocks, #0               wz
  if_z  jmp     #.Linvalid
        cmp     xfrq, #0                 wz
  if_z  jmp     #.Linvalid
        cmp     scanhalf, #0             wz
  if_z  jmp     #.Linvalid
        cmp     startlimit, #0           wz
  if_z  jmp     #.Linvalid
        cmp     crcptr, #0               wz
  if_z  jmp     #.Linvalid

        mov     nibbles, blocklen
        shl     nibbles, #1
        mov     value, nibbles
        and     value, ##0xffff0000      wz
  if_nz jmp     #.Linvalid
        mov     divisor, clockx
        zerox   divisor, #15
        cmp     divisor, #2              wc
  if_c  jmp     #.Linvalid

        mov     transferred, #0
        wrpin   inputmode, #16
        wrpin   inputmode, #17
        wrpin   inputmode, #18
        wrpin   inputmode, #19

.Lblock:
        /* Find the start bit while sampling DAT0 on a rising edge.  Lowering
         * CLK advances the card to its first data nibble, which is then
         * stable before XINIT takes its initial sample.
         */

        dirl    #21
        wrpin   #0, #21
        outl    #21
        mov     scans, startlimit
.Lscan:
        drvh    #21
        waitx   scanhalf
        testp   #16                     wc
        drvl    #21
        waitx   scanhalf
  if_nc jmp     #.Lstart
        djnz    scans, #.Lscan
        mov     status, ##P2_SDIO_WIRE_STATUS_ETIMEDOUT
        jmp     #.Lerror

.Lstart:
        /* PULSE produces the requested divisor clock (odd divisors have an
         * asymmetric duty cycle).  The streamer reads P16..P19, records
         * samples through WRFAST, and reverses the two 4-bit values within
         * each output byte so SD's first nibble is the high nibble.  Divider
         * 3 and faster are intentionally HIL-required phase points; divider
         * 4 is the conservative activation sequence.
         */

        dirl    #21
        wrpin   #0x48, #21               /* P_PULSE | P_OE */
        wxpin   clockx, #21
        wypin   #0, #21
        wrfast  #0, buffer
        pollxfi wc                       /* discard a stale completion event */
        mov     streamcmd, ##0xe0a50000  /* 4-pin P16, WFBYTE, MS nibble first */
        or      streamcmd, nibbles

        /* Lead with a harmless sysclk-rate streamer command.  The buffered
         * SETQ+XZERO command atomically installs the data NCO and resets its
         * phase at the alignment boundary.  Starting the pulse Smart Pin
         * after the data command is buffered prevents any payload samples
         * from occurring before CLK starts.  Calibration varies the lead-in
         * count by one sysclk at a time.
         */

        mov     aligncmd, ##(0x40000000 | P2_SDIO_ALIGN_BASE)
        add     aligncmd, divisor
        add     aligncmd, rxlag
        setxfrq ##0x80000000
        xinit   aligncmd, #0
        setq    xfrq

        /* Pinned P2LLVM does not yet expose the XZERO mnemonic.  This is
         * XZERO streamcmd,#0 (EEEE=always, opcode=1100101, C=1, L=0,
         * I=1, D=$0f1, S=0), encoded from the public P2 instruction format.
         */

        .long   0xfcb5e200
        dirh    #21
        mov     value, nibbles
        cmp     operation, #0           wz
  if_z  add     value, #17
        wypin   value, #21

        mov     waitcount, nibbles
        mul     waitcount, divisor
        shl     waitcount, #2
        add     waitcount, ##1024
.Lwait_stream:
        pollxfi wc
  if_c  jmp     #.Lstream_done
        djnz    waitcount, #.Lwait_stream
        mov     status, ##P2_SDIO_WIRE_STATUS_ETIMEDOUT
        jmp     #.Lerror

.Lstream_done:
        mov     waitcount, ##1024
.Lwait_clock:
        testp   #21                     wc
  if_c  jmp     #.Lclock_done
        djnz    waitcount, #.Lwait_clock
        mov     status, ##P2_SDIO_WIRE_STATUS_ETIMEDOUT
        jmp     #.Lerror

.Lclock_done:
        dirl    #21
        wrpin   #0, #21
        outl    #21
        rdfast  #0, #0                  /* commit this block's FIFO writes */
        add     transferred, blocklen
        add     buffer, blocklen

        cmp     operation, #0           wz
  if_z  jmp     #.Ltrailer_done

        /* Capture the four parallel CRC16 fields into the HUB sidecar.  The
         * following end bit must be high on every DAT lane or the request
         * fails explicitly.
         */

        mov     crc0, #0
        mov     crc1, #0
        mov     crc2, #0
        mov     crc3, #0
        mov     trailers, #16
.Lcrc_clock:
        drvh    #21
        waitx   scanhalf
        mov     value, ina
        shr     value, #16
        and     value, #0x0f
        test    value, #1               wc
        rcl     crc0, #1
        test    value, #2               wc
        rcl     crc1, #1
        test    value, #4               wc
        rcl     crc2, #1
        test    value, #8               wc
        rcl     crc3, #1
        drvl    #21
        waitx   scanhalf
        djnz    trailers, #.Lcrc_clock

        wrword  crc0, crcptr
        add     crcptr, #2
        wrword  crc1, crcptr
        add     crcptr, #2
        wrword  crc2, crcptr
        add     crcptr, #2
        wrword  crc3, crcptr
        add     crcptr, #2

        drvh    #21
        waitx   scanhalf
        mov     value, ina
        shr     value, #16
        and     value, #0x0f
        drvl    #21
        waitx   scanhalf
        cmp     value, #0x0f            wz
  if_nz jmp     #.Lioerror

.Ltrailer_done:
        djnz    blocks, #.Lblock
        mov     status, #P2_SDIO_WIRE_STATUS_OK
        jmp     #.Lpublish

.Lioerror:
        mov     status, ##P2_SDIO_WIRE_STATUS_EIO
        jmp     #.Lerror

.Linvalid:
        mov     transferred, #0
        mov     status, ##P2_SDIO_WIRE_STATUS_EINVAL
        jmp     #.Lpublish

.Lerror:
        dirl    #21
        wrpin   #0, #21
        outl    #21
        rdfast  #0, #0

.Lpublish:
        /* Publish completion only after this COG has released CLK.  The
         * scheduler COG waits on COMPLETE before reclaiming P21 as an
         * ordinary low output, so no two COGs own the Smart Pin at once.
         */

        dirl    #21
        wrpin   #0, #21
        outl    #21
        mov     addr, mb
        add     addr, #P2_SDIO_WIRE_STATUS_OFFSET
        wrlong  status, addr
        add     addr, #4
        wrlong  transferred, addr
        mov     addr, mb
        add     addr, #P2_SDIO_WIRE_COMPLETE_OFFSET
        wrlong  sequence, addr
        jmp     #.Lidle

        .if (. - p2_sdio_service_image) > (224 * 4)
        .error "native SDIO service overlaps cog scratch registers"
        .endif

        .size p2_sdio_service_image, .-p2_sdio_service_image
