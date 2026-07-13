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

        /* Start one free cog with HUBEXEC_NEW (0x30).  The worker is
         * leaf assembly and deliberately needs neither PTRA nor a C stack.
         * Return the COGINIT failure flag in the p2llvm scalar return register.
         */

        .globl p2_uart_rx_cog_start
        .type p2_uart_rx_cog_start,@function
p2_uart_rx_cog_start:
        mov     r0, #0x30
        augs    #0
        mov     r1, ##p2_uart_rx_cog
        coginit r0, r1                  wc
        wrc     r31
        reta
        .size p2_uart_rx_cog_start, .-p2_uart_rx_cog_start

        /* Asynchronous receive Smart Pins retain only one completed character.
         * Drain P63 continuously into a coherent single-producer/single-
         * consumer ring so normal NuttX idle polling can tolerate line bursts.
         */

        .globl p2_uart_rx_cog
        .type p2_uart_rx_cog,@function
p2_uart_rx_cog:
        /* Cog 0 releases P63 before launch.  Sample raw 8-N-1 frames with a
         * half-bit start validation and a stop-bit check.  The per-bit wait
         * compensates for TESTP, RCR, and DJNZ execution between samples.
         */

        dirl    #BOARD_CONSOLE_RX_PIN
        wrpin   #0, #BOARD_CONSOLE_RX_PIN
        mov     r5, ##(P2_UART_RX_BIT_TICKS - 8)
        mov     r0, #1
        augs    #0
        wrlong  r0, ##g_p2_uart_rx_alive
.Lrx_wait:
        testp   #BOARD_CONSOLE_RX_PIN    wc
  if_c  jmp     #\.Lrx_wait

        mov     r7, ##(P2_UART_RX_BIT_TICKS / 2)
        waitx   r7
        testp   #BOARD_CONSOLE_RX_PIN    wc
  if_c  jmp     #\.Lrx_wait

        mov     r3, #0
        mov     r6, #8
.Lrx_bits:
        waitx   r5
        testp   #BOARD_CONSOLE_RX_PIN    wc
        rcr     r3, #1
        djnz    r6, #.Lrx_bits

        waitx   r5
        testp   #BOARD_CONSOLE_RX_PIN    wc
  if_nc jmp     #\.Lrx_wait
        shr     r3, #24

        augs    #0
        rdlong  r0, ##g_p2_uart_rx_head
        augs    #0
        rdlong  r1, ##g_p2_uart_rx_tail
        mov     r2, r0
        sub     r2, r1
        cmp     r2, #P2_UART_RX_RING_SIZE wz
  if_z  jmp     #\.Lrx_drop

        mov     r2, r0
        and     r2, #P2_UART_RX_RING_MASK
        augs    #0
        mov     r4, ##g_p2_uart_rx_ring
        add     r2, r4
        wrbyte  r3, r2
        add     r0, #1
        augs    #0
        wrlong  r0, ##g_p2_uart_rx_head
        jmp     #\.Lrx_wait

.Lrx_drop:
        augs    #0
        rdlong  r2, ##g_p2_uart_rx_dropped
        add     r2, #1
        augs    #0
        wrlong  r2, ##g_p2_uart_rx_dropped
        jmp     #\.Lrx_wait
        .size p2_uart_rx_cog, .-p2_uart_rx_cog
