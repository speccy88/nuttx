/****************************************************************************
 * arch/p2/src/common/p2_internal.h
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

#ifndef __ARCH_P2_SRC_COMMON_P2_INTERNAL_H
#define __ARCH_P2_SRC_COMMON_P2_INTERNAL_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#ifndef __ASSEMBLY__
#  include <stdint.h>

#  include <nuttx/compiler.h>
#endif

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_HUB_RAM_BASE 0x00000000u
#define P2_HUB_RAM_SIZE (512u * 1024u)
#define P2_INITIAL_STACK_SIZE 4096u
#define P2_STACK_COLOR 0x1bad1deau
#define P2_UART_ASYNC_RX_MODE 0x3e
#ifndef P2_UART_RX_BIT_TICKS
#  define P2_UART_RX_BIT_TICKS \
    ((CONFIG_P2_SYSCLK_HZ + CONFIG_UART0_BAUD / 2) / CONFIG_UART0_BAUD)
#endif
#define P2_UART_RX_CONFIG \
  (((P2_UART_RX_BIT_TICKS << 16) & 0xfffffc00) | 7)
#define P2_UART_RX_RING_SIZE CONFIG_P2_UART_RX_RING_SIZE
#define P2_UART_RX_RING_MASK (P2_UART_RX_RING_SIZE - 1)

#if CONFIG_UART0_BAUD <= 0 || P2_UART_RX_BIT_TICKS <= 8
#  error "P2 console baud leaves too few clocks for Smart Pin RX"
#endif

#if P2_UART_RX_RING_SIZE < 2 || \
    (P2_UART_RX_RING_SIZE & P2_UART_RX_RING_MASK) != 0
#  error "CONFIG_P2_UART_RX_RING_SIZE must be a power of two"
#endif

#ifndef __ASSEMBLY__

/****************************************************************************
 * Public Function Prototypes
 ****************************************************************************/

uintptr_t p2_getsp(void);
void p2_lowsetup(void);
void p2_lowputc(int ch) noinline_function;
void p2_serialinit(void);
void p2_serialpoll(void);
int p2_serialinterrupt(int irq, void *context, void *arg);
int p2_uart_rx_cog_start(void);
void up_fullcontextrestore(void *restoreregs) noreturn_function;

#ifdef CONFIG_P2_BOOT_TRACE
/* The assembly name is part of the unified-memory recursion boundary.  The
 * compiler pass skips __p2_xmem_* functions, so this raw fatal-path console
 * helper must retain that linked name even though callers use the stable C
 * API below.
 */

void p2_boot_trace(const char *message)
  __asm__("__p2_xmem_boot_trace");
#else
#  define p2_boot_trace(message) ((void)0)
#endif
#endif /* __ASSEMBLY__ */

#endif /* __ARCH_P2_SRC_COMMON_P2_INTERNAL_H */
