/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_dac.c
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

#include <nuttx/analog/dac.h>

#include "p2_ec32mb_pins.h"
#include "p2_ec32mb_smartpin.h"

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct p2_dac_s
{
  bool claimed;
};

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

static void p2_dac_reset(struct dac_dev_s *dev);
static int p2_dac_setup(struct dac_dev_s *dev);
static void p2_dac_shutdown(struct dac_dev_s *dev);
static void p2_dac_txint(struct dac_dev_s *dev, bool enable);
static int p2_dac_send(struct dac_dev_s *dev, struct dac_msg_s *msg);
static int p2_dac_ioctl(struct dac_dev_s *dev, int cmd, unsigned long arg);

/****************************************************************************
 * Private Data
 ****************************************************************************/

static const struct dac_ops_s g_p2_dac_ops =
{
  .ao_reset = p2_dac_reset,
  .ao_setup = p2_dac_setup,
  .ao_shutdown = p2_dac_shutdown,
  .ao_txint = p2_dac_txint,
  .ao_send = p2_dac_send,
  .ao_ioctl = p2_dac_ioctl,
};

static struct p2_dac_s g_p2_dac_priv;

static struct dac_dev_s g_p2_dac_dev =
{
  .ad_ops = &g_p2_dac_ops,
  .ad_priv = &g_p2_dac_priv,
  .ad_nchannel = 1,
};

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static void p2_dac_reset(struct dac_dev_s *dev)
{
  struct p2_dac_s *priv = dev->ad_priv;

  if (priv->claimed)
    {
      p2_sp_disable(CONFIG_P2_EC32MB_DAC_PIN);
    }
}

static int p2_dac_setup(struct dac_dev_s *dev)
{
  struct p2_dac_s *priv = dev->ad_priv;
  struct p2_pin_config_s config;
  int ret;

  if (priv->claimed)
    {
      return 0;
    }

  ret = p2_pin_claim(CONFIG_P2_EC32MB_DAC_PIN, P2_PIN_OWNER_DAC);
  if (ret < 0)
    {
      return ret;
    }

  config.direction = P2_PIN_DIRECTION_OUTPUT;
  config.drive = P2_PIN_DRIVE_ANALOG;
  config.event = P2_PIN_EVENT_NONE;
  config.safe = P2_PIN_SAFE_FLOAT;
  config.smartpin_mode = P2_SP_DAC_DITHER_PWM;
  ret = p2_pin_configure(CONFIG_P2_EC32MB_DAC_PIN, P2_PIN_OWNER_DAC,
                         &config);
  if (ret < 0)
    {
      p2_pin_release(CONFIG_P2_EC32MB_DAC_PIN, P2_PIN_OWNER_DAC);
      return ret;
    }

  p2_sp_dir_low(CONFIG_P2_EC32MB_DAC_PIN);
  p2_sp_wrpin(CONFIG_P2_EC32MB_DAC_PIN,
              P2_SP_DAC_990R_3V | P2_SP_OE | P2_SP_DAC_DITHER_PWM);
  p2_sp_wxpin(CONFIG_P2_EC32MB_DAC_PIN, 256);
  p2_sp_wypin(CONFIG_P2_EC32MB_DAC_PIN, 0);
  p2_sp_dir_high(CONFIG_P2_EC32MB_DAC_PIN);
  priv->claimed = true;
  return 0;
}

static void p2_dac_shutdown(struct dac_dev_s *dev)
{
  struct p2_dac_s *priv = dev->ad_priv;

  if (!priv->claimed)
    {
      return;
    }

  p2_sp_disable(CONFIG_P2_EC32MB_DAC_PIN);
  p2_pin_release(CONFIG_P2_EC32MB_DAC_PIN, P2_PIN_OWNER_DAC);
  priv->claimed = false;
}

static void p2_dac_txint(struct dac_dev_s *dev, bool enable)
{
  (void)dev;
  (void)enable;
}

static int p2_dac_send(struct dac_dev_s *dev, struct dac_msg_s *msg)
{
  struct p2_dac_s *priv = dev->ad_priv;
  uint32_t value;

  if (!priv->claimed || msg == NULL || msg->am_channel != 0)
    {
      return -EINVAL;
    }

  /* The generic DAC upper half left-justifies 1- and 2-byte writes. */

  value = (uint32_t)msg->am_data;
  if (value > UINT16_MAX)
    {
      value >>= 16;
    }

  p2_sp_wypin(CONFIG_P2_EC32MB_DAC_PIN, value);
  return dac_txdone(dev);
}

static int p2_dac_ioctl(struct dac_dev_s *dev, int cmd, unsigned long arg)
{
  (void)dev;
  (void)cmd;
  (void)arg;
  return -ENOTTY;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int p2_dac_initialize(void)
{
  return dac_register("/dev/dac0", &g_p2_dac_dev);
}
