/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_storage_arbiter.c
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

#include <errno.h>
#include <stddef.h>

#include "p2_ec32mb_storage_arbiter.h"

/****************************************************************************
 * Private Data
 ****************************************************************************/

/* P59 is held high when idle so a recovery edge cannot begin an all-zero
 * command.  P60 and P61 are both high to deassert the two chip selects.
 */

static const struct p2_storage_lines_s g_idle_lines =
{
  P2_STORAGE_OUTPUTS,
  P2_STORAGE_MOSI | P2_STORAGE_FLASH_CLK | P2_STORAGE_FLASH_CS
};

/* The flash uses SPI mode 3.  Its clock therefore starts high before P61 is
 * lowered.  The inactive SD sees no clock edge on P61.
 */

static const struct p2_storage_lines_s g_flash_lines =
{
  P2_STORAGE_OUTPUTS,
  P2_STORAGE_MOSI | P2_STORAGE_FLASH_CLK
};

/* The stable profile uses SPI mode 0.  P60 is lowered first to select the
 * card, then P61 is lowered to establish the clock-idle level.  The
 * accelerated profile uses mode 3 and keeps P61 high.  In either case the
 * inactive flash sees no clock edge on P60.
 */

static const struct p2_storage_lines_s g_sd_lines =
{
  P2_STORAGE_OUTPUTS,
#ifdef CONFIG_P2_STORAGE_SD_MODE3
  P2_STORAGE_MOSI | P2_STORAGE_SD_CLK
#else
  P2_STORAGE_MOSI
#endif
};

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static bool p2_storage_target_valid(enum p2_storage_target_e target)
{
  return target == P2_STORAGE_TARGET_FLASH ||
         target == P2_STORAGE_TARGET_SD;
}

static void p2_storage_apply(struct p2_storage_arbiter_s *arbiter,
                             const struct p2_storage_lines_s *lines)
{
  arbiter->lines = *lines;
  arbiter->ops->apply(arbiter->arg, lines);
}

static void p2_storage_enter_recovery(
  struct p2_storage_arbiter_s *arbiter)
{
  /* RECOVERY has the same electrical levels as IDLE but remains a distinct
   * logical state.  No transfer is accepted until recover() acknowledges it.
   */

  p2_storage_apply(arbiter, &g_idle_lines);
  arbiter->state = P2_STORAGE_RECOVERY;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int p2_storage_arbiter_initialize(
  struct p2_storage_arbiter_s *arbiter,
  const struct p2_storage_arbiter_ops_s *ops,
  void *arg)
{
  if (arbiter == NULL || ops == NULL || ops->lock == NULL ||
      ops->unlock == NULL || ops->apply == NULL)
    {
      return -EINVAL;
    }

  arbiter->ops = ops;
  arbiter->arg = arg;
  arbiter->owner = P2_STORAGE_TARGET_NONE;
  arbiter->state = P2_STORAGE_RECOVERY;
  p2_storage_apply(arbiter, &g_idle_lines);
  arbiter->state = P2_STORAGE_IDLE;
  return 0;
}

int p2_storage_arbiter_acquire(struct p2_storage_arbiter_s *arbiter,
                               enum p2_storage_target_e target,
                               uint32_t timeout)
{
  int ret;

  if (arbiter == NULL || !p2_storage_target_valid(target))
    {
      return -EINVAL;
    }

  ret = arbiter->ops->lock(arbiter->arg, timeout);
  if (ret < 0)
    {
      /* Another owner can still be driving the pins.  A timed-out contender
       * must not change either the state or the electrical levels.
       */

      return ret;
    }

  if (arbiter->owner != P2_STORAGE_TARGET_NONE ||
      arbiter->state != P2_STORAGE_IDLE)
    {
      p2_storage_enter_recovery(arbiter);
      arbiter->ops->unlock(arbiter->arg);
      return -EIO;
    }

  p2_storage_apply(arbiter, &g_idle_lines);
  arbiter->owner = target;
  return 0;
}

int p2_storage_arbiter_select(struct p2_storage_arbiter_s *arbiter,
                              enum p2_storage_target_e target)
{
  if (arbiter == NULL || !p2_storage_target_valid(target))
    {
      return -EINVAL;
    }

  if (arbiter->owner != target || arbiter->state != P2_STORAGE_IDLE)
    {
      if (arbiter->owner != P2_STORAGE_TARGET_NONE)
        {
          p2_storage_enter_recovery(arbiter);
        }

      return -EBUSY;
    }

  /* Always pass through the common safe levels before changing roles. */

  p2_storage_apply(arbiter, &g_idle_lines);
  if (target == P2_STORAGE_TARGET_FLASH)
    {
      p2_storage_apply(arbiter, &g_flash_lines);
      arbiter->state = P2_STORAGE_FLASH_SELECTED;
    }
  else
    {
      p2_storage_apply(arbiter, &g_sd_lines);
      arbiter->state = P2_STORAGE_SD_SELECTED;
    }

  return 0;
}

int p2_storage_arbiter_deselect(struct p2_storage_arbiter_s *arbiter,
                                enum p2_storage_target_e target)
{
  enum p2_storage_state_e expected;

  if (arbiter == NULL || !p2_storage_target_valid(target))
    {
      return -EINVAL;
    }

  expected = target == P2_STORAGE_TARGET_FLASH ?
             P2_STORAGE_FLASH_SELECTED : P2_STORAGE_SD_SELECTED;
  if (arbiter->owner != target || arbiter->state != expected)
    {
      if (arbiter->owner != P2_STORAGE_TARGET_NONE)
        {
          p2_storage_enter_recovery(arbiter);
        }

      return -EPERM;
    }

  p2_storage_apply(arbiter, &g_idle_lines);
  arbiter->state = P2_STORAGE_IDLE;
  return 0;
}

int p2_storage_arbiter_fail(struct p2_storage_arbiter_s *arbiter,
                            enum p2_storage_target_e target)
{
  if (arbiter == NULL || arbiter->owner != target ||
      !p2_storage_target_valid(target))
    {
      return -EPERM;
    }

  p2_storage_enter_recovery(arbiter);
  return 0;
}

int p2_storage_arbiter_recover(struct p2_storage_arbiter_s *arbiter,
                               enum p2_storage_target_e target)
{
  if (arbiter == NULL || arbiter->owner != target ||
      arbiter->state != P2_STORAGE_RECOVERY ||
      !p2_storage_target_valid(target))
    {
      return -EPERM;
    }

  p2_storage_apply(arbiter, &g_idle_lines);
  arbiter->state = P2_STORAGE_IDLE;
  return 0;
}

int p2_storage_arbiter_release(struct p2_storage_arbiter_s *arbiter,
                               enum p2_storage_target_e target)
{
  int ret;

  if (arbiter == NULL || arbiter->owner != target ||
      !p2_storage_target_valid(target))
    {
      return -EPERM;
    }

  if (arbiter->state == P2_STORAGE_FLASH_SELECTED ||
      arbiter->state == P2_STORAGE_SD_SELECTED)
    {
      ret = p2_storage_arbiter_deselect(arbiter, target);
      if (ret < 0)
        {
          return ret;
        }
    }
  else if (arbiter->state == P2_STORAGE_RECOVERY)
    {
      ret = p2_storage_arbiter_recover(arbiter, target);
      if (ret < 0)
        {
          return ret;
        }
    }

  p2_storage_apply(arbiter, &g_idle_lines);
  arbiter->state = P2_STORAGE_IDLE;
  arbiter->owner = P2_STORAGE_TARGET_NONE;
  return arbiter->ops->unlock(arbiter->arg);
}

bool p2_storage_arbiter_transaction_allowed(
  const struct p2_storage_arbiter_s *arbiter,
  enum p2_storage_target_e target)
{
  enum p2_storage_state_e selected;

  if (arbiter == NULL || arbiter->owner != target ||
      !p2_storage_target_valid(target))
    {
      return false;
    }

  selected = target == P2_STORAGE_TARGET_FLASH ?
             P2_STORAGE_FLASH_SELECTED : P2_STORAGE_SD_SELECTED;
  return arbiter->state == P2_STORAGE_IDLE ||
         arbiter->state == selected;
}
