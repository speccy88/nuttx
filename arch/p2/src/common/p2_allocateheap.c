/****************************************************************************
 * arch/p2/src/common/p2_allocateheap.c
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
#include <stddef.h>
#include <stdint.h>

#include "p2_internal.h"

/****************************************************************************
 * Public Data
 ****************************************************************************/

extern uint8_t _sheap[];
extern uint8_t _eheap[];

/****************************************************************************
 * Private Functions
 ****************************************************************************/

#ifdef CONFIG_MM_KERNEL_HEAP
static uintptr_t p2_kernel_heap_end(void)
{
  uintptr_t start = (uintptr_t)_sheap;
  uintptr_t end = (start + CONFIG_MM_KERNEL_HEAPSIZE + 15u) &
                  ~(uintptr_t)15;

  /* Both allocators require a real initial Hub region.  Unified PSRAM is
   * added to kumm only after these heaps and the service cog are
   * initialized.
   */

  if (end <= start || end >= (uintptr_t)_eheap)
    {
      PANIC();
    }

  return end;
}
#endif

/****************************************************************************
 * Public Functions
 ****************************************************************************/

/****************************************************************************
 * Name: up_allocate_heap
 ****************************************************************************/

void up_allocate_heap(void **heap_start, size_t *heap_size)
{
#ifdef CONFIG_MM_KERNEL_HEAP
  uintptr_t start = p2_kernel_heap_end();

  *heap_start = (void *)start;
  *heap_size = (size_t)((uintptr_t)_eheap - start);
#else
  *heap_start = _sheap;
  *heap_size  = (size_t)(_eheap - _sheap);
#endif
  p2_boot_trace("P2K:HEAP");
}

#ifdef CONFIG_MM_KERNEL_HEAP
/****************************************************************************
 * Name: up_allocate_kheap
 ****************************************************************************/

void up_allocate_kheap(void **heap_start, size_t *heap_size)
{
  uintptr_t end = p2_kernel_heap_end();

  *heap_start = _sheap;
  *heap_size = (size_t)(end - (uintptr_t)_sheap);
  p2_boot_trace("P2K:KHEAP");
}
#endif
