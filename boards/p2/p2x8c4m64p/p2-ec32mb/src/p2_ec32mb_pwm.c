/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_pwm.c
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

#include <nuttx/timers/pwm.h>

#include "p2_ec32mb_pins.h"
#include "p2_ec32mb_smartpin.h"

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct p2_pwm_s
{
  struct pwm_lowerhalf_s lower;
  bool claimed;
  bool running;
};

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

static int p2_pwm_setup(struct pwm_lowerhalf_s *lower);
static int p2_pwm_shutdown(struct pwm_lowerhalf_s *lower);
static int p2_pwm_start(struct pwm_lowerhalf_s *lower,
                        const struct pwm_info_s *info);
static int p2_pwm_stop(struct pwm_lowerhalf_s *lower);
static int p2_pwm_ioctl(struct pwm_lowerhalf_s *lower, int cmd,
                        unsigned long arg);

/****************************************************************************
 * Private Data
 ****************************************************************************/

static const struct pwm_ops_s g_p2_pwm_ops =
{
  .setup = p2_pwm_setup,
  .shutdown = p2_pwm_shutdown,
  .start = p2_pwm_start,
  .stop = p2_pwm_stop,
  .ioctl = p2_pwm_ioctl,
};

static struct p2_pwm_s g_p2_pwm =
{
  .lower =
    {
      .ops = &g_p2_pwm_ops,
    },
};

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static int p2_pwm_setup(struct pwm_lowerhalf_s *lower)
{
  struct p2_pwm_s *priv = (struct p2_pwm_s *)lower;
  struct p2_pin_config_s config;
  int ret;

  if (priv->claimed)
    {
      return 0;
    }

  ret = p2_pin_claim(CONFIG_P2_EC32MB_PWM_PIN, P2_PIN_OWNER_PWM);
  if (ret < 0)
    {
      return ret;
    }

  config.direction = P2_PIN_DIRECTION_OUTPUT;
  config.drive = P2_PIN_DRIVE_PUSH_PULL;
  config.event = P2_PIN_EVENT_NONE;
  config.safe = P2_PIN_SAFE_FLOAT;
  config.smartpin_mode = P2_SP_PWM_SAWTOOTH;
  ret = p2_pin_configure(CONFIG_P2_EC32MB_PWM_PIN, P2_PIN_OWNER_PWM,
                         &config);
  if (ret < 0)
    {
      p2_pin_release(CONFIG_P2_EC32MB_PWM_PIN, P2_PIN_OWNER_PWM);
      return ret;
    }

  p2_sp_disable(CONFIG_P2_EC32MB_PWM_PIN);
  priv->claimed = true;
  return 0;
}

static int p2_pwm_shutdown(struct pwm_lowerhalf_s *lower)
{
  struct p2_pwm_s *priv = (struct p2_pwm_s *)lower;

  if (!priv->claimed)
    {
      return 0;
    }

  p2_pwm_stop(lower);
  priv->claimed = false;
  return p2_pin_release(CONFIG_P2_EC32MB_PWM_PIN, P2_PIN_OWNER_PWM);
}

static int p2_pwm_start(struct pwm_lowerhalf_s *lower,
                        const struct pwm_info_s *info)
{
  struct p2_pwm_s *priv = (struct p2_pwm_s *)lower;
  uint32_t base;
  uint32_t duty;
  uint32_t frame;
  uint32_t high;
  uint32_t period;
  uint32_t x;

  if (!priv->claimed || info == NULL || info->frequency == 0)
    {
      return -EINVAL;
    }

  duty = info->channels[0].duty;
  if (duty > b16ONE)
    {
      return -ERANGE;
    }

  period = CONFIG_P2_SYSCLK_HZ / info->frequency;
  if (period == 0)
    {
      return -ERANGE;
    }

  /* X[31:16] is the sawtooth frame length and X[15:0] is its clock
   * prescaler.  Select the smallest prescaler that keeps both fields in
   * range.  Y is the leading high portion of the frame.
   */

  base = (period + UINT16_MAX - 1u) / UINT16_MAX;
  if (base == 0 || base > UINT16_MAX)
    {
      return -ERANGE;
    }

  frame = (period + base / 2u) / base;
  if (frame == 0 || frame > UINT16_MAX)
    {
      return -ERANGE;
    }

  high = (frame * duty) >> 16;
  x = (frame << 16) | base;

  p2_sp_dir_low(CONFIG_P2_EC32MB_PWM_PIN);
  p2_sp_out_low(CONFIG_P2_EC32MB_PWM_PIN);
  p2_sp_wrpin(CONFIG_P2_EC32MB_PWM_PIN,
              P2_SP_PWM_SAWTOOTH | P2_SP_OE);
  p2_sp_wxpin(CONFIG_P2_EC32MB_PWM_PIN, x);
  p2_sp_wypin(CONFIG_P2_EC32MB_PWM_PIN, high);
  p2_sp_dir_high(CONFIG_P2_EC32MB_PWM_PIN);
  priv->running = true;
  return 0;
}

static int p2_pwm_stop(struct pwm_lowerhalf_s *lower)
{
  struct p2_pwm_s *priv = (struct p2_pwm_s *)lower;

  p2_sp_disable(CONFIG_P2_EC32MB_PWM_PIN);
  priv->running = false;
  return 0;
}

static int p2_pwm_ioctl(struct pwm_lowerhalf_s *lower, int cmd,
                        unsigned long arg)
{
  (void)lower;
  (void)cmd;
  (void)arg;
  return -ENOTTY;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int p2_pwm_initialize(void)
{
  return pwm_register("/dev/pwm0", &g_p2_pwm.lower);
}
