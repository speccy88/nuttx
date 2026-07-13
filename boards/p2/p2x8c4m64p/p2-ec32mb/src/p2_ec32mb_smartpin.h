/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_smartpin.h
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

#ifndef __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_SMARTPIN_H
#define __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_SMARTPIN_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <stdbool.h>
#include <stdint.h>

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

/* P2 Smart Pin mode and electrical fields.  The mode occupies bits 1..5;
 * P2_SP_OE enables the Smart Pin result as an output for digital modes.
 */

#define P2_SP_OE                    0x00000040u
#define P2_SP_TT_01                 0x00000040u

#define P2_SP_PWM_SAWTOOTH          0x00000012u
#define P2_SP_COUNT_RISES           0x0000001cu
#define P2_SP_HIGH_TICKS            0x00000022u
#define P2_SP_PERIODS_TICKS         0x00000026u
#define P2_SP_PERIODS_HIGHS         0x00000028u
#define P2_SP_ADC                    0x00000030u
#define P2_SP_ASYNC_TX              0x0000003cu
#define P2_SP_ASYNC_RX              0x0000003eu

#define P2_SP_DAC_DITHER_PWM         0x00000006u
#define P2_SP_ADC_1X                 0x00118000u
#define P2_SP_DAC_990R_3V            0x00140000u

/****************************************************************************
 * Inline Functions
 ****************************************************************************/

static inline void p2_sp_dir_low(unsigned int pin)
{
  __asm__ __volatile__("dirl %0" : : "r" (pin));
}

static inline void p2_sp_dir_high(unsigned int pin)
{
  __asm__ __volatile__("dirh %0" : : "r" (pin));
}

static inline void p2_sp_out_low(unsigned int pin)
{
  __asm__ __volatile__("outl %0" : : "r" (pin));
}

static inline void p2_sp_out_high(unsigned int pin)
{
  __asm__ __volatile__("outh %0" : : "r" (pin));
}

static inline void p2_sp_wrpin(unsigned int pin, uint32_t value)
{
  __asm__ __volatile__("wrpin %0, %1" : : "r" (value), "r" (pin));
}

static inline void p2_sp_wxpin(unsigned int pin, uint32_t value)
{
  __asm__ __volatile__("wxpin %0, %1" : : "r" (value), "r" (pin));
}

static inline void p2_sp_wypin(unsigned int pin, uint32_t value)
{
  __asm__ __volatile__("wypin %0, %1" : : "r" (value), "r" (pin));
}

static inline uint32_t p2_sp_rdpin(unsigned int pin)
{
  uint32_t value;

  __asm__ __volatile__("rdpin %0, %1" : "=r" (value) : "r" (pin));
  return value;
}

static inline bool p2_sp_busy(unsigned int pin)
{
  uint32_t value;
  unsigned int busy;

  __asm__ __volatile__("rdpin %0, %2 wc\n\twrc %1"
                       : "=r" (value), "=r" (busy)
                       : "r" (pin));
  (void)value;
  return busy != 0;
}

static inline void p2_sp_ack(unsigned int pin)
{
  __asm__ __volatile__("akpin %0" : : "r" (pin));
}

static inline bool p2_sp_ready(unsigned int pin)
{
  unsigned int ready;

  __asm__ __volatile__("testp %1 wc\n\twrc %0"
                       : "=r" (ready)
                       : "r" (pin));
  return ready != 0;
}

static inline uint32_t p2_sp_counter(void)
{
  uint32_t value;

  __asm__ __volatile__("getct %0" : "=r" (value));
  return value;
}

static inline void p2_sp_disable(unsigned int pin)
{
  p2_sp_dir_low(pin);
  p2_sp_wrpin(pin, 0);
  p2_sp_out_low(pin);
}

#endif /* __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_SMARTPIN_H */
