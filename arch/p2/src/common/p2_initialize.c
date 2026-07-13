/****************************************************************************
 * arch/p2/src/common/p2_initialize.c
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

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <nuttx/board.h>

#include "p2_internal.h"

/****************************************************************************
 * Public Functions
 ****************************************************************************/

void up_initialize(void)
{
  p2_boot_trace("P2K:UP:ENTER");
  p2_serialinit();

#ifdef CONFIG_ARCH_LEDS
  board_autoled_initialize();
#endif

  p2_boot_trace("P2K:UP:OK");
}

void up_idle(void)
{
  p2_serialpoll();
  __asm__ __volatile__("nop");
}
