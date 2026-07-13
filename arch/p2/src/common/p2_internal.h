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
#define P2_UART_RX_BIT_TICKS \
  (BOARD_SYSCLK_FREQUENCY / BOARD_UART0_BAUD)
#define P2_UART_RX_RING_SIZE 256
#define P2_UART_RX_RING_MASK (P2_UART_RX_RING_SIZE - 1)

#ifndef __ASSEMBLY__

/****************************************************************************
 * Public Function Prototypes
 ****************************************************************************/

uintptr_t p2_getsp(void);
void p2_lowsetup(void);
void p2_lowputc(int ch);
void p2_serialinit(void);
void p2_serialpoll(void);
int p2_uart_rx_cog_start(void);
void up_fullcontextrestore(void *restoreregs) noreturn_function;

#ifdef CONFIG_P2_BOOT_TRACE
void p2_boot_trace(const char *message);
#else
#  define p2_boot_trace(message) ((void)0)
#endif
#endif /* __ASSEMBLY__ */

#endif /* __ARCH_P2_SRC_COMMON_P2_INTERNAL_H */
