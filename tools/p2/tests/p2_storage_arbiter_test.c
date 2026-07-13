/****************************************************************************
 * tools/p2/tests/p2_storage_arbiter_test.c
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

#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>

#include "p2_ec32mb_storage_arbiter.h"

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct test_context_s
{
  struct p2_storage_lines_s log[32];
  unsigned int log_count;
  bool locked;
};

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static int test_lock(void *arg, uint32_t timeout)
{
  struct test_context_s *context = arg;

  if (context->locked)
    {
      assert(timeout == 7);
      return -ETIMEDOUT;
    }

  context->locked = true;
  return 0;
}

static int test_unlock(void *arg)
{
  struct test_context_s *context = arg;

  assert(context->locked);
  context->locked = false;
  return 0;
}

static void test_apply(void *arg, const struct p2_storage_lines_s *lines)
{
  struct test_context_s *context = arg;

  assert(context->log_count < 32);
  context->log[context->log_count++] = *lines;
}

static void assert_idle(const struct p2_storage_arbiter_s *arbiter)
{
  assert(arbiter->state == P2_STORAGE_IDLE);
  assert(arbiter->lines.outputs == P2_STORAGE_OUTPUTS);
  assert(arbiter->lines.levels == (P2_STORAGE_MOSI |
                                   P2_STORAGE_FLASH_CLK |
                                   P2_STORAGE_FLASH_CS));
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int main(void)
{
  static const struct p2_storage_arbiter_ops_s ops =
  {
    test_lock,
    test_unlock,
    test_apply,
  };

  struct p2_storage_arbiter_s arbiter;
  struct test_context_s context =
  {
    0
  };

  unsigned int log_count;

  assert(p2_storage_arbiter_initialize(&arbiter, &ops, &context) == 0);
  assert_idle(&arbiter);
  assert(arbiter.owner == P2_STORAGE_TARGET_NONE);

  /* Flash select/release and exact line roles. */

  assert(p2_storage_arbiter_acquire(&arbiter,
                                    P2_STORAGE_TARGET_FLASH, 7) == 0);
  assert(p2_storage_arbiter_select(&arbiter,
                                   P2_STORAGE_TARGET_FLASH) == 0);
  assert(arbiter.state == P2_STORAGE_FLASH_SELECTED);
  assert(arbiter.lines.outputs == P2_STORAGE_OUTPUTS);
  assert(arbiter.lines.levels == (P2_STORAGE_MOSI |
                                  P2_STORAGE_FLASH_CLK));
  assert(p2_storage_arbiter_transaction_allowed(
           &arbiter, P2_STORAGE_TARGET_FLASH));
  assert(!p2_storage_arbiter_transaction_allowed(
           &arbiter, P2_STORAGE_TARGET_SD));

  /* A timed-out contender cannot disturb the active target. */

  log_count = context.log_count;
  assert(p2_storage_arbiter_acquire(&arbiter,
                                    P2_STORAGE_TARGET_SD, 7) ==
         -ETIMEDOUT);
  assert(arbiter.state == P2_STORAGE_FLASH_SELECTED);
  assert(context.log_count == log_count);

  /* A conflicting select by the lock owner fails closed in RECOVERY. */

  assert(p2_storage_arbiter_select(&arbiter, P2_STORAGE_TARGET_SD) ==
         -EBUSY);
  assert(arbiter.state == P2_STORAGE_RECOVERY);
  assert(arbiter.lines.levels == (P2_STORAGE_MOSI |
                                  P2_STORAGE_FLASH_CLK |
                                  P2_STORAGE_FLASH_CS));
  assert(!p2_storage_arbiter_transaction_allowed(
           &arbiter, P2_STORAGE_TARGET_FLASH));
  assert(p2_storage_arbiter_recover(&arbiter,
                                    P2_STORAGE_TARGET_FLASH) == 0);
  assert(p2_storage_arbiter_release(&arbiter,
                                    P2_STORAGE_TARGET_FLASH) == 0);
  assert_idle(&arbiter);
  assert(!context.locked);

  /* Flash-to-SD is a release followed by a new serialized acquisition. */

  assert(p2_storage_arbiter_acquire(&arbiter,
                                    P2_STORAGE_TARGET_FLASH, 7) == 0);
  assert(p2_storage_arbiter_select(&arbiter,
                                   P2_STORAGE_TARGET_FLASH) == 0);
  assert(p2_storage_arbiter_deselect(&arbiter,
                                     P2_STORAGE_TARGET_FLASH) == 0);
  assert(p2_storage_arbiter_release(&arbiter,
                                    P2_STORAGE_TARGET_FLASH) == 0);
  assert(p2_storage_arbiter_acquire(&arbiter,
                                    P2_STORAGE_TARGET_SD, 7) == 0);
  assert(p2_storage_arbiter_select(&arbiter,
                                   P2_STORAGE_TARGET_SD) == 0);
  assert(arbiter.state == P2_STORAGE_SD_SELECTED);
  assert(arbiter.lines.outputs == P2_STORAGE_OUTPUTS);
  assert(arbiter.lines.levels == P2_STORAGE_MOSI);
  assert(p2_storage_arbiter_release(&arbiter,
                                    P2_STORAGE_TARGET_SD) == 0);
  assert_idle(&arbiter);

  /* SD-to-flash takes the same single-owner path. */

  assert(p2_storage_arbiter_acquire(&arbiter,
                                    P2_STORAGE_TARGET_SD, 7) == 0);
  assert(p2_storage_arbiter_select(&arbiter,
                                   P2_STORAGE_TARGET_SD) == 0);
  assert(p2_storage_arbiter_release(&arbiter,
                                    P2_STORAGE_TARGET_SD) == 0);
  assert(p2_storage_arbiter_acquire(&arbiter,
                                    P2_STORAGE_TARGET_FLASH, 7) == 0);
  assert(p2_storage_arbiter_select(&arbiter,
                                   P2_STORAGE_TARGET_FLASH) == 0);
  assert(p2_storage_arbiter_release(&arbiter,
                                    P2_STORAGE_TARGET_FLASH) == 0);
  assert_idle(&arbiter);

  return 0;
}
