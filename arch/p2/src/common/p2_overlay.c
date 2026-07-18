/****************************************************************************
 * arch/p2/src/common/p2_overlay.c
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
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <nuttx/irq.h>
#include <nuttx/sched.h>

#include <arch/context.h>
#include <arch/irq.h>
#include <arch/overlay.h>

#include "sched/sched.h"
#include "p2_internal.h"
#include "p2_overlay_internal.h"

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_OVERLAY_PSRAM_BASE         UINT32_C(0x10000000)
#define P2_OVERLAY_PSRAM_END          UINT32_C(0x12000000)
#define P2_OVERLAY_CALLA_MASK         UINT32_C(0xfff00000)
#define P2_OVERLAY_CALLA_OPCODE       UINT32_C(0xfdc00000)
#define P2_OVERLAY_CRC_POLYNOMIAL     UINT32_C(0xedb88320)

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct p2_overlay_shadow_s
{
  uint32_t resume;
  uint32_t caller_group;
  uint32_t callee_group;
};

/****************************************************************************
 * Public Data
 ****************************************************************************/

extern uint8_t __p2_overlay_slot_start[];
extern uint8_t __p2_overlay_slot_end[];
extern uint8_t __p2_overlay_stubs_start[];
extern uint8_t __p2_overlay_stubs_end[];
extern const struct p2_overlay_entry_s __p2_overlay_entries_start[];
extern const struct p2_overlay_entry_s __p2_overlay_entries_end[];
extern struct p2_overlay_group_s __p2_overlay_groups_start[];
extern struct p2_overlay_group_s __p2_overlay_groups_end[];
extern void __p2_overlay_enter(void);

/****************************************************************************
 * Private Data
 ****************************************************************************/

/* The execution slot is global on this UP port.  Its owning task may be
 * preempted while resident kernel, interrupt, or other task code runs, but
 * no second task may enter an overlay until the owner's root shadow record
 * has unwound.  State transitions are protected by short critical sections;
 * the potentially blocking PSRAM copy deliberately occurs with interrupts
 * and scheduling enabled.  g_p2_overlay_transition makes any reentrant
 * overlay attempt fail closed while the slot contains a partial image.
 */

static struct p2_overlay_shadow_s
  g_p2_overlay_shadow[CONFIG_P2_HUB_OVERLAY_SHADOW_DEPTH]
  __attribute__((section(".bss.p2_overlay_shadow"), aligned(16)));

static FAR struct tcb_s *g_p2_overlay_owner;
static p2_overlay_loader_t g_p2_overlay_loader;
static FAR void *g_p2_overlay_loader_arg;
static uint32_t g_p2_overlay_depth;
static uint32_t g_p2_overlay_loaded_group;
static bool g_p2_overlay_ready;
static bool g_p2_overlay_relocated;
static bool g_p2_overlay_transition;
static int g_p2_overlay_error;

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static size_t p2_overlay_stub_count(void)
{
  return ((uintptr_t)__p2_overlay_stubs_end -
          (uintptr_t)__p2_overlay_stubs_start) / P2_OVERLAY_STUB_BYTES;
}

static size_t p2_overlay_entry_count(void)
{
  return (size_t)(__p2_overlay_entries_end -
                  __p2_overlay_entries_start);
}

static size_t p2_overlay_group_count(void)
{
  return (size_t)(__p2_overlay_groups_end - __p2_overlay_groups_start);
}

static size_t p2_overlay_slot_size(void)
{
  return (size_t)((uintptr_t)__p2_overlay_slot_end -
                  (uintptr_t)__p2_overlay_slot_start);
}

static bool p2_overlay_tagged_range(uintptr_t source, size_t size)
{
  return source >= P2_OVERLAY_PSRAM_BASE &&
         source < P2_OVERLAY_PSRAM_END &&
         size <= P2_OVERLAY_PSRAM_END - source;
}

static bool p2_overlay_group_flags_valid(uint32_t flags)
{
  return flags == P2_OVERLAY_GROUP_FLAGS_V1;
}

static uint32_t p2_overlay_crc32(FAR const uint8_t *data, size_t size)
{
  uint32_t crc = UINT32_C(0xffffffff);
  size_t index;
  unsigned int bit;

  for (index = 0; index < size; index++)
    {
      crc ^= data[index];
      for (bit = 0; bit < 8; bit++)
        {
          crc = (crc >> 1) ^
                ((crc & 1) != 0 ? P2_OVERLAY_CRC_POLYNOMIAL : 0);
        }
    }

  return crc ^ UINT32_C(0xffffffff);
}

static int p2_overlay_validate_group(uint32_t group, bool relocated)
{
  FAR const struct p2_overlay_group_s *descriptor;
  size_t groups = p2_overlay_group_count();
  size_t slot_size = p2_overlay_slot_size();

  if (group == P2_OVERLAY_RESIDENT_GROUP || group >= groups)
    {
      return -EINVAL;
    }

  descriptor = &__p2_overlay_groups_start[group];
  if (!p2_overlay_group_flags_valid(descriptor->flags) ||
      descriptor->image_size == 0 ||
      descriptor->image_size > slot_size ||
      (descriptor->image_size & (P2_OVERLAY_STUB_BYTES - 1)) != 0)
    {
      return -EINVAL;
    }

  if (relocated &&
      (!p2_overlay_tagged_range(descriptor->source,
                                descriptor->image_size) ||
       (descriptor->source & (P2_OVERLAY_STUB_BYTES - 1)) != 0))
    {
      return -EFAULT;
    }

  return 0;
}

static int p2_overlay_validate_tables(void)
{
  FAR const struct p2_overlay_group_s *resident;
  size_t stubs = p2_overlay_stub_count();
  size_t entries = p2_overlay_entry_count();
  size_t groups = p2_overlay_group_count();
  size_t slot_size = p2_overlay_slot_size();
  uintptr_t enter = (uintptr_t)__p2_overlay_enter;
  size_t index;

  if (slot_size != CONFIG_P2_HUB_OVERLAY_SLOT_SIZE ||
      slot_size == 0 ||
      (slot_size & (P2_OVERLAY_STUB_BYTES - 1)) != 0 ||
      ((uintptr_t)__p2_overlay_slot_start &
       (P2_OVERLAY_STUB_BYTES - 1)) != 0 ||
      stubs == 0 || stubs != entries || groups < 2 ||
      (enter & ~P2_RESUME_PC_MASK) != 0 ||
      (((uintptr_t)__p2_overlay_slot_start |
        (uintptr_t)__p2_overlay_slot_end) &
       P2_OVERLAY_DIRECT_TARGET_FLAG) != 0)
    {
      return -EINVAL;
    }

  resident = &__p2_overlay_groups_start[P2_OVERLAY_RESIDENT_GROUP];
  if (resident->source != 0 || resident->image_size != 0 ||
      resident->image_crc32 != 0 || resident->flags != 0)
    {
      return -EINVAL;
    }

  for (index = 1; index < groups; index++)
    {
      int ret = p2_overlay_validate_group((uint32_t)index, true);

      if (ret < 0)
        {
          return ret;
        }
    }

  for (index = 0; index < stubs; index++)
    {
      FAR const struct p2_overlay_entry_s *entry =
        &__p2_overlay_entries_start[index];
      uint32_t instruction =
        ((FAR const uint32_t *)__p2_overlay_stubs_start)[index];
      FAR const struct p2_overlay_group_s *descriptor;

      if ((instruction & P2_OVERLAY_CALLA_MASK) !=
          P2_OVERLAY_CALLA_OPCODE ||
          (instruction & P2_RESUME_PC_MASK) != enter ||
          entry->group == P2_OVERLAY_RESIDENT_GROUP ||
          entry->group >= groups)
        {
          return -EINVAL;
        }

      descriptor = &__p2_overlay_groups_start[entry->group];
      if ((entry->offset & (P2_OVERLAY_STUB_BYTES - 1)) != 0 ||
          entry->offset >= descriptor->image_size ||
          entry->offset >= slot_size)
        {
          return -EINVAL;
        }
    }

  return 0;
}

static int p2_overlay_load_group(uint32_t group)
{
  FAR const struct p2_overlay_group_s *descriptor =
    &__p2_overlay_groups_start[group];
  p2_overlay_loader_t loader = g_p2_overlay_loader;
  FAR void *loader_arg = g_p2_overlay_loader_arg;
  int ret;

  ret = p2_overlay_validate_group(group, true);
  if (ret < 0)
    {
      return ret;
    }

  if (loader == NULL)
    {
      return -ENOSYS;
    }

  ret = loader(loader_arg, group, descriptor->source,
               __p2_overlay_slot_start, descriptor->image_size);
  if (ret != 0)
    {
      return ret < 0 ? ret : -EIO;
    }

  __asm__ __volatile__("" : : : "memory");
  if (p2_overlay_crc32(__p2_overlay_slot_start,
                       descriptor->image_size) !=
      descriptor->image_crc32)
    {
      return -EILSEQ;
    }

  return 0;
}

static void p2_overlay_fail(int error) noreturn_function;
static void p2_overlay_fail(int error)
{
  irqstate_t irqstate = enter_critical_section();

  if (g_p2_overlay_error == 0)
    {
      g_p2_overlay_error = error < 0 ? error : -EFAULT;
    }

  g_p2_overlay_ready = false;
  g_p2_overlay_loaded_group = P2_OVERLAY_RESIDENT_GROUP;
  leave_critical_section(irqstate);

  p2_boot_trace("P2K:OVERLAY:FAIL");
  PANIC();

  /* PANIC is expected not to return.  Keep this fail-closed even in a build
   * that supplies a diagnostic PANIC hook which unexpectedly does return.
   */

  up_irq_disable();
  for (; ; )
    {
      __asm__ __volatile__("nop");
    }
}

static int p2_overlay_decode_stub(uint32_t stub_resume, size_t *index)
{
  uintptr_t start = (uintptr_t)__p2_overlay_stubs_start;
  uintptr_t end = (uintptr_t)__p2_overlay_stubs_end;
  uintptr_t pc = stub_resume & P2_RESUME_PC_MASK;
  uintptr_t delta;

  if ((stub_resume & P2_RESUME_RESERVED_MASK) != 0 || pc <= start ||
      pc > end || (pc & (P2_OVERLAY_STUB_BYTES - 1)) != 0)
    {
      return -EINVAL;
    }

  delta = pc - start;
  if ((delta % P2_OVERLAY_STUB_BYTES) != 0)
    {
      return -EINVAL;
    }

  *index = delta / P2_OVERLAY_STUB_BYTES - 1;
  if (*index >= p2_overlay_entry_count())
    {
      return -EINVAL;
    }

  return 0;
}

static int p2_overlay_validate_resume(uint32_t resume)
{
  uintptr_t pc = resume & P2_RESUME_PC_MASK;

  if ((resume & P2_RESUME_RESERVED_MASK) != 0 ||
      (pc & (P2_OVERLAY_STUB_BYTES - 1)) != 0 ||
      pc < UINT32_C(0x400) ||
      pc >= (uintptr_t)__p2_overlay_slot_end)
    {
      return -EINVAL;
    }

  return 0;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int p2_overlay_relocate_groups(uintptr_t tagged_base, size_t backing_size)
{
  FAR struct p2_overlay_group_s *resident =
    &__p2_overlay_groups_start[P2_OVERLAY_RESIDENT_GROUP];
  size_t groups = p2_overlay_group_count();
  irqstate_t irqstate;
  size_t index;
  int ret = 0;

  if (up_interrupt_context() ||
      !p2_overlay_tagged_range(tagged_base, backing_size) ||
      (tagged_base & (P2_OVERLAY_STUB_BYTES - 1)) != 0 || groups < 2)
    {
      return -EINVAL;
    }

  irqstate = enter_critical_section();
  if (g_p2_overlay_ready || g_p2_overlay_loader != NULL ||
      g_p2_overlay_owner != NULL || g_p2_overlay_depth != 0 ||
      g_p2_overlay_transition || g_p2_overlay_relocated)
    {
      leave_critical_section(irqstate);
      return -EBUSY;
    }

  /* Validate every offset and flag before changing the first record. */

  if (resident->source != 0 || resident->image_size != 0 ||
      resident->image_crc32 != 0 || resident->flags != 0)
    {
      ret = -EINVAL;
    }

  for (index = 1; ret == 0 && index < groups; index++)
    {
      FAR struct p2_overlay_group_s *descriptor =
        &__p2_overlay_groups_start[index];

      ret = p2_overlay_validate_group((uint32_t)index, false);
      if (ret < 0 || descriptor->source > backing_size ||
          descriptor->image_size > backing_size - descriptor->source ||
          (descriptor->source & (P2_OVERLAY_STUB_BYTES - 1)) != 0)
        {
          ret = ret < 0 ? ret : -ERANGE;
          break;
        }
    }

  if (ret == 0)
    {
      for (index = 1; index < groups; index++)
        {
          FAR struct p2_overlay_group_s *descriptor =
            &__p2_overlay_groups_start[index];

          descriptor->source += tagged_base;
        }

      g_p2_overlay_relocated = true;
    }

  leave_critical_section(irqstate);
  return ret;
}

int p2_overlay_register_loader(p2_overlay_loader_t loader, FAR void *arg)
{
  irqstate_t irqstate;
  int ret;

  if (loader == NULL || up_interrupt_context())
    {
      return -EINVAL;
    }

  irqstate = enter_critical_section();
  if (g_p2_overlay_owner != NULL || g_p2_overlay_depth != 0 ||
      g_p2_overlay_transition || !g_p2_overlay_relocated)
    {
      leave_critical_section(irqstate);
      return -EBUSY;
    }

  g_p2_overlay_ready = false;
  g_p2_overlay_loader = NULL;
  g_p2_overlay_loader_arg = NULL;
  g_p2_overlay_loaded_group = P2_OVERLAY_RESIDENT_GROUP;
  ret = p2_overlay_validate_tables();
  if (ret == 0)
    {
      g_p2_overlay_loader = loader;
      g_p2_overlay_loader_arg = arg;
      g_p2_overlay_loaded_group = P2_OVERLAY_RESIDENT_GROUP;
      g_p2_overlay_error = 0;
      g_p2_overlay_ready = true;
    }

  leave_critical_section(irqstate);
  return ret;
}

int p2_overlay_last_error(void)
{
  return g_p2_overlay_error;
}

/* Called only by the register-preserving assembly veneer. */

uintptr_t p2_overlay_dispatch_enter(uint32_t stub_resume,
                                    uint32_t caller_resume)
{
  FAR struct tcb_s *task;
  FAR const struct p2_overlay_entry_s *entry;
  FAR struct p2_overlay_shadow_s *shadow;
  irqstate_t irqstate;
  size_t stub_index;
  uint32_t caller_group;
  uint32_t group;
  bool direct;
  bool needs_load;
  int ret;

  if (up_interrupt_context())
    {
      p2_overlay_fail(-EPERM);
    }

  ret = p2_overlay_decode_stub(stub_resume, &stub_index);
  if (ret < 0 || p2_overlay_validate_resume(caller_resume) < 0)
    {
      p2_overlay_fail(ret < 0 ? ret : -EINVAL);
    }

  entry = &__p2_overlay_entries_start[stub_index];
  group = entry->group;
  ret = p2_overlay_validate_group(group, true);
  if (ret < 0 || entry->offset >=
                 __p2_overlay_groups_start[group].image_size ||
      (entry->offset & (P2_OVERLAY_STUB_BYTES - 1)) != 0)
    {
      p2_overlay_fail(ret < 0 ? ret : -EINVAL);
    }

  task = this_task();
  if (task == NULL)
    {
      p2_overlay_fail(-ESRCH);
    }

  irqstate = enter_critical_section();
  if (!g_p2_overlay_ready || g_p2_overlay_loader == NULL ||
      g_p2_overlay_transition ||
      g_p2_overlay_depth > CONFIG_P2_HUB_OVERLAY_SHADOW_DEPTH ||
      (g_p2_overlay_depth == 0 && g_p2_overlay_owner != NULL) ||
      (g_p2_overlay_depth != 0 && g_p2_overlay_owner != task))
    {
      int error = !g_p2_overlay_ready || g_p2_overlay_loader == NULL ?
                  -ENOSYS :
                  g_p2_overlay_depth >
                    CONFIG_P2_HUB_OVERLAY_SHADOW_DEPTH ? -EOVERFLOW : -EBUSY;

      leave_critical_section(irqstate);
      p2_overlay_fail(error);
    }

  /* Calls within the currently loaded group need neither a reload nor a
   * shadow record.  The veneer leaves the original CALLA resume untouched,
   * and the callee consumes it directly with RETA.  Require the top record
   * to agree with the published image so a corrupted or transient state can
   * never bypass the fail-closed path.  In particular, a direct call remains
   * valid when the cross-group shadow stack is exactly full because it does
   * not increase that depth.
   */

  direct = g_p2_overlay_depth != 0 &&
           g_p2_overlay_loaded_group == group;
  if (direct)
    {
      if (g_p2_overlay_shadow[g_p2_overlay_depth - 1].callee_group != group)
        {
          leave_critical_section(irqstate);
          p2_overlay_fail(-EFAULT);
        }

      leave_critical_section(irqstate);
      return ((uintptr_t)__p2_overlay_slot_start + entry->offset) |
             P2_OVERLAY_DIRECT_TARGET_FLAG;
    }

  if (g_p2_overlay_depth >= CONFIG_P2_HUB_OVERLAY_SHADOW_DEPTH)
    {
      leave_critical_section(irqstate);
      p2_overlay_fail(-EOVERFLOW);
    }

  if (g_p2_overlay_depth == 0)
    {
      g_p2_overlay_owner = task;
      caller_group = P2_OVERLAY_RESIDENT_GROUP;
    }
  else
    {
      caller_group = g_p2_overlay_loaded_group;
      if (caller_group == P2_OVERLAY_RESIDENT_GROUP)
        {
          leave_critical_section(irqstate);
          p2_overlay_fail(-EFAULT);
        }
    }

  shadow = &g_p2_overlay_shadow[g_p2_overlay_depth++];
  shadow->resume = caller_resume;
  shadow->caller_group = caller_group;
  shadow->callee_group = group;

  needs_load = g_p2_overlay_loaded_group != group;
  if (needs_load)
    {
      g_p2_overlay_loaded_group = P2_OVERLAY_RESIDENT_GROUP;
      g_p2_overlay_transition = true;
    }

  leave_critical_section(irqstate);

  if (needs_load)
    {
      ret = p2_overlay_load_group(group);
      if (ret < 0)
        {
          p2_overlay_fail(ret);
        }

      irqstate = enter_critical_section();
      if (!g_p2_overlay_transition || g_p2_overlay_owner != task ||
          g_p2_overlay_depth == 0 ||
          g_p2_overlay_shadow[g_p2_overlay_depth - 1].callee_group != group)
        {
          leave_critical_section(irqstate);
          p2_overlay_fail(-EFAULT);
        }

      g_p2_overlay_loaded_group = group;
      g_p2_overlay_transition = false;
      leave_critical_section(irqstate);
    }

  return (uintptr_t)__p2_overlay_slot_start + entry->offset;
}

/* Called only by the assembly veneer after it has saved both scalar-return
 * registers.  The returned packed resume is installed on the task stack and
 * consumed by one final RETA.
 */

uint32_t p2_overlay_dispatch_exit(void)
{
  FAR struct tcb_s *task;
  FAR struct p2_overlay_shadow_s *shadow;
  irqstate_t irqstate;
  uint32_t caller_group;
  uint32_t callee_group;
  uint32_t resume;
  bool needs_load;
  int ret;

  if (up_interrupt_context())
    {
      p2_overlay_fail(-EPERM);
    }

  task = this_task();
  irqstate = enter_critical_section();
  if (!g_p2_overlay_ready || task == NULL || g_p2_overlay_owner != task ||
      g_p2_overlay_transition || g_p2_overlay_depth == 0)
    {
      leave_critical_section(irqstate);
      p2_overlay_fail(-EFAULT);
    }

  shadow = &g_p2_overlay_shadow[g_p2_overlay_depth - 1];
  caller_group = shadow->caller_group;
  callee_group = shadow->callee_group;
  resume = shadow->resume;

  if (g_p2_overlay_loaded_group != callee_group ||
      p2_overlay_validate_resume(resume) < 0)
    {
      leave_critical_section(irqstate);
      p2_overlay_fail(-EFAULT);
    }

  needs_load = caller_group != P2_OVERLAY_RESIDENT_GROUP &&
               caller_group != callee_group;
  if (needs_load)
    {
      g_p2_overlay_loaded_group = P2_OVERLAY_RESIDENT_GROUP;
      g_p2_overlay_transition = true;
    }

  leave_critical_section(irqstate);

  if (needs_load)
    {
      ret = p2_overlay_load_group(caller_group);
      if (ret < 0)
        {
          p2_overlay_fail(ret);
        }

      irqstate = enter_critical_section();
      if (!g_p2_overlay_transition || g_p2_overlay_owner != task ||
          g_p2_overlay_depth == 0 ||
          &g_p2_overlay_shadow[g_p2_overlay_depth - 1] != shadow)
        {
          leave_critical_section(irqstate);
          p2_overlay_fail(-EFAULT);
        }

      g_p2_overlay_loaded_group = caller_group;
      g_p2_overlay_transition = false;
      leave_critical_section(irqstate);
    }

  irqstate = enter_critical_section();
  if (g_p2_overlay_owner != task || g_p2_overlay_depth == 0 ||
      &g_p2_overlay_shadow[g_p2_overlay_depth - 1] != shadow)
    {
      leave_critical_section(irqstate);
      p2_overlay_fail(-EFAULT);
    }

  g_p2_overlay_depth--;
  if (g_p2_overlay_depth == 0)
    {
      g_p2_overlay_owner = NULL;
    }

  leave_critical_section(irqstate);
  return resume;
}

static_assert(sizeof(struct p2_overlay_entry_s) == P2_OVERLAY_ENTRY_BYTES,
              "P2 overlay entry ABI changed");
static_assert(sizeof(struct p2_overlay_group_s) == P2_OVERLAY_GROUP_BYTES,
              "P2 overlay group ABI changed");
static_assert(sizeof(struct p2_overlay_shadow_s) == 12,
              "P2 overlay shadow record changed");
