/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_pins.c
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

#ifndef P2_PIN_MANAGER_HOST_TEST
#  include <nuttx/config.h>
#  include <nuttx/irq.h>
#endif

#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifndef P2_PIN_MANAGER_HOST_TEST
#  include <arch/board/board.h>
#else
#  ifndef BOARD_LED0_PIN
#    define BOARD_LED0_PIN 38
#  endif
#  ifndef BOARD_LED1_PIN
#    define BOARD_LED1_PIN 39
#  endif
#  if !defined(BOARD_PSRAM_FIRST_PIN) && \
      !defined(P2_PIN_MANAGER_HOST_NO_PSRAM)
#    define BOARD_PSRAM_FIRST_PIN 40
#    define BOARD_PSRAM_LAST_PIN 57
#    define BOARD_HAVE_PSRAM 1
#  endif
#  ifndef BOARD_FLASH_MISO_PIN
#    define BOARD_FLASH_MISO_PIN 58
#    define BOARD_FLASH_CS_PIN 61
#  endif
#  ifndef BOARD_CONSOLE_TX_PIN
#    define BOARD_CONSOLE_TX_PIN 62
#  endif
#endif

#include "p2_ec32mb_pins.h"

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

/* P2 custom I/O drive fields used for safe pull states. */

#define P2_PIN_HIGH_15K              0x00001000
#define P2_PIN_HIGH_FLOAT            0x00003800
#define P2_PIN_LOW_15K               0x00000200
#define P2_PIN_LOW_FLOAT             0x00000700

#define P2_PIN_LOCK_NONE             (-1)

/****************************************************************************
 * Private Data
 ****************************************************************************/

static struct p2_pin_state_s g_pins[P2_PIN_COUNT];
static bool g_initialized;
static int g_lockid = P2_PIN_LOCK_NONE;

#ifdef P2_PIN_MANAGER_HOST_TEST
static unsigned int g_test_cog;
static unsigned int g_test_safe_apply_count;
static unsigned int g_test_cog_stop_count;
#endif

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static enum p2_pin_owner_e p2_pin_role_owner(enum p2_pin_role_e role)
{
  switch (role)
    {
      case P2_PIN_ROLE_BOARD_LED:
        return P2_PIN_OWNER_BOARD_LED;

      case P2_PIN_ROLE_PSRAM:
        return P2_PIN_OWNER_PSRAM;

      case P2_PIN_ROLE_STORAGE:
        return P2_PIN_OWNER_STORAGE;

      case P2_PIN_ROLE_CONSOLE:
        return P2_PIN_OWNER_CONSOLE;

      case P2_PIN_ROLE_NONE:
      default:
        return P2_PIN_OWNER_NONE;
    }
}

#ifdef P2_PIN_MANAGER_HOST_TEST

typedef unsigned int p2_pin_irqstate_t;

static int p2_pin_locknew(void)
{
  return 0;
}

static p2_pin_irqstate_t p2_pin_lock(void)
{
  return 0;
}

static void p2_pin_unlock(p2_pin_irqstate_t flags)
{
  (void)flags;
}

static unsigned int p2_pin_cogid(void)
{
  return g_test_cog;
}

static void p2_pin_apply_safe(unsigned int pin, enum p2_pin_safe_e safe)
{
  (void)pin;
  (void)safe;
  g_test_safe_apply_count++;
}

#else

typedef irqstate_t p2_pin_irqstate_t;

static int p2_pin_locknew(void)
{
  unsigned int id;
  unsigned int failed;

  __asm__ __volatile__("locknew %0 wc\n\twrc %1"
                       : "=r" (id), "=r" (failed));
  return failed != 0 ? P2_PIN_LOCK_NONE : (int)id;
}

static p2_pin_irqstate_t p2_pin_lock(void)
{
  p2_pin_irqstate_t flags;
  unsigned int acquired;

  flags = up_irq_save();
  do
    {
      __asm__ __volatile__("locktry %1 wc\n\twrc %0"
                           : "=r" (acquired)
                           : "r" (g_lockid));
    }
  while (acquired == 0);

  return flags;
}

static void p2_pin_unlock(p2_pin_irqstate_t flags)
{
  __asm__ __volatile__("lockrel %0" : : "r" (g_lockid));
  up_irq_restore(flags);
}

static unsigned int p2_pin_cogid(void)
{
  unsigned int cog;

  __asm__ __volatile__("cogid %0" : "=r" (cog));
  return cog;
}

static void p2_pin_apply_safe(unsigned int pin, enum p2_pin_safe_e safe)
{
  uint32_t mode = 0;
  bool drive = false;
  bool high = false;

  /* Stop any Smart Pin engine before changing its electrical state. */

  __asm__ __volatile__("dirl %0" : : "r" (pin));

  switch (safe)
    {
      case P2_PIN_SAFE_LOW:
        drive = true;
        break;

      case P2_PIN_SAFE_HIGH:
        drive = true;
        high = true;
        break;

      case P2_PIN_SAFE_PULL_UP:
        mode = P2_PIN_HIGH_15K | P2_PIN_LOW_FLOAT;
        drive = true;
        high = true;
        break;

      case P2_PIN_SAFE_PULL_DOWN:
        mode = P2_PIN_HIGH_FLOAT | P2_PIN_LOW_15K;
        drive = true;
        break;

      case P2_PIN_SAFE_FLOAT:
      default:
        break;
    }

  __asm__ __volatile__("wrpin %0, %1" : : "r" (mode), "r" (pin));

  if (high)
    {
      __asm__ __volatile__("outh %0" : : "r" (pin));
    }
  else
    {
      __asm__ __volatile__("outl %0" : : "r" (pin));
    }

  if (drive)
    {
      __asm__ __volatile__("dirh %0" : : "r" (pin));
    }
}

#endif

static bool p2_pin_owner_valid(enum p2_pin_owner_e owner)
{
  return owner > P2_PIN_OWNER_NONE && owner <= P2_PIN_OWNER_I2C;
}

static bool p2_pin_config_valid(const struct p2_pin_config_s *config)
{
  if (config == NULL ||
      config->direction > P2_PIN_DIRECTION_BIDIRECTIONAL ||
      config->drive > P2_PIN_DRIVE_ANALOG ||
      config->event > P2_PIN_EVENT_SE4 ||
      config->safe > P2_PIN_SAFE_PULL_DOWN ||
      config->smartpin_mode > P2_SMARTPIN_MODE_MAX ||
      (config->smartpin_mode & 1) != 0)
    {
      return false;
    }

  return true;
}

static bool p2_pin_event_busy(unsigned int pin, unsigned int cog,
                              enum p2_pin_event_e event)
{
  unsigned int index;

  if (event == P2_PIN_EVENT_NONE)
    {
      return false;
    }

  for (index = 0; index < P2_PIN_COUNT; index++)
    {
      if (index != pin && g_pins[index].refs != 0 &&
          g_pins[index].owning_cog == cog &&
          g_pins[index].event == event)
        {
          return true;
        }
    }

  return false;
}

static void p2_pin_clear_claim(struct p2_pin_state_s *state)
{
  state->owner = P2_PIN_OWNER_NONE;
  state->owning_cog = P2_PIN_COG_NONE;
  state->refs = 0;
  state->direction = P2_PIN_DIRECTION_DISABLED;
  state->drive = P2_PIN_DRIVE_FLOAT;
  state->event = P2_PIN_EVENT_NONE;
  state->safe = P2_PIN_SAFE_FLOAT;
  state->smartpin_mode = P2_SMARTPIN_MODE_DISABLED;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int p2_pin_reserved_role(unsigned int pin)
{
  if (pin >= P2_PIN_COUNT)
    {
      return -EINVAL;
    }

#ifdef BOARD_HAVE_PSRAM
  if (pin >= BOARD_PSRAM_FIRST_PIN && pin <= BOARD_PSRAM_LAST_PIN)
    {
      return P2_PIN_ROLE_PSRAM;
    }
#endif

  if (pin >= BOARD_FLASH_MISO_PIN && pin <= BOARD_FLASH_CS_PIN)
    {
      return P2_PIN_ROLE_STORAGE;
    }

  if (pin >= BOARD_CONSOLE_TX_PIN)
    {
      return P2_PIN_ROLE_CONSOLE;
    }

#if defined(CONFIG_ARCH_LEDS) || defined(CONFIG_USERLED)
  if (pin == BOARD_LED0_PIN || pin == BOARD_LED1_PIN)
    {
      return P2_PIN_ROLE_BOARD_LED;
    }
#endif

  return P2_PIN_ROLE_NONE;
}

int p2_pin_initialize(void)
{
  unsigned int pin;
  int lockid;

  if (g_initialized)
    {
      return 0;
    }

  lockid = p2_pin_locknew();
  if (lockid < 0)
    {
      return -ENOSPC;
    }

  g_lockid = lockid;

  for (pin = 0; pin < P2_PIN_COUNT; pin++)
    {
      g_pins[pin].pin = pin;
      g_pins[pin].reserved_role = p2_pin_reserved_role(pin);
      p2_pin_clear_claim(&g_pins[pin]);
    }

  g_initialized = true;
  return 0;
}

int p2_pin_claim(unsigned int pin, enum p2_pin_owner_e owner)
{
  struct p2_pin_state_s *state;
  enum p2_pin_owner_e reserved_owner;
  p2_pin_irqstate_t flags;
  unsigned int cog;
  int ret = 0;

  if (pin >= P2_PIN_COUNT || !p2_pin_owner_valid(owner))
    {
      return -EINVAL;
    }

  if (!g_initialized)
    {
      return -EAGAIN;
    }

  cog = p2_pin_cogid();
  flags = p2_pin_lock();
  state = &g_pins[pin];
  reserved_owner = p2_pin_role_owner(state->reserved_role);

  if (reserved_owner != P2_PIN_OWNER_NONE && reserved_owner != owner)
    {
      ret = -EBUSY;
    }
  else if (state->refs == UINT16_MAX)
    {
      ret = -EOVERFLOW;
    }
  else if (state->refs != 0 &&
           (state->owner != owner || state->owning_cog != cog))
    {
      ret = -EBUSY;
    }
  else
    {
      state->owner = owner;
      state->owning_cog = cog;
      state->refs++;
    }

  p2_pin_unlock(flags);
  return ret;
}

int p2_pin_configure(unsigned int pin, enum p2_pin_owner_e owner,
                     const struct p2_pin_config_s *config)
{
  struct p2_pin_state_s *state;
  p2_pin_irqstate_t flags;
  unsigned int cog;
  int ret = 0;

  if (pin >= P2_PIN_COUNT || !p2_pin_owner_valid(owner) ||
      !p2_pin_config_valid(config))
    {
      return -EINVAL;
    }

  if (!g_initialized)
    {
      return -EAGAIN;
    }

  cog = p2_pin_cogid();
  flags = p2_pin_lock();
  state = &g_pins[pin];

  if (state->refs == 0 || state->owner != owner ||
      state->owning_cog != cog)
    {
      ret = -EPERM;
    }
  else if (p2_pin_event_busy(pin, cog, config->event))
    {
      ret = -EBUSY;
    }
  else
    {
      state->direction = config->direction;
      state->drive = config->drive;
      state->event = config->event;
      state->safe = config->safe;
      state->smartpin_mode = config->smartpin_mode;
    }

  p2_pin_unlock(flags);
  return ret;
}

int p2_pin_release(unsigned int pin, enum p2_pin_owner_e owner)
{
  struct p2_pin_state_s *state;
  p2_pin_irqstate_t flags;
  unsigned int cog;
  int ret = 0;

  if (pin >= P2_PIN_COUNT || !p2_pin_owner_valid(owner))
    {
      return -EINVAL;
    }

  if (!g_initialized)
    {
      return -EAGAIN;
    }

  cog = p2_pin_cogid();
  flags = p2_pin_lock();
  state = &g_pins[pin];

  if (state->refs == 0 || state->owner != owner ||
      state->owning_cog != cog)
    {
      ret = -EPERM;
    }
  else
    {
      state->refs--;
      if (state->refs == 0)
        {
          p2_pin_apply_safe(pin, state->safe);
          p2_pin_clear_claim(state);
        }
    }

  p2_pin_unlock(flags);
  return ret;
}

int p2_pin_transfer_claims(enum p2_pin_owner_e owner,
                           unsigned int destination_cog,
                           unsigned int expected_claims)
{
  p2_pin_irqstate_t flags;
  unsigned int source_cog;
  unsigned int pin;
  unsigned int matching = 0;

  if (destination_cog >= P2_PIN_COG_COUNT ||
      !p2_pin_owner_valid(owner) || expected_claims == 0 ||
      expected_claims > P2_PIN_COUNT)
    {
      return -EINVAL;
    }

  if (!g_initialized)
    {
      return -EAGAIN;
    }

  source_cog = p2_pin_cogid();
  if (destination_cog == source_cog)
    {
      return -EINVAL;
    }

  flags = p2_pin_lock();
  for (pin = 0; pin < P2_PIN_COUNT; pin++)
    {
      struct p2_pin_state_s *state = &g_pins[pin];

      if (state->refs != 0 && state->owner == owner &&
          state->owning_cog == source_cog)
        {
          matching++;
        }
    }

  if (matching != expected_claims)
    {
      p2_pin_unlock(flags);
      return -ENOENT;
    }

  for (pin = 0; pin < P2_PIN_COUNT; pin++)
    {
      struct p2_pin_state_s *state = &g_pins[pin];

      if (state->refs != 0 && state->owner == owner &&
          state->owning_cog == source_cog)
        {
          state->owning_cog = destination_cog;
        }
    }

  p2_pin_unlock(flags);
  return (int)matching;
}

int p2_pin_stop_and_forget_cog(unsigned int cog,
                               enum p2_pin_owner_e owner,
                               p2_pin_safe_callback_t make_safe)
{
  p2_pin_irqstate_t flags;
  unsigned int pin;
  int released = 0;

  if (cog >= P2_PIN_COG_COUNT || !p2_pin_owner_valid(owner) ||
      make_safe == NULL)
    {
      return -EINVAL;
    }

  if (!g_initialized)
    {
      return -EAGAIN;
    }

  if (cog == p2_pin_cogid())
    {
      return -EINVAL;
    }

  /* Keep allocation, electrical safety, and metadata cleanup inside one pin
   * transaction.  A new cog may reuse the ID immediately after COGSTOP, but
   * it cannot claim these pins until their stale records have been cleared.
   */

  flags = p2_pin_lock();
#ifdef P2_PIN_MANAGER_HOST_TEST
  g_test_cog_stop_count++;
#else
  __asm__ __volatile__("cogstop %0" : : "r" (cog));
#endif
  make_safe();

  for (pin = 0; pin < P2_PIN_COUNT; pin++)
    {
      struct p2_pin_state_s *state = &g_pins[pin];

      if (state->refs != 0 && state->owner == owner &&
          state->owning_cog == cog)
        {
          p2_pin_clear_claim(state);
          released++;
        }
    }

  p2_pin_unlock(flags);
  return released;
}

int p2_pin_get_state(unsigned int pin, struct p2_pin_state_s *state)
{
  p2_pin_irqstate_t flags;

  if (pin >= P2_PIN_COUNT || state == NULL)
    {
      return -EINVAL;
    }

  if (!g_initialized)
    {
      return -EAGAIN;
    }

  flags = p2_pin_lock();
  *state = g_pins[pin];
  p2_pin_unlock(flags);
  return 0;
}

#ifdef P2_PIN_MANAGER_HOST_TEST
void p2_pin_test_reset(void)
{
  g_initialized = false;
  g_lockid = P2_PIN_LOCK_NONE;
  g_test_cog = 0;
  g_test_safe_apply_count = 0;
  g_test_cog_stop_count = 0;
}

void p2_pin_test_set_cog(unsigned int cog)
{
  g_test_cog = cog;
}

unsigned int p2_pin_test_safe_apply_count(void)
{
  return g_test_safe_apply_count;
}

unsigned int p2_pin_test_cog_stop_count(void)
{
  return g_test_cog_stop_count;
}
#endif
