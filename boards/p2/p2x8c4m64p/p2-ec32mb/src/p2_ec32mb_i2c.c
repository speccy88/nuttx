/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_i2c.c
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
#include <syslog.h>

#include <nuttx/arch.h>
#include <nuttx/i2c/i2c_bitbang.h>
#include <nuttx/i2c/i2c_master.h>

#ifdef CONFIG_P2_EC32MB_BMP180
#  include <nuttx/sensors/bmp180.h>
#endif

#include "p2_ec32mb_pins.h"

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

/* Float a high output and drive a low output.  OUT selects which half of
 * this custom pad mode is active while DIR remains enabled.
 */

#define P2_I2C_HIGH_FLOAT            0x00003800u
#define P2_I2C_LOW_FAST              0x00000000u
#define P2_I2C_PAD_MODE              (P2_I2C_HIGH_FLOAT | P2_I2C_LOW_FAST)
#define P2_I2C_RECOVERY_CLOCKS       9
#define P2_I2C_HALF_PERIOD_USEC      5
#define P2_I2C_RELEASE_TIMEOUT_USEC  1000

#if CONFIG_P2_EC32MB_I2C_SDA_PIN == CONFIG_P2_EC32MB_I2C_SCL_PIN
#  error "P2 I2C SDA and SCL pins must be distinct"
#endif

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

static void p2_i2c_lower_initialize(
  FAR struct i2c_bitbang_lower_dev_s *lower);
static void p2_i2c_set_scl(FAR struct i2c_bitbang_lower_dev_s *lower,
                           bool high);
static void p2_i2c_set_sda(FAR struct i2c_bitbang_lower_dev_s *lower,
                           bool high);
static bool p2_i2c_get_scl(FAR struct i2c_bitbang_lower_dev_s *lower);
static bool p2_i2c_get_sda(FAR struct i2c_bitbang_lower_dev_s *lower);

/****************************************************************************
 * Private Data
 ****************************************************************************/

static const struct i2c_bitbang_lower_ops_s g_p2_i2c_ops =
{
  .initialize = p2_i2c_lower_initialize,
  .set_scl = p2_i2c_set_scl,
  .set_sda = p2_i2c_set_sda,
  .get_scl = p2_i2c_get_scl,
  .get_sda = p2_i2c_get_sda,
};

static struct i2c_bitbang_lower_dev_s g_p2_i2c_lower =
{
  .ops = &g_p2_i2c_ops,
};

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static inline void p2_i2c_dir_low(unsigned int pin)
{
  __asm__ __volatile__("dirl %0" : : "r" (pin));
}

static inline void p2_i2c_dir_high(unsigned int pin)
{
  __asm__ __volatile__("dirh %0" : : "r" (pin));
}

static inline void p2_i2c_out_low(unsigned int pin)
{
  __asm__ __volatile__("outl %0" : : "r" (pin));
}

static inline void p2_i2c_out_high(unsigned int pin)
{
  __asm__ __volatile__("outh %0" : : "r" (pin));
}

static inline void p2_i2c_wrpin(unsigned int pin, uint32_t mode)
{
  __asm__ __volatile__("wrpin %0, %1" : : "r" (mode), "r" (pin));
}

static inline bool p2_i2c_test(unsigned int pin)
{
  unsigned int value;

  __asm__ __volatile__("testp %1 wc\n\twrc %0"
                       : "=r" (value)
                       : "r" (pin));
  return value != 0;
}

static void p2_i2c_apply(unsigned int pin)
{
  p2_i2c_dir_low(pin);
  p2_i2c_wrpin(pin, P2_I2C_PAD_MODE);
  p2_i2c_out_high(pin);
  p2_i2c_dir_high(pin);
}

static int p2_i2c_configure(unsigned int pin)
{
  struct p2_pin_config_s config;

  config.direction = P2_PIN_DIRECTION_BIDIRECTIONAL;
  config.drive = P2_PIN_DRIVE_OPEN_DRAIN;
  config.event = P2_PIN_EVENT_NONE;
  config.safe = P2_PIN_SAFE_FLOAT;
  config.smartpin_mode = P2_SMARTPIN_MODE_DISABLED;
  return p2_pin_configure(pin, P2_PIN_OWNER_I2C, &config);
}

static int p2_i2c_wait_high(unsigned int pin)
{
  unsigned int elapsed;

  for (elapsed = 0; elapsed < P2_I2C_RELEASE_TIMEOUT_USEC; elapsed++)
    {
      if (p2_i2c_test(pin))
        {
          return 0;
        }

      up_udelay(1);
    }

  return -ETIMEDOUT;
}

static int p2_i2c_recover(void)
{
  unsigned int pulses = 0;
  int ret;

  p2_i2c_out_high(CONFIG_P2_EC32MB_I2C_SDA_PIN);
  p2_i2c_out_high(CONFIG_P2_EC32MB_I2C_SCL_PIN);

  ret = p2_i2c_wait_high(CONFIG_P2_EC32MB_I2C_SCL_PIN);
  if (ret < 0)
    {
      return ret;
    }

  while (!p2_i2c_test(CONFIG_P2_EC32MB_I2C_SDA_PIN) &&
         pulses < P2_I2C_RECOVERY_CLOCKS)
    {
      p2_i2c_out_low(CONFIG_P2_EC32MB_I2C_SCL_PIN);
      up_udelay(P2_I2C_HALF_PERIOD_USEC);
      p2_i2c_out_high(CONFIG_P2_EC32MB_I2C_SCL_PIN);

      ret = p2_i2c_wait_high(CONFIG_P2_EC32MB_I2C_SCL_PIN);
      if (ret < 0)
        {
          return ret;
        }

      up_udelay(P2_I2C_HALF_PERIOD_USEC);
      pulses++;
    }

  /* Emit a STOP from a known state, even when no recovery clocks were
   * required.  Both lines must then read high through the external pull-ups.
   */

  p2_i2c_out_low(CONFIG_P2_EC32MB_I2C_SDA_PIN);
  up_udelay(P2_I2C_HALF_PERIOD_USEC);
  p2_i2c_out_high(CONFIG_P2_EC32MB_I2C_SCL_PIN);

  ret = p2_i2c_wait_high(CONFIG_P2_EC32MB_I2C_SCL_PIN);
  if (ret < 0)
    {
      return ret;
    }

  up_udelay(P2_I2C_HALF_PERIOD_USEC);
  p2_i2c_out_high(CONFIG_P2_EC32MB_I2C_SDA_PIN);
  up_udelay(P2_I2C_HALF_PERIOD_USEC);

  if (!p2_i2c_test(CONFIG_P2_EC32MB_I2C_SDA_PIN))
    {
      return -EBUSY;
    }

  syslog(LOG_NOTICE,
         "P2I2C:BUS_RECOVERY=PASS:SDA=%u:SCL=%u:PULSES=%u\n",
         CONFIG_P2_EC32MB_I2C_SDA_PIN,
         CONFIG_P2_EC32MB_I2C_SCL_PIN, pulses);
  return 0;
}

static void p2_i2c_lower_initialize(
  FAR struct i2c_bitbang_lower_dev_s *lower)
{
  (void)lower;
  p2_i2c_apply(CONFIG_P2_EC32MB_I2C_SDA_PIN);
  p2_i2c_apply(CONFIG_P2_EC32MB_I2C_SCL_PIN);
}

static void p2_i2c_set_scl(FAR struct i2c_bitbang_lower_dev_s *lower,
                           bool high)
{
  (void)lower;
  if (high)
    {
      p2_i2c_out_high(CONFIG_P2_EC32MB_I2C_SCL_PIN);
    }
  else
    {
      p2_i2c_out_low(CONFIG_P2_EC32MB_I2C_SCL_PIN);
    }
}

static void p2_i2c_set_sda(FAR struct i2c_bitbang_lower_dev_s *lower,
                           bool high)
{
  (void)lower;
  if (high)
    {
      p2_i2c_out_high(CONFIG_P2_EC32MB_I2C_SDA_PIN);
    }
  else
    {
      p2_i2c_out_low(CONFIG_P2_EC32MB_I2C_SDA_PIN);
    }
}

static bool p2_i2c_get_scl(FAR struct i2c_bitbang_lower_dev_s *lower)
{
  (void)lower;
  return p2_i2c_test(CONFIG_P2_EC32MB_I2C_SCL_PIN);
}

static bool p2_i2c_get_sda(FAR struct i2c_bitbang_lower_dev_s *lower)
{
  (void)lower;
  return p2_i2c_test(CONFIG_P2_EC32MB_I2C_SDA_PIN);
}

static void p2_i2c_release_pins(void)
{
  p2_pin_release(CONFIG_P2_EC32MB_I2C_SCL_PIN, P2_PIN_OWNER_I2C);
  p2_pin_release(CONFIG_P2_EC32MB_I2C_SDA_PIN, P2_PIN_OWNER_I2C);
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int p2_i2c_initialize(void)
{
  FAR struct i2c_master_s *i2c;
  int ret;

  ret = p2_pin_initialize();
  if (ret < 0)
    {
      return ret;
    }

  ret = p2_pin_claim(CONFIG_P2_EC32MB_I2C_SDA_PIN, P2_PIN_OWNER_I2C);
  if (ret < 0)
    {
      return ret;
    }

  ret = p2_pin_claim(CONFIG_P2_EC32MB_I2C_SCL_PIN, P2_PIN_OWNER_I2C);
  if (ret < 0)
    {
      p2_pin_release(CONFIG_P2_EC32MB_I2C_SDA_PIN, P2_PIN_OWNER_I2C);
      return ret;
    }

  ret = p2_i2c_configure(CONFIG_P2_EC32MB_I2C_SDA_PIN);
  if (ret < 0)
    {
      goto fail;
    }

  ret = p2_i2c_configure(CONFIG_P2_EC32MB_I2C_SCL_PIN);
  if (ret < 0)
    {
      goto fail;
    }

  p2_i2c_lower_initialize(&g_p2_i2c_lower);
  ret = p2_i2c_recover();
  if (ret < 0)
    {
      goto fail;
    }

  i2c = i2c_bitbang_initialize(&g_p2_i2c_lower);
  if (i2c == NULL)
    {
      ret = -ENOMEM;
      goto fail;
    }

#ifdef CONFIG_I2C_DRIVER
  ret = i2c_register(i2c, 0);
  if (ret < 0)
    {
      return ret;
    }

  syslog(LOG_NOTICE,
         "P2I2C:BUS=PASS:DEV=/dev/i2c0:SDA=%u:SCL=%u:OPEN_DRAIN=YES\n",
         CONFIG_P2_EC32MB_I2C_SDA_PIN,
         CONFIG_P2_EC32MB_I2C_SCL_PIN);
#endif

#ifdef CONFIG_P2_EC32MB_BMP180
  ret = bmp180_register("/dev/press0", i2c);
  if (ret < 0)
    {
      return ret;
    }

  syslog(LOG_NOTICE,
         "P2I2C:BMP180=PASS:DEV=/dev/press0:ADDR=0x77:ID=0x55\n");
#endif

  return 0;

fail:
  p2_i2c_release_pins();
  return ret;
}
