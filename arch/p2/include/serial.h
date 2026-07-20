/****************************************************************************
 * arch/p2/include/serial.h
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

#ifndef __ARCH_P2_INCLUDE_SERIAL_H
#define __ARCH_P2_INCLUDE_SERIAL_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <stddef.h>
#include <sys/types.h>

#include <nuttx/compiler.h>

/****************************************************************************
 * Public Function Prototypes
 ****************************************************************************/

/* Temporarily transfer the console lower-RX-ring consumer from the serial
 * upper half to one scheduler-cog caller.  Begin succeeds only when the
 * dedicated Smart Pin drain cog is live, normal receive is enabled, the
 * lower ring is empty, and no other exclusive consumer is active.  While the
 * claim is held, the timer and idle services do not promote bytes into the
 * serial upper half.
 *
 * This is a bounded boot/runtime-loading primitive, not a second tty API.
 * The caller must restore normal service with p2_uart_rxraw_end() on every
 * success and failure path before ordinary console input is expected.
 */

int p2_uart_rxraw_begin(void);

/* BUFFER must describe native Hub RAM.  Tagged external-memory pointers and
 * ranges crossing the physical Hub boundary are rejected before any byte or
 * consumer index changes.
 */

ssize_t p2_uart_rxraw_read(FAR void *buffer, size_t size);
void p2_uart_rxraw_end(void);

#endif /* __ARCH_P2_INCLUDE_SERIAL_H */
