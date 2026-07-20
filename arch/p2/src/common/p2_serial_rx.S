/****************************************************************************
 * arch/p2/src/common/p2_serial_rx.S
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
#include <arch/board/board.h>

#include "p2_internal.h"

        .text

        /* Start one free cog with COGEXEC_NEW (0x10).  COGINIT copies the
         * 504-long image beginning at p2_uart_rx_cog into cog RAM and starts
         * it at cog address zero.  Running the byte-complete poll loop from
         * cog RAM is required at multi-megabaud rates: a Smart Pin retains
         * only one completed character, so HUBEXEC fetch stalls can silently
         * let the next character replace it.  Preserve p2llvm's callee-saved
         * r0/r1 pair on the caller's stack; the worker itself is leaf assembly
         * and needs neither PTRA nor a C stack.  Return the COGINIT failure
         * flag in the p2llvm scalar return register.
         */

        .globl p2_uart_rx_cog_start
        .type p2_uart_rx_cog_start,@function
p2_uart_rx_cog_start:
        setq    #1
        wrlong  r0, ptra++
        mov     r0, #0x10
        augs    #0
        mov     r1, ##p2_uart_rx_cog
        coginit r0, r1                  wc
        /* In automatic-new-cog mode COGINIT clears C on success and sets C
         * when no free cog exists.  The C caller expects zero on success.
         */

        wrc     r31
        setq    #1
        rdlong  r0, --ptra
        reta
        .size p2_uart_rx_cog_start, .-p2_uart_rx_cog_start

        /* Asynchronous receive Smart Pins retain only one completed character.
         * Poll the hardware-complete event from a dedicated cog and drain P63
         * immediately into a coherent single-producer/single-consumer ring so
         * normal NuttX idle polling can tolerate line bursts.  The hardware
         * UART performs the start/data/stop sampling; the cog must not bit-bang
         * the pin because 2-Mbaud timing leaves too little phase margin for a
         * software sampling loop.
         */

        .globl p2_uart_rx_cog
        .type p2_uart_rx_cog,@function
p2_uart_rx_cog:
        /* This image is assembled in Hub address space and copied to cog
         * RAM.  Branch targets therefore translate byte offsets in this
         * image to absolute cog-long addresses.  p2llvm's r0..r5 aliases are
         * cog registers $1d0..$1d5, safely above this short worker.
         */

        dirl    #BOARD_CONSOLE_RX_PIN
        wrpin   #P2_UART_ASYNC_RX_MODE, #BOARD_CONSOLE_RX_PIN
        mov     r5, ##P2_UART_RX_CONFIG
        wxpin   r5, #BOARD_CONSOLE_RX_PIN
        dirh    #BOARD_CONSOLE_RX_PIN
        /* Discard and acknowledge any result left by pin setup before the
         * producer head becomes live.  This mirrors the TAQOZ receive setup.
         */

        rdpin   r3, #BOARD_CONSOLE_RX_PIN
        mov     r0, #1
        augs    #0
        wrlong  r0, ##g_p2_uart_rx_alive
        augs    #0
        rdlong  r0, ##g_p2_uart_rx_head
        augs    #0
        mov     r4, ##g_p2_uart_rx_ring
.Lrx_wait:
        testp   #BOARD_CONSOLE_RX_PIN    wc
  if_nc jmp     #\((.Lrx_wait - p2_uart_rx_cog) / 4)
        rdpin   r3, #BOARD_CONSOLE_RX_PIN
        shr     r3, #24

        augs    #0
        rdlong  r1, ##g_p2_uart_rx_tail
        mov     r2, r0
        sub     r2, r1
        cmp     r2, ##P2_UART_RX_RING_SIZE wz
  if_z  jmp     #\((.Lrx_drop - p2_uart_rx_cog) / 4)

        mov     r2, r0
        and     r2, ##P2_UART_RX_RING_MASK
        add     r2, r4
        wrbyte  r3, r2
        add     r0, #1
        augs    #0
        wrlong  r0, ##g_p2_uart_rx_head
        jmp     #\((.Lrx_wait - p2_uart_rx_cog) / 4)

.Lrx_drop:
        augs    #0
        rdlong  r2, ##g_p2_uart_rx_dropped
        add     r2, #1
        augs    #0
        wrlong  r2, ##g_p2_uart_rx_dropped
        jmp     #\((.Lrx_wait - p2_uart_rx_cog) / 4)
        .size p2_uart_rx_cog, .-p2_uart_rx_cog
