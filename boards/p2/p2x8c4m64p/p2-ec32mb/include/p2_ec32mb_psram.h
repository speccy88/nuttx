/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/include/p2_ec32mb_psram.h
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

#ifndef __BOARDS_P2_P2X8C4M64P_P2_EC32MB_INCLUDE_P2_EC32MB_PSRAM_H
#define __BOARDS_P2_P2X8C4M64P_P2_EC32MB_INCLUDE_P2_EC32MB_PSRAM_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <sys/types.h>

#include <stddef.h>
#include <stdint.h>

#include <nuttx/compiler.h>

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_PSRAM_DEVICE_PATH          "/dev/psram0"
#define P2_PSRAM_SIZE_BYTES           UINT32_C(33554432)
#define P2_PSRAM_CHIP_COUNT           4u
#define P2_PSRAM_CHIP_SIZE_BYTES      UINT32_C(8388608)
#define P2_PSRAM_NATURAL_WORD_BYTES   4u

/* Unified memory uses a deliberately unmapped tag window.  These are pointer
 * values consumed by the p2llvm lowering pass, not byte-addressable P2 Hub
 * addresses.
 */

#define P2_PSRAM_UNIFIED_BASE         UINT32_C(0x10000000)
#define P2_PSRAM_UNIFIED_SIZE         P2_PSRAM_SIZE_BYTES
#define P2_PSRAM_UNIFIED_END          \
  (P2_PSRAM_UNIFIED_BASE + P2_PSRAM_UNIFIED_SIZE)

/****************************************************************************
 * Public Types
 ****************************************************************************/

enum p2_psram_operation_e
{
  P2_PSRAM_OPERATION_READ = 1,
  P2_PSRAM_OPERATION_WRITE,
  P2_PSRAM_OPERATION_STOP
};

enum p2_psram_completion_e
{
  P2_PSRAM_COMPLETION_IDLE = 0,
  P2_PSRAM_COMPLETION_SUBMITTED,
  P2_PSRAM_COMPLETION_ACTIVE,
  P2_PSRAM_COMPLETION_DONE
};

/* This descriptor resides in coherent Hub RAM.  The NuttX CPU cog is its
 * only producer and the PSRAM service cog is its only consumer.  Both sides
 * hold the service hardware lock while publishing or consuming fields.  The
 * producer clears completion_sequence before submission and the service cog
 * writes the completed sequence last.
 */

struct p2_psram_request_s
{
  volatile uint32_t sequence;
  volatile uint32_t operation;
  uint32_t external_address;
  uintptr_t hub_buffer;
  uint32_t length;
  volatile int32_t status;
  uint32_t timeout_ticks;
  volatile uint32_t completion;
  volatile uint32_t completion_sequence;
};

struct p2_psram_geometry_s
{
  uint32_t size_bytes;
  uint32_t chip_count;
  uint32_t chip_size_bytes;
  uint32_t natural_word_bytes;
  uint32_t max_request_bytes;
  uint32_t qpi_clock_hz;
  uint32_t ce_low_limit_cycles;
  uint32_t max_ce_low_cycles;
  uint32_t service_cog;
};

/****************************************************************************
 * Public Function Prototypes
 ****************************************************************************/

int p2_psram_initialize(void);
int p2_psram_get_geometry(FAR struct p2_psram_geometry_s *geometry);
ssize_t p2_psram_transfer(enum p2_psram_operation_e operation,
                          uint32_t external_address, FAR void *hub_buffer,
                          size_t length, uint32_t timeout_ticks);

#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED
int p2_psram_unified_transfer(enum p2_psram_operation_e operation,
                              uint32_t external_address,
                              FAR void *hub_buffer, uint32_t length);

#  ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED_FAULT_INJECT_RAW_LOCK
int p2_psram_unified_arm_raw_lock_stall(void);
#  endif

uint8_t __p2_xmem_load8(FAR const void *address);
uint16_t __p2_xmem_load16(FAR const void *address);
uint32_t __p2_xmem_load32(FAR const void *address);
uint64_t __p2_xmem_load64(FAR const void *address);

void __p2_xmem_store8(FAR void *address, uint8_t value);
void __p2_xmem_store16(FAR void *address, uint16_t value);
void __p2_xmem_store32(FAR void *address, uint32_t value);
void __p2_xmem_store64(FAR void *address, uint64_t value);

void __p2_xmem_memcpy(FAR void *destination, FAR const void *source,
                      uint32_t length);
void __p2_xmem_memmove(FAR void *destination, FAR const void *source,
                       uint32_t length);
void __p2_xmem_memset(FAR void *destination, uint8_t value,
                      uint32_t length);

#  ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED_SELFTEST
int p2_psram_unified_selftest(void);
#    ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED_SELFTEST_FULL
int p2_psram_unified_fulltest(void);
#    endif
#  endif
#endif

#endif /* __BOARDS_P2_P2X8C4M64P_P2_EC32MB_INCLUDE_P2_EC32MB_PSRAM_H */
