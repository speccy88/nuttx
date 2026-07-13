/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_capture.c
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

#include <errno.h>
#include <stdbool.h>
#include <stdint.h>

#include <nuttx/timers/capture.h>

#include "p2_ec32mb_pins.h"
#include "p2_ec32mb_smartpin.h"

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_CAPTURE_TIMEOUT_TICKS       CONFIG_P2_SYSCLK_HZ

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct p2_capture_s
{
  struct cap_lowerhalf_s lower;
  bool started;
  uint32_t edges;
  uint32_t last_counter;
};

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

static int p2_capture_start(struct cap_lowerhalf_s *lower);
static int p2_capture_stop(struct cap_lowerhalf_s *lower);
static int p2_capture_getduty(struct cap_lowerhalf_s *lower, uint8_t *duty);
static int p2_capture_getfreq(struct cap_lowerhalf_s *lower,
                              uint32_t *freq);
static int p2_capture_getedges(struct cap_lowerhalf_s *lower,
                               uint32_t *edges);
static int p2_capture_ioctl(struct cap_lowerhalf_s *lower, int cmd,
                            unsigned long arg);

/****************************************************************************
 * Private Data
 ****************************************************************************/

static const struct cap_ops_s g_p2_capture_ops =
{
  .start = p2_capture_start,
  .stop = p2_capture_stop,
  .getduty = p2_capture_getduty,
  .getfreq = p2_capture_getfreq,
  .getedges = p2_capture_getedges,
  .ioctl = p2_capture_ioctl,
};

static struct p2_capture_s g_p2_capture =
{
  .lower =
    {
      .ops = &g_p2_capture_ops,
    },
};

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static int p2_capture_track(uint32_t mode)
{
  struct p2_pin_config_s config;

  config.direction = P2_PIN_DIRECTION_INPUT;
  config.drive = P2_PIN_DRIVE_FLOAT;
  config.event = P2_PIN_EVENT_NONE;
  config.safe = P2_PIN_SAFE_FLOAT;
  config.smartpin_mode = mode;
  return p2_pin_configure(CONFIG_P2_EC32MB_CAPTURE_PIN,
                          P2_PIN_OWNER_CAPTURE, &config);
}

static int p2_capture_counter_start(struct p2_capture_s *priv)
{
  int ret;

  ret = p2_capture_track(P2_SP_COUNT_RISES);
  if (ret < 0)
    {
      return ret;
    }

  p2_sp_dir_low(CONFIG_P2_EC32MB_CAPTURE_PIN);
  p2_sp_wrpin(CONFIG_P2_EC32MB_CAPTURE_PIN, P2_SP_COUNT_RISES);
  p2_sp_wxpin(CONFIG_P2_EC32MB_CAPTURE_PIN, 0);
  p2_sp_wypin(CONFIG_P2_EC32MB_CAPTURE_PIN, 0);
  p2_sp_dir_high(CONFIG_P2_EC32MB_CAPTURE_PIN);
  priv->last_counter = 0;
  return 0;
}

static void p2_capture_accumulate(struct p2_capture_s *priv)
{
  uint32_t current = p2_sp_rdpin(CONFIG_P2_EC32MB_CAPTURE_PIN);

  priv->edges += current - priv->last_counter;
  priv->last_counter = current;
}

static int p2_capture_measure(struct p2_capture_s *priv, uint32_t mode,
                              uint32_t x, uint32_t *result)
{
  uint32_t deadline;
  int ret;

  if (!priv->started || result == NULL)
    {
      return -EINVAL;
    }

  p2_capture_accumulate(priv);
  p2_sp_dir_low(CONFIG_P2_EC32MB_CAPTURE_PIN);

  ret = p2_capture_track(mode);
  if (ret < 0)
    {
      p2_capture_counter_start(priv);
      return ret;
    }

  p2_sp_wrpin(CONFIG_P2_EC32MB_CAPTURE_PIN, mode);
  p2_sp_wxpin(CONFIG_P2_EC32MB_CAPTURE_PIN, x);
  p2_sp_wypin(CONFIG_P2_EC32MB_CAPTURE_PIN, 0);
  p2_sp_dir_high(CONFIG_P2_EC32MB_CAPTURE_PIN);
  p2_sp_ack(CONFIG_P2_EC32MB_CAPTURE_PIN);

  deadline = p2_sp_counter() + P2_CAPTURE_TIMEOUT_TICKS;
  while (!p2_sp_ready(CONFIG_P2_EC32MB_CAPTURE_PIN))
    {
      if ((int32_t)(p2_sp_counter() - deadline) >= 0)
        {
          p2_capture_counter_start(priv);
          return -ETIMEDOUT;
        }
    }

  *result = p2_sp_rdpin(CONFIG_P2_EC32MB_CAPTURE_PIN);
  if (mode == P2_SP_PERIODS_TICKS || mode == P2_SP_PERIODS_HIGHS)
    {
      priv->edges += x;
    }

  return p2_capture_counter_start(priv);
}

static int p2_capture_start(struct cap_lowerhalf_s *lower)
{
  struct p2_capture_s *priv = (struct p2_capture_s *)lower;
  int ret;

  if (priv->started)
    {
      return 0;
    }

  ret = p2_pin_claim(CONFIG_P2_EC32MB_CAPTURE_PIN,
                     P2_PIN_OWNER_CAPTURE);
  if (ret < 0)
    {
      return ret;
    }

  priv->edges = 0;
  priv->started = true;
  ret = p2_capture_counter_start(priv);
  if (ret < 0)
    {
      priv->started = false;
      p2_pin_release(CONFIG_P2_EC32MB_CAPTURE_PIN,
                     P2_PIN_OWNER_CAPTURE);
    }

  return ret;
}

static int p2_capture_stop(struct cap_lowerhalf_s *lower)
{
  struct p2_capture_s *priv = (struct p2_capture_s *)lower;

  if (!priv->started)
    {
      return 0;
    }

  p2_capture_accumulate(priv);
  p2_sp_disable(CONFIG_P2_EC32MB_CAPTURE_PIN);
  priv->started = false;
  return p2_pin_release(CONFIG_P2_EC32MB_CAPTURE_PIN,
                        P2_PIN_OWNER_CAPTURE);
}

static int p2_capture_getduty(struct cap_lowerhalf_s *lower, uint8_t *duty)
{
  struct p2_capture_s *priv = (struct p2_capture_s *)lower;
  uint32_t high;
  uint32_t period;
  uint64_t scaled;
  int ret;

  if (duty == NULL)
    {
      return -EINVAL;
    }

  ret = p2_capture_measure(priv, P2_SP_PERIODS_TICKS, 1, &period);
  if (ret < 0)
    {
      return ret;
    }

  ret = p2_capture_measure(priv, P2_SP_PERIODS_HIGHS, 1, &high);
  if (ret < 0)
    {
      return ret;
    }

  if (period == 0)
    {
      return -ERANGE;
    }

  scaled = (uint64_t)high * 100u + period / 2u;
  scaled /= period;
  if (scaled > 100u)
    {
      scaled = 100u;
    }

  *duty = (uint8_t)scaled;
  return 0;
}

static int p2_capture_getfreq(struct cap_lowerhalf_s *lower,
                              uint32_t *freq)
{
  struct p2_capture_s *priv = (struct p2_capture_s *)lower;
  uint32_t period;
  int ret;

  if (freq == NULL)
    {
      return -EINVAL;
    }

  ret = p2_capture_measure(priv, P2_SP_PERIODS_TICKS, 1, &period);
  if (ret < 0)
    {
      return ret;
    }

  if (period == 0)
    {
      return -ERANGE;
    }

  *freq = CONFIG_P2_SYSCLK_HZ / period;
  return 0;
}

static int p2_capture_getedges(struct cap_lowerhalf_s *lower,
                               uint32_t *edges)
{
  struct p2_capture_s *priv = (struct p2_capture_s *)lower;

  if (!priv->started || edges == NULL)
    {
      return -EINVAL;
    }

  p2_capture_accumulate(priv);
  *edges = priv->edges;
  return 0;
}

static int p2_capture_ioctl(struct cap_lowerhalf_s *lower, int cmd,
                            unsigned long arg)
{
  struct p2_capture_s *priv = (struct p2_capture_s *)lower;
  uint32_t *value = (uint32_t *)(uintptr_t)arg;

  switch (cmd)
    {
      case CAPIOC_PULSES:
        return p2_capture_getedges(lower, value);

      case CAPIOC_CLR_CNT:
        if (!priv->started)
          {
            return -EINVAL;
          }

        priv->edges = 0;
        return p2_capture_counter_start(priv);

      case CAPIOC_FILTER:
      case CAPIOC_HANDLER:
      case CAPIOC_ADD_WP:
        return -ENOSYS;

      default:
        return -ENOTTY;
    }
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int p2_capture_initialize(void)
{
  return cap_register("/dev/cap0", &g_p2_capture.lower);
}
