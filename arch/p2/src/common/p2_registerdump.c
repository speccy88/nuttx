/****************************************************************************
 * arch/p2/src/common/p2_registerdump.c
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

#include <stdint.h>

#include <nuttx/debug.h>
#include <nuttx/irq.h>

#include <arch/context.h>
#include <arch/irq.h>

/****************************************************************************
 * Public Functions
 ****************************************************************************/

void up_dump_register(void *dumpregs)
{
  xcpt_reg_t *regs = dumpregs != NULL ? dumpregs : up_current_regs();

  if (regs == NULL)
    {
      _alert("No P2 saved register context\n");
      return;
    }

  _alert("R0 : %08x %08x %08x %08x %08x %08x %08x %08x\n",
         regs[0], regs[1], regs[2], regs[3],
         regs[4], regs[5], regs[6], regs[7]);
  _alert("R8 : %08x %08x %08x %08x %08x %08x %08x %08x\n",
         regs[8], regs[9], regs[10], regs[11],
         regs[12], regs[13], regs[14], regs[15]);
  _alert("R16: %08x %08x %08x %08x %08x %08x %08x %08x\n",
         regs[16], regs[17], regs[18], regs[19],
         regs[20], regs[21], regs[22], regs[23]);
  _alert("R24: %08x %08x %08x %08x %08x %08x %08x %08x\n",
         regs[24], regs[25], regs[26], regs[27],
         regs[28], regs[29], regs[30], regs[31]);
  _alert("PC : %08x PA:%08x PB:%08x PTRA:%08x PTRB:%08x IRQ:%08x\n",
         regs[P2_REG_RESUME] & P2_RESUME_PC_MASK,
         regs[P2_REG_PA], regs[P2_REG_PB],
         regs[P2_REG_PTRA] + P2_RESUME_STACK_OFFSET,
         regs[P2_REG_PTRB], regs[P2_REG_IRQSTATE]);
}
