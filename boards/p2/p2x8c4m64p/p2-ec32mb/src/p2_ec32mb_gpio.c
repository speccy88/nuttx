/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_gpio.c
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

#include <nuttx/ioexpander/gpio.h>

#include "p2_ec32mb_pins.h"

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

/* P2 custom I/O drive fields used by the standard GPIO pin types. */

#define P2_GPIO_HIGH_15K             0x00001000
#define P2_GPIO_HIGH_FLOAT           0x00003800
#define P2_GPIO_LOW_FAST             0x00000000
#define P2_GPIO_LOW_15K              0x00000200
#define P2_GPIO_LOW_FLOAT            0x00000700

#define P2_GPIO_DEVICE_COUNT         2
#define P2_GPIO_OUTPUT_DEVICE        0
#define P2_GPIO_INPUT_DEVICE         1

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct p2_gpio_dev_s
{
  struct gpio_dev_s gpio;
  uint8_t pin;
  bool value;
  bool last;
  bool enabled;
  bool masked;
  pin_interrupt_t callback;
};

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

static int p2_gpio_read(struct gpio_dev_s *dev, bool *value);
static int p2_gpio_write(struct gpio_dev_s *dev, bool value);
static int p2_gpio_attach(struct gpio_dev_s *dev,
                          pin_interrupt_t callback);
static int p2_gpio_enable(struct gpio_dev_s *dev, bool enable);
static int p2_gpio_setpintype(struct gpio_dev_s *dev,
                              enum gpio_pintype_e pintype);
static int p2_gpio_setdebounce(struct gpio_dev_s *dev,
                               unsigned long duration);
static int p2_gpio_setmask(struct gpio_dev_s *dev, bool enable);

/****************************************************************************
 * Private Data
 ****************************************************************************/

static const struct gpio_operations_s g_p2_gpio_ops =
{
  .go_read        = p2_gpio_read,
  .go_write       = p2_gpio_write,
  .go_attach      = p2_gpio_attach,
  .go_enable      = p2_gpio_enable,
  .go_setpintype  = p2_gpio_setpintype,
  .go_setdebounce = p2_gpio_setdebounce,
  .go_setmask     = p2_gpio_setmask,
};

static struct p2_gpio_dev_s g_p2_gpio[P2_GPIO_DEVICE_COUNT] =
{
  {
    .gpio =
      {
        .gp_pintype = GPIO_OUTPUT_PIN,
        .gp_ops = &g_p2_gpio_ops,
      },
    .pin = CONFIG_P2_EC32MB_GPIO_OUT_PIN,
  },
  {
    .gpio =
      {
        .gp_pintype = GPIO_INPUT_PIN,
        .gp_ops = &g_p2_gpio_ops,
      },
    .pin = CONFIG_P2_EC32MB_GPIO_IN_PIN,
  }
};

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static inline void p2_gpio_dir_low(unsigned int pin)
{
  __asm__ __volatile__("dirl %0" : : "r" (pin));
}

static inline void p2_gpio_dir_high(unsigned int pin)
{
  __asm__ __volatile__("dirh %0" : : "r" (pin));
}

static inline void p2_gpio_out_low(unsigned int pin)
{
  __asm__ __volatile__("outl %0" : : "r" (pin));
}

static inline void p2_gpio_out_high(unsigned int pin)
{
  __asm__ __volatile__("outh %0" : : "r" (pin));
}

static inline void p2_gpio_wrpin(unsigned int pin, uint32_t mode)
{
  __asm__ __volatile__("wrpin %0, %1" : : "r" (mode), "r" (pin));
}

static inline bool p2_gpio_test(unsigned int pin)
{
  unsigned int value;

  __asm__ __volatile__("testp %1 wc\n\twrc %0"
                       : "=r" (value)
                       : "r" (pin));
  return value != 0;
}

static bool p2_gpio_is_interrupt(enum gpio_pintype_e pintype)
{
  return pintype >= GPIO_INTERRUPT_PIN &&
         pintype < GPIO_NPINTYPES;
}

static bool p2_gpio_event_matches(enum gpio_pintype_e pintype,
                                  bool previous, bool current)
{
  switch (pintype)
    {
      case GPIO_INTERRUPT_HIGH_PIN:
      case GPIO_INTERRUPT_HIGH_PIN_WAKEUP:
      case GPIO_INTERRUPT_RISING_PIN:
      case GPIO_INTERRUPT_RISING_PIN_WAKEUP:
        return !previous && current;

      case GPIO_INTERRUPT_LOW_PIN:
      case GPIO_INTERRUPT_LOW_PIN_WAKEUP:
      case GPIO_INTERRUPT_FALLING_PIN:
      case GPIO_INTERRUPT_FALLING_PIN_WAKEUP:
        return previous && !current;

      case GPIO_INTERRUPT_PIN:
      case GPIO_INTERRUPT_BOTH_PIN:
      case GPIO_INTERRUPT_PIN_WAKEUP:
      case GPIO_INTERRUPT_BOTH_PIN_WAKEUP:
        return previous != current;

      default:
        return false;
    }
}

static int p2_gpio_track(struct p2_gpio_dev_s *priv,
                         enum gpio_pintype_e pintype)
{
  struct p2_pin_config_s config;

  config.event = P2_PIN_EVENT_NONE;
  config.safe = P2_PIN_SAFE_FLOAT;
  config.smartpin_mode = P2_SMARTPIN_MODE_DISABLED;

  switch (pintype)
    {
      case GPIO_INPUT_PIN:
        config.direction = P2_PIN_DIRECTION_INPUT;
        config.drive = P2_PIN_DRIVE_FLOAT;
        break;

      case GPIO_INPUT_PIN_PULLUP:
        config.direction = P2_PIN_DIRECTION_INPUT;
        config.drive = P2_PIN_DRIVE_PULL_UP;
        break;

      case GPIO_INPUT_PIN_PULLDOWN:
        config.direction = P2_PIN_DIRECTION_INPUT;
        config.drive = P2_PIN_DRIVE_PULL_DOWN;
        break;

      case GPIO_OUTPUT_PIN:
        config.direction = P2_PIN_DIRECTION_OUTPUT;
        config.drive = P2_PIN_DRIVE_PUSH_PULL;
        break;

      case GPIO_OUTPUT_PIN_OPENDRAIN:
        config.direction = P2_PIN_DIRECTION_BIDIRECTIONAL;
        config.drive = P2_PIN_DRIVE_OPEN_DRAIN;
        break;

      case GPIO_INTERRUPT_PIN:
      case GPIO_INTERRUPT_HIGH_PIN:
      case GPIO_INTERRUPT_LOW_PIN:
      case GPIO_INTERRUPT_RISING_PIN:
      case GPIO_INTERRUPT_FALLING_PIN:
      case GPIO_INTERRUPT_BOTH_PIN:
      case GPIO_INTERRUPT_PIN_WAKEUP:
      case GPIO_INTERRUPT_HIGH_PIN_WAKEUP:
      case GPIO_INTERRUPT_LOW_PIN_WAKEUP:
      case GPIO_INTERRUPT_RISING_PIN_WAKEUP:
      case GPIO_INTERRUPT_FALLING_PIN_WAKEUP:
      case GPIO_INTERRUPT_BOTH_PIN_WAKEUP:
        config.direction = P2_PIN_DIRECTION_INPUT;
        config.drive = P2_PIN_DRIVE_FLOAT;
        config.event = priv == &g_p2_gpio[P2_GPIO_OUTPUT_DEVICE] ?
                       P2_PIN_EVENT_SE1 : P2_PIN_EVENT_SE2;
        break;

      default:
        return -ENOSYS;
    }

  return p2_pin_configure(priv->pin, P2_PIN_OWNER_GPIO, &config);
}

static void p2_gpio_apply(struct p2_gpio_dev_s *priv,
                          enum gpio_pintype_e pintype)
{
  uint32_t mode = 0;
  bool drive = false;
  bool high = priv->value;

  p2_gpio_dir_low(priv->pin);

  switch (pintype)
    {
      case GPIO_INPUT_PIN_PULLUP:
        mode = P2_GPIO_HIGH_15K | P2_GPIO_LOW_FLOAT;
        drive = true;
        high = true;
        break;

      case GPIO_INPUT_PIN_PULLDOWN:
        mode = P2_GPIO_HIGH_FLOAT | P2_GPIO_LOW_15K;
        drive = true;
        high = false;
        break;

      case GPIO_OUTPUT_PIN:
        drive = true;
        break;

      case GPIO_OUTPUT_PIN_OPENDRAIN:
        mode = P2_GPIO_HIGH_FLOAT | P2_GPIO_LOW_FAST;
        drive = true;
        break;

      case GPIO_INPUT_PIN:
      case GPIO_INTERRUPT_PIN:
      case GPIO_INTERRUPT_HIGH_PIN:
      case GPIO_INTERRUPT_LOW_PIN:
      case GPIO_INTERRUPT_RISING_PIN:
      case GPIO_INTERRUPT_FALLING_PIN:
      case GPIO_INTERRUPT_BOTH_PIN:
      case GPIO_INTERRUPT_PIN_WAKEUP:
      case GPIO_INTERRUPT_HIGH_PIN_WAKEUP:
      case GPIO_INTERRUPT_LOW_PIN_WAKEUP:
      case GPIO_INTERRUPT_RISING_PIN_WAKEUP:
      case GPIO_INTERRUPT_FALLING_PIN_WAKEUP:
      case GPIO_INTERRUPT_BOTH_PIN_WAKEUP:
      default:
        break;
    }

  p2_gpio_wrpin(priv->pin, mode);

  if (high)
    {
      p2_gpio_out_high(priv->pin);
    }
  else
    {
      p2_gpio_out_low(priv->pin);
    }

  if (drive)
    {
      p2_gpio_dir_high(priv->pin);
    }
}

static int p2_gpio_read(struct gpio_dev_s *dev, bool *value)
{
  struct p2_gpio_dev_s *priv = (struct p2_gpio_dev_s *)dev;

  if (priv == NULL || value == NULL)
    {
      return -EINVAL;
    }

  *value = p2_gpio_test(priv->pin);
  return 0;
}

static int p2_gpio_write(struct gpio_dev_s *dev, bool value)
{
  struct p2_gpio_dev_s *priv = (struct p2_gpio_dev_s *)dev;

  if (priv == NULL)
    {
      return -EINVAL;
    }

  if (priv->gpio.gp_pintype != GPIO_OUTPUT_PIN &&
      priv->gpio.gp_pintype != GPIO_OUTPUT_PIN_OPENDRAIN)
    {
      return -EPERM;
    }

  priv->value = value;
  if (value)
    {
      p2_gpio_out_high(priv->pin);
    }
  else
    {
      p2_gpio_out_low(priv->pin);
    }

  return 0;
}

static int p2_gpio_attach(struct gpio_dev_s *dev,
                          pin_interrupt_t callback)
{
  struct p2_gpio_dev_s *priv = (struct p2_gpio_dev_s *)dev;
  irqstate_t flags;

  if (priv == NULL || !p2_gpio_is_interrupt(priv->gpio.gp_pintype))
    {
      return -EINVAL;
    }

  flags = enter_critical_section();
  priv->callback = callback;
  if (callback == NULL)
    {
      priv->enabled = false;
    }

  leave_critical_section(flags);
  return 0;
}

static int p2_gpio_enable(struct gpio_dev_s *dev, bool enable)
{
  struct p2_gpio_dev_s *priv = (struct p2_gpio_dev_s *)dev;
  irqstate_t flags;

  if (priv == NULL || !p2_gpio_is_interrupt(priv->gpio.gp_pintype))
    {
      return -EINVAL;
    }

  if (enable && priv->callback == NULL)
    {
      return -EINVAL;
    }

  flags = enter_critical_section();
  priv->last = p2_gpio_test(priv->pin);
  priv->enabled = enable;
  leave_critical_section(flags);
  return 0;
}

static int p2_gpio_setpintype(struct gpio_dev_s *dev,
                              enum gpio_pintype_e pintype)
{
  struct p2_gpio_dev_s *priv = (struct p2_gpio_dev_s *)dev;
  int ret;

  if (priv == NULL || pintype >= GPIO_NPINTYPES)
    {
      return -EINVAL;
    }

  ret = p2_gpio_track(priv, pintype);
  if (ret < 0)
    {
      return ret;
    }

  priv->enabled = false;
  priv->masked = false;
  p2_gpio_apply(priv, pintype);
  priv->last = p2_gpio_test(priv->pin);
  priv->gpio.gp_pintype = pintype;
  return 0;
}

static int p2_gpio_setdebounce(struct gpio_dev_s *dev,
                               unsigned long duration)
{
  (void)dev;
  (void)duration;
  return -ENOSYS;
}

static int p2_gpio_setmask(struct gpio_dev_s *dev, bool enable)
{
  struct p2_gpio_dev_s *priv = (struct p2_gpio_dev_s *)dev;
  irqstate_t flags;

  if (priv == NULL || !p2_gpio_is_interrupt(priv->gpio.gp_pintype))
    {
      return -EINVAL;
    }

  flags = enter_critical_section();
  priv->masked = enable;
  if (!enable)
    {
      priv->last = p2_gpio_test(priv->pin);
    }

  leave_critical_section(flags);
  return 0;
}

static int p2_gpio_register(struct p2_gpio_dev_s *priv)
{
  int ret;

  ret = p2_pin_claim(priv->pin, P2_PIN_OWNER_GPIO);
  if (ret < 0)
    {
      return ret;
    }

  ret = p2_gpio_setpintype(&priv->gpio, priv->gpio.gp_pintype);
  if (ret < 0)
    {
      p2_pin_release(priv->pin, P2_PIN_OWNER_GPIO);
      return ret;
    }

  ret = gpio_pin_register(&priv->gpio, priv->pin);
  if (ret < 0)
    {
      p2_pin_release(priv->pin, P2_PIN_OWNER_GPIO);
    }

  return ret;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int p2_gpio_initialize(void)
{
  int ret;

  if (CONFIG_P2_EC32MB_GPIO_OUT_PIN == CONFIG_P2_EC32MB_GPIO_IN_PIN)
    {
      return -EINVAL;
    }

  ret = p2_pin_initialize();
  if (ret < 0)
    {
      return ret;
    }

  ret = p2_gpio_register(&g_p2_gpio[P2_GPIO_OUTPUT_DEVICE]);
  if (ret < 0)
    {
      return ret;
    }

  ret = p2_gpio_register(&g_p2_gpio[P2_GPIO_INPUT_DEVICE]);
  if (ret < 0)
    {
      gpio_pin_unregister(&g_p2_gpio[P2_GPIO_OUTPUT_DEVICE].gpio,
                          g_p2_gpio[P2_GPIO_OUTPUT_DEVICE].pin);
      p2_pin_release(g_p2_gpio[P2_GPIO_OUTPUT_DEVICE].pin,
                     P2_PIN_OWNER_GPIO);
    }

  return ret;
}

/* CONFIG_SYSTEMTICK_HOOK supplies a bounded fallback event route while CT1
 * owns the architecture's only context-saving interrupt channel.  Inputs
 * are sampled once per system tick; the callback is a real observation of
 * the pin level and never a fabricated success notification.
 */

void p2_gpio_poll(void)
{
  struct p2_gpio_dev_s *priv;
  pin_interrupt_t callback;
  bool current;
  bool notify;
  unsigned int index;

  for (index = 0; index < P2_GPIO_DEVICE_COUNT; index++)
    {
      priv = &g_p2_gpio[index];
      if (!priv->enabled || priv->masked || priv->callback == NULL ||
          !p2_gpio_is_interrupt(priv->gpio.gp_pintype))
        {
          continue;
        }

      current = p2_gpio_test(priv->pin);
      notify = p2_gpio_event_matches(priv->gpio.gp_pintype,
                                     priv->last, current);
      priv->last = current;

      if (notify)
        {
          callback = priv->callback;
          callback(&priv->gpio, priv->pin);
        }
    }
}
