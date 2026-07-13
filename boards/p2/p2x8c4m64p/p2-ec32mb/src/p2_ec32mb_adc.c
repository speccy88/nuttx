/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_adc.c
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

#include <nuttx/analog/adc.h>
#include <nuttx/analog/ioctl.h>

#include "p2_ec32mb_pins.h"
#include "p2_ec32mb_smartpin.h"

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct p2_adc_s
{
  const struct adc_callback_s *callback;
  bool claimed;
};

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

static int p2_adc_bind(struct adc_dev_s *dev,
                       const struct adc_callback_s *callback);
static void p2_adc_reset(struct adc_dev_s *dev);
static int p2_adc_setup(struct adc_dev_s *dev);
static void p2_adc_shutdown(struct adc_dev_s *dev);
static void p2_adc_rxint(struct adc_dev_s *dev, bool enable);
static int p2_adc_ioctl(struct adc_dev_s *dev, int cmd, unsigned long arg);

/****************************************************************************
 * Private Data
 ****************************************************************************/

static const struct adc_ops_s g_p2_adc_ops =
{
  .ao_bind = p2_adc_bind,
  .ao_reset = p2_adc_reset,
  .ao_setup = p2_adc_setup,
  .ao_shutdown = p2_adc_shutdown,
  .ao_rxint = p2_adc_rxint,
  .ao_ioctl = p2_adc_ioctl,
};

static struct p2_adc_s g_p2_adc_priv;

static struct adc_dev_s g_p2_adc_dev =
{
  .ad_ops = &g_p2_adc_ops,
  .ad_priv = &g_p2_adc_priv,
};

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static int p2_adc_sample(struct adc_dev_s *dev)
{
  struct p2_adc_s *priv = dev->ad_priv;
  uint32_t deadline;
  uint32_t sample;

  if (!priv->claimed || priv->callback == NULL)
    {
      return -EINVAL;
    }

  deadline = p2_sp_counter() + CONFIG_P2_SYSCLK_HZ / 1000u;
  while (!p2_sp_ready(CONFIG_P2_EC32MB_ADC_PIN))
    {
      if ((int32_t)(p2_sp_counter() - deadline) >= 0)
        {
          return -ETIMEDOUT;
        }
    }

  sample = p2_sp_rdpin(CONFIG_P2_EC32MB_ADC_PIN);
  return priv->callback->au_receive(dev, 0, (int32_t)sample);
}

static int p2_adc_bind(struct adc_dev_s *dev,
                       const struct adc_callback_s *callback)
{
  struct p2_adc_s *priv = dev->ad_priv;

  priv->callback = callback;
  return 0;
}

static void p2_adc_reset(struct adc_dev_s *dev)
{
  struct p2_adc_s *priv = dev->ad_priv;

  if (priv->claimed)
    {
      p2_sp_disable(CONFIG_P2_EC32MB_ADC_PIN);
    }
}

static int p2_adc_setup(struct adc_dev_s *dev)
{
  struct p2_adc_s *priv = dev->ad_priv;
  struct p2_pin_config_s config;
  int ret;

  if (priv->claimed)
    {
      return 0;
    }

  ret = p2_pin_claim(CONFIG_P2_EC32MB_ADC_PIN, P2_PIN_OWNER_ADC);
  if (ret < 0)
    {
      return ret;
    }

  config.direction = P2_PIN_DIRECTION_INPUT;
  config.drive = P2_PIN_DRIVE_ANALOG;
  config.event = P2_PIN_EVENT_NONE;
  config.safe = P2_PIN_SAFE_FLOAT;
  config.smartpin_mode = P2_SP_ADC;
  ret = p2_pin_configure(CONFIG_P2_EC32MB_ADC_PIN, P2_PIN_OWNER_ADC,
                         &config);
  if (ret < 0)
    {
      p2_pin_release(CONFIG_P2_EC32MB_ADC_PIN, P2_PIN_OWNER_ADC);
      return ret;
    }

  p2_sp_dir_low(CONFIG_P2_EC32MB_ADC_PIN);
  p2_sp_wrpin(CONFIG_P2_EC32MB_ADC_PIN, P2_SP_ADC_1X | P2_SP_ADC);
  p2_sp_wxpin(CONFIG_P2_EC32MB_ADC_PIN,
              CONFIG_P2_EC32MB_ADC_SAMPLE_EXPONENT);
  p2_sp_dir_high(CONFIG_P2_EC32MB_ADC_PIN);
  priv->claimed = true;
  return 0;
}

static void p2_adc_shutdown(struct adc_dev_s *dev)
{
  struct p2_adc_s *priv = dev->ad_priv;

  if (!priv->claimed)
    {
      return;
    }

  p2_sp_disable(CONFIG_P2_EC32MB_ADC_PIN);
  p2_pin_release(CONFIG_P2_EC32MB_ADC_PIN, P2_PIN_OWNER_ADC);
  priv->claimed = false;
}

static void p2_adc_rxint(struct adc_dev_s *dev, bool enable)
{
  (void)dev;
  (void)enable;
}

static int p2_adc_ioctl(struct adc_dev_s *dev, int cmd, unsigned long arg)
{
  (void)arg;

  switch (cmd)
    {
      case ANIOC_TRIGGER:
        return p2_adc_sample(dev);

      case ANIOC_GET_NCHANNELS:
        return 1;

      default:
        return -ENOTTY;
    }
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int p2_adc_initialize(void)
{
  return adc_register("/dev/adc0", &g_p2_adc_dev);
}
