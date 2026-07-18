/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_xmem.c
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

#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <nuttx/compiler.h>
#include <nuttx/kmalloc.h>

#include <arch/board/board.h>
#include <arch/board/p2_ec32mb_psram.h>
#include <arch/chip/chip.h>

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_XMEM_BOUNCE_SIZE 64u

#ifndef CONFIG_P2_EC32MB_PSRAM_UNIFIED
#  error "The P2 xmem runtime must only be built for unified PSRAM"
#endif

#if CONFIG_P2_EC32MB_PSRAM_UNIFIED_RESERVE_SIZE > P2_PSRAM_UNIFIED_SIZE
#  error "The unified PSRAM reserve exceeds physical PSRAM"
#endif

#if (CONFIG_P2_EC32MB_PSRAM_UNIFIED_RESERVE_SIZE & 15) != 0
#  error "The unified PSRAM reserve must be 16-byte aligned"
#endif

/****************************************************************************
 * Private Types
 ****************************************************************************/

enum p2_xmem_region_e
{
  P2_XMEM_REGION_HUB = 0,
  P2_XMEM_REGION_PSRAM
};

/****************************************************************************
 * Private Functions
 ****************************************************************************/

/* The p2llvm unified-memory pass deliberately skips every function whose
 * name begins with __p2_xmem_.  Keep every function reachable from an ABI
 * helper under that prefix: these native Hub accesses are the recursion
 * boundary for the compiler lowering.
 */

static void __p2_xmem_fault(void) noreturn_function;
static void __p2_xmem_fault(void)
{
  PANIC();

  for (; ; )
    {
      __asm__ __volatile__("nop");
    }
}

static enum p2_xmem_region_e
__p2_xmem_classify(FAR const void *address, uint32_t length)
{
  uintptr_t start = (uintptr_t)address;
  uintptr_t hub_end = BOARD_P2_HUB_USABLE_END;

  if (start < hub_end && length <= hub_end - start)
    {
      return P2_XMEM_REGION_HUB;
    }

  if (start >= P2_PSRAM_UNIFIED_BASE &&
      start < P2_PSRAM_UNIFIED_END)
    {
      if (length <= P2_PSRAM_UNIFIED_END - start)
        {
          return P2_XMEM_REGION_PSRAM;
        }
    }

  /* There are exactly two legal data spaces.  Fault gaps, addresses above
   * the tag window, wrapped ranges, and operations crossing either boundary.
   */

  __p2_xmem_fault();
}

static uint32_t __p2_xmem_offset(FAR const void *address)
{
  return (uint32_t)((uintptr_t)address - P2_PSRAM_UNIFIED_BASE);
}

static void __p2_xmem_transfer(enum p2_psram_operation_e operation,
                               FAR const void *external,
                               FAR void *hub, uint32_t length)
{
  int ret;

  ret = p2_psram_unified_transfer(operation, __p2_xmem_offset(external),
                                  hub, length);
  if (ret < 0)
    {
      __p2_xmem_fault();
    }
}

static void __p2_xmem_hub_copy_forward(FAR void *destination,
                                       FAR const void *source,
                                       uint32_t length)
{
  FAR volatile uint8_t *dest = (FAR volatile uint8_t *)destination;
  FAR const volatile uint8_t *src =
    (FAR const volatile uint8_t *)source;

  while (length-- > 0)
    {
      *dest++ = *src++;
    }
}

static void __p2_xmem_hub_copy_backward(FAR void *destination,
                                        FAR const void *source,
                                        uint32_t length)
{
  FAR volatile uint8_t *dest =
    (FAR volatile uint8_t *)destination + length;
  FAR const volatile uint8_t *src =
    (FAR const volatile uint8_t *)source + length;

  while (length-- > 0)
    {
      *--dest = *--src;
    }
}

static void __p2_xmem_copy_chunk(FAR void *destination,
                                 enum p2_xmem_region_e dest_region,
                                 FAR const void *source,
                                 enum p2_xmem_region_e source_region,
                                 uint32_t length)
{
  uint8_t bounce[P2_XMEM_BOUNCE_SIZE];

  if (source_region == P2_XMEM_REGION_PSRAM)
    {
      __p2_xmem_transfer(P2_PSRAM_OPERATION_READ, source, bounce, length);
    }
  else
    {
      __p2_xmem_hub_copy_forward(bounce, source, length);
    }

  if (dest_region == P2_XMEM_REGION_PSRAM)
    {
      __p2_xmem_transfer(P2_PSRAM_OPERATION_WRITE, destination, bounce,
                         length);
    }
  else
    {
      __p2_xmem_hub_copy_forward(destination, bounce, length);
    }
}

static void __p2_xmem_copy_forward(FAR void *destination,
                                   enum p2_xmem_region_e dest_region,
                                   FAR const void *source,
                                   enum p2_xmem_region_e source_region,
                                   uint32_t length)
{
  uintptr_t dest = (uintptr_t)destination;
  uintptr_t src = (uintptr_t)source;

  while (length > 0)
    {
      uint32_t chunk = length > P2_XMEM_BOUNCE_SIZE ?
                       P2_XMEM_BOUNCE_SIZE : length;

      __p2_xmem_copy_chunk((FAR void *)dest, dest_region,
                           (FAR const void *)src, source_region, chunk);
      dest += chunk;
      src += chunk;
      length -= chunk;
    }
}

static void __p2_xmem_copy_backward(FAR void *destination,
                                    enum p2_xmem_region_e dest_region,
                                    FAR const void *source,
                                    enum p2_xmem_region_e source_region,
                                    uint32_t length)
{
  uintptr_t dest = (uintptr_t)destination + length;
  uintptr_t src = (uintptr_t)source + length;

  while (length > 0)
    {
      uint32_t chunk = length > P2_XMEM_BOUNCE_SIZE ?
                       P2_XMEM_BOUNCE_SIZE : length;

      dest -= chunk;
      src -= chunk;
      __p2_xmem_copy_chunk((FAR void *)dest, dest_region,
                           (FAR const void *)src, source_region, chunk);
      length -= chunk;
    }
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

uint8_t __p2_xmem_load8(FAR const void *address)
{
  uint8_t value;

  if (__p2_xmem_classify(address, sizeof(value)) == P2_XMEM_REGION_PSRAM)
    {
      __p2_xmem_transfer(P2_PSRAM_OPERATION_READ, address, &value,
                         sizeof(value));
    }
  else
    {
      value = *(FAR const volatile uint8_t *)address;
    }

  return value;
}

uint16_t __p2_xmem_load16(FAR const void *address)
{
  uint16_t value;

  if (__p2_xmem_classify(address, sizeof(value)) == P2_XMEM_REGION_PSRAM)
    {
      __p2_xmem_transfer(P2_PSRAM_OPERATION_READ, address, &value,
                         sizeof(value));
    }
  else
    {
      if (((uintptr_t)address & (sizeof(value) - 1u)) == 0)
        {
          value = *(FAR const volatile uint16_t *)address;
        }
      else
        {
          __p2_xmem_hub_copy_forward(&value, address, sizeof(value));
        }
    }

  return value;
}

uint32_t __p2_xmem_load32(FAR const void *address)
{
  uint32_t value;

  if (__p2_xmem_classify(address, sizeof(value)) == P2_XMEM_REGION_PSRAM)
    {
      __p2_xmem_transfer(P2_PSRAM_OPERATION_READ, address, &value,
                         sizeof(value));
    }
  else
    {
      if (((uintptr_t)address & (sizeof(value) - 1u)) == 0)
        {
          value = *(FAR const volatile uint32_t *)address;
        }
      else
        {
          __p2_xmem_hub_copy_forward(&value, address, sizeof(value));
        }
    }

  return value;
}

uint64_t __p2_xmem_load64(FAR const void *address)
{
  uint64_t value;

  if (__p2_xmem_classify(address, sizeof(value)) == P2_XMEM_REGION_PSRAM)
    {
      __p2_xmem_transfer(P2_PSRAM_OPERATION_READ, address, &value,
                         sizeof(value));
    }
  else
    {
      if (((uintptr_t)address & (sizeof(value) - 1u)) == 0)
        {
          value = *(FAR const volatile uint64_t *)address;
        }
      else
        {
          __p2_xmem_hub_copy_forward(&value, address, sizeof(value));
        }
    }

  return value;
}

void __p2_xmem_store8(FAR void *address, uint8_t value)
{
  if (__p2_xmem_classify(address, sizeof(value)) == P2_XMEM_REGION_PSRAM)
    {
      __p2_xmem_transfer(P2_PSRAM_OPERATION_WRITE, address, &value,
                         sizeof(value));
    }
  else
    {
      *(FAR volatile uint8_t *)address = value;
    }
}

void __p2_xmem_store16(FAR void *address, uint16_t value)
{
  if (__p2_xmem_classify(address, sizeof(value)) == P2_XMEM_REGION_PSRAM)
    {
      __p2_xmem_transfer(P2_PSRAM_OPERATION_WRITE, address, &value,
                         sizeof(value));
    }
  else
    {
      if (((uintptr_t)address & (sizeof(value) - 1u)) == 0)
        {
          *(FAR volatile uint16_t *)address = value;
        }
      else
        {
          __p2_xmem_hub_copy_forward(address, &value, sizeof(value));
        }
    }
}

void __p2_xmem_store32(FAR void *address, uint32_t value)
{
  if (__p2_xmem_classify(address, sizeof(value)) == P2_XMEM_REGION_PSRAM)
    {
      __p2_xmem_transfer(P2_PSRAM_OPERATION_WRITE, address, &value,
                         sizeof(value));
    }
  else
    {
      if (((uintptr_t)address & (sizeof(value) - 1u)) == 0)
        {
          *(FAR volatile uint32_t *)address = value;
        }
      else
        {
          __p2_xmem_hub_copy_forward(address, &value, sizeof(value));
        }
    }
}

void __p2_xmem_store64(FAR void *address, uint64_t value)
{
  if (__p2_xmem_classify(address, sizeof(value)) == P2_XMEM_REGION_PSRAM)
    {
      __p2_xmem_transfer(P2_PSRAM_OPERATION_WRITE, address, &value,
                         sizeof(value));
    }
  else
    {
      if (((uintptr_t)address & (sizeof(value) - 1u)) == 0)
        {
          *(FAR volatile uint64_t *)address = value;
        }
      else
        {
          __p2_xmem_hub_copy_forward(address, &value, sizeof(value));
        }
    }
}

void __p2_xmem_memcpy(FAR void *destination, FAR const void *source,
                      uint32_t length)
{
  enum p2_xmem_region_e dest_region;
  enum p2_xmem_region_e source_region;

  if (length == 0)
    {
      return;
    }

  dest_region = __p2_xmem_classify(destination, length);
  source_region = __p2_xmem_classify(source, length);
  if (dest_region == P2_XMEM_REGION_HUB &&
      source_region == P2_XMEM_REGION_HUB)
    {
      __p2_xmem_hub_copy_forward(destination, source, length);
    }
  else
    {
      __p2_xmem_copy_forward(destination, dest_region, source, source_region,
                             length);
    }
}

void __p2_xmem_memmove(FAR void *destination, FAR const void *source,
                       uint32_t length)
{
  enum p2_xmem_region_e dest_region;
  enum p2_xmem_region_e source_region;
  uintptr_t dest = (uintptr_t)destination;
  uintptr_t src = (uintptr_t)source;
  bool backward;

  if (length == 0)
    {
      return;
    }

  dest_region = __p2_xmem_classify(destination, length);
  source_region = __p2_xmem_classify(source, length);
  if (destination == source)
    {
      return;
    }

  backward = dest > src && dest < src + length;
  if (dest_region == P2_XMEM_REGION_HUB &&
      source_region == P2_XMEM_REGION_HUB)
    {
      if (backward)
        {
          __p2_xmem_hub_copy_backward(destination, source, length);
        }
      else
        {
          __p2_xmem_hub_copy_forward(destination, source, length);
        }
    }
  else if (backward)
    {
      __p2_xmem_copy_backward(destination, dest_region, source,
                              source_region, length);
    }
  else
    {
      __p2_xmem_copy_forward(destination, dest_region, source, source_region,
                             length);
    }
}

void __p2_xmem_memset(FAR void *destination, uint8_t value,
                      uint32_t length)
{
  enum p2_xmem_region_e dest_region;
  FAR volatile uint8_t *dest = (FAR volatile uint8_t *)destination;

  if (length == 0)
    {
      return;
    }

  dest_region = __p2_xmem_classify(destination, length);
  if (dest_region == P2_XMEM_REGION_HUB)
    {
      while (length-- > 0)
        {
          *dest++ = value;
        }
    }
  else
    {
      uint8_t bounce[P2_XMEM_BOUNCE_SIZE];
      uint32_t index;

      for (index = 0; index < P2_XMEM_BOUNCE_SIZE; index++)
        {
          bounce[index] = value;
        }

      while (length > 0)
        {
          uint32_t chunk = length > P2_XMEM_BOUNCE_SIZE ?
                           P2_XMEM_BOUNCE_SIZE : length;

          __p2_xmem_transfer(P2_PSRAM_OPERATION_WRITE, (FAR void *)dest,
                             bounce, chunk);
          dest += chunk;
          length -= chunk;
        }
    }
}

void up_extraheaps_init(void)
{
  int ret;

  ret = p2_psram_initialize();
  if (ret < 0)
    {
      PANIC();
    }

#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED_SELFTEST_FULL
  /* A whole-device destructive test is safe only before allocator guard
   * nodes and free-list links exist in the tagged range.
   */

  ret = p2_psram_unified_fulltest();
  if (ret < 0)
    {
      PANIC();
    }
#endif

  /* Service readiness is a hard prerequisite: mm_addregion immediately
   * writes allocator guard nodes into this tagged address range.
   */

  kumm_addregion(
    (FAR void *)(P2_PSRAM_UNIFIED_BASE +
                 CONFIG_P2_EC32MB_PSRAM_UNIFIED_RESERVE_SIZE),
    P2_PSRAM_UNIFIED_SIZE -
      CONFIG_P2_EC32MB_PSRAM_UNIFIED_RESERVE_SIZE);
}
