/****************************************************************************
 * arch/p2/src/common/p2_stack.c
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
#include <errno.h>
#include <stdint.h>
#include <string.h>

#include <nuttx/arch.h>
#include <nuttx/board.h>
#include <nuttx/kmalloc.h>
#include <nuttx/sched.h>
#include <nuttx/tls.h>

#include <arch/board/board.h>

#include "p2_internal.h"

/****************************************************************************
 * Private Functions
 ****************************************************************************/

#ifdef CONFIG_STACK_COLORATION
static void p2_stack_color(void *stackbase, size_t nbytes)
{
  uint32_t *ptr;
  uint32_t *end;

  ptr = (uint32_t *)STACKFRAME_ALIGN_UP((uintptr_t)stackbase);
  end = (uint32_t *)STACKFRAME_ALIGN_DOWN((uintptr_t)stackbase + nbytes);
  while (ptr < end)
    {
      *ptr++ = P2_STACK_COLOR;
    }
}
#endif

/****************************************************************************
 * Public Functions
 ****************************************************************************/

uintptr_t p2_getsp(void)
{
  uintptr_t sp;

  __asm__ __volatile__("mov %0, ptra" : "=r" (sp));
  return sp;
}

#ifdef CONFIG_STACK_COLORATION
size_t up_check_tcbstack(struct tcb_s *tcb, size_t check_size)
{
  uintptr_t base;
  uintptr_t top;
  uint32_t *ptr;
  uint32_t *start;

  DEBUGASSERT(tcb != NULL);
  if (tcb == NULL || tcb->stack_base_ptr == NULL || check_size == 0)
    {
      return 0;
    }

  if (check_size > tcb->adj_stack_size)
    {
      check_size = tcb->adj_stack_size;
    }

  /* PTRA grows from stack_base_ptr toward the high address.  Scan the
   * requested range backward from the high end to find the highest word
   * that lost its marker.  With check_size equal to the whole stack this is
   * the high-water usage; with CONFIG_STACKCHECK_MARGIN it examines only
   * the overflow margin at the top.
   */

  base = (uintptr_t)tcb->stack_base_ptr +
         tcb->adj_stack_size - check_size;
  top = (uintptr_t)tcb->stack_base_ptr + tcb->adj_stack_size;
  start = (uint32_t *)STACKFRAME_ALIGN_UP(base);
  ptr = (uint32_t *)STACKFRAME_ALIGN_DOWN(top);

  while (ptr > start && *(ptr - 1) == P2_STACK_COLOR)
    {
      ptr--;
    }

  return (size_t)((uintptr_t)ptr - (uintptr_t)start);
}
#endif

void *up_stack_frame(struct tcb_s *tcb, size_t frame_size)
{
  void *frame;

  frame_size = STACKFRAME_ALIGN_UP(frame_size);
  if (tcb == NULL || tcb->stack_alloc_ptr == NULL ||
      tcb->adj_stack_size <= frame_size)
    {
      return NULL;
    }

  frame = tcb->stack_base_ptr;
  memset(frame, 0, frame_size);
  tcb->stack_base_ptr = (uint8_t *)tcb->stack_base_ptr + frame_size;
  tcb->adj_stack_size -= frame_size;
  return frame;
}

int up_use_stack(struct tcb_s *tcb, void *stack, size_t stack_size)
{
  uintptr_t base;
  uintptr_t top;

  DEBUGASSERT(tcb != NULL && stack != NULL);
  if (tcb == NULL || stack == NULL)
    {
      return -EINVAL;
    }

#ifdef CONFIG_TLS_ALIGNED
  DEBUGASSERT(stack_size <= TLS_MAXSTACK);
  if (stack_size > TLS_MAXSTACK)
    {
      stack_size = TLS_MAXSTACK;
    }
#endif

  if (tcb->stack_alloc_ptr != NULL &&
      (tcb->flags & TCB_FLAG_FREE_STACK) != 0)
    {
      up_release_stack(tcb, tcb->flags & TCB_FLAG_TTYPE_MASK);
    }

  base = STACKFRAME_ALIGN_UP((uintptr_t)stack);
  top = STACKFRAME_ALIGN_DOWN((uintptr_t)stack + stack_size);
  if (top <= base)
    {
      return -EINVAL;
    }

  tcb->stack_alloc_ptr = stack;
  tcb->stack_base_ptr = (void *)base;
  tcb->adj_stack_size = top - base;
  tcb->flags &= ~TCB_FLAG_FREE_STACK;

#ifdef CONFIG_STACK_COLORATION
  p2_stack_color(tcb->stack_base_ptr, tcb->adj_stack_size);
#endif

  return OK;
}

void up_release_stack(struct tcb_s *tcb, uint8_t ttype)
{
  (void)ttype;

  if (tcb != NULL && tcb->stack_alloc_ptr != NULL &&
      (tcb->flags & TCB_FLAG_FREE_STACK) != 0)
    {
#ifdef CONFIG_MM_KERNEL_HEAP
      if (ttype == TCB_FLAG_TTYPE_KERNEL)
        {
          kmm_free(tcb->stack_alloc_ptr);
        }
      else
#endif
        {
          kumm_free(tcb->stack_alloc_ptr);
        }
    }

  if (tcb != NULL)
    {
      tcb->stack_alloc_ptr = NULL;
      tcb->stack_base_ptr = NULL;
      tcb->adj_stack_size = 0;
      tcb->flags &= ~TCB_FLAG_FREE_STACK;
    }
}

int up_create_stack(struct tcb_s *tcb, size_t stack_size, uint8_t ttype)
{
  void *stack;
  int ret;

  DEBUGASSERT(tcb != NULL);
  if (tcb == NULL)
    {
      return -EINVAL;
    }

#ifdef CONFIG_TLS_ALIGNED
  DEBUGASSERT(stack_size <= TLS_MAXSTACK);
  if (stack_size > TLS_MAXSTACK)
    {
      stack_size = TLS_MAXSTACK;
    }
#endif

  if (tcb->stack_alloc_ptr != NULL)
    {
      up_release_stack(tcb, ttype);
    }

#ifdef CONFIG_TLS_ALIGNED
#  ifdef CONFIG_MM_KERNEL_HEAP
  if (ttype == TCB_FLAG_TTYPE_KERNEL)
    {
      stack = kmm_memalign(TLS_STACK_ALIGN, stack_size);
    }
  else
#  endif
    {
      stack = kumm_memalign(TLS_STACK_ALIGN, stack_size);
    }
#else
#  ifdef CONFIG_MM_KERNEL_HEAP
  if (ttype == TCB_FLAG_TTYPE_KERNEL)
    {
      stack = kmm_malloc(stack_size);
    }
  else
#  endif
    {
      stack = kumm_malloc(stack_size);
    }
#endif

  if (stack == NULL)
    {
      return -ENOMEM;
    }

  ret = up_use_stack(tcb, stack, stack_size);
  if (ret < 0)
    {
#ifdef CONFIG_MM_KERNEL_HEAP
      if (ttype == TCB_FLAG_TTYPE_KERNEL)
        {
          kmm_free(stack);
        }
      else
#endif
        {
          kumm_free(stack);
        }

      return ret;
    }

  tcb->flags |= TCB_FLAG_FREE_STACK;
#ifdef CONFIG_ARCH_LEDS
  board_autoled_on(LED_STACKCREATED);
#endif
  return OK;
}
