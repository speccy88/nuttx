/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_spi.c
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

#include <nuttx/spi/spi.h>
#include <nuttx/spi/spi_bitbang.h>
#include <nuttx/spi/spi_transfer.h>

#include "p2_ec32mb_pins.h"
#include "p2_ec32mb_smartpin.h"

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_SPI_CLAIM_MISO          (1u << 0)
#define P2_SPI_CLAIM_MOSI          (1u << 1)
#define P2_SPI_CLAIM_SCK           (1u << 2)
#define P2_SPI_CLAIM_CS            (1u << 3)
#define P2_SPI_CLAIM_TOUCH_CS      (1u << 4)

#if CONFIG_P2_EC32MB_SPI_MOSI_PIN == CONFIG_P2_EC32MB_SPI_MISO_PIN || \
    CONFIG_P2_EC32MB_SPI_MOSI_PIN == CONFIG_P2_EC32MB_SPI_SCK_PIN || \
    CONFIG_P2_EC32MB_SPI_MOSI_PIN == CONFIG_P2_EC32MB_SPI_CS_PIN || \
    CONFIG_P2_EC32MB_SPI_MISO_PIN == CONFIG_P2_EC32MB_SPI_SCK_PIN || \
    CONFIG_P2_EC32MB_SPI_MISO_PIN == CONFIG_P2_EC32MB_SPI_CS_PIN || \
    CONFIG_P2_EC32MB_SPI_SCK_PIN == CONFIG_P2_EC32MB_SPI_CS_PIN
#  error "P2 general-purpose SPI pins must be distinct"
#endif

#ifdef CONFIG_P2_EC32MB_ILI9341_XPT2046
#  if CONFIG_P2_EC32MB_XPT2046_CS_PIN == CONFIG_P2_EC32MB_SPI_MOSI_PIN || \
      CONFIG_P2_EC32MB_XPT2046_CS_PIN == CONFIG_P2_EC32MB_SPI_MISO_PIN || \
      CONFIG_P2_EC32MB_XPT2046_CS_PIN == CONFIG_P2_EC32MB_SPI_SCK_PIN || \
      CONFIG_P2_EC32MB_XPT2046_CS_PIN == CONFIG_P2_EC32MB_SPI_CS_PIN
#    error "P2 XPT2046 chip select must be distinct from the SPI pins"
#  endif
#endif

#if CONFIG_P2_EC32MB_SPI_MAX_FREQUENCY <= 0
#  error "P2 general-purpose SPI maximum frequency must be positive"
#endif

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct p2_spi_lower_s
{
  enum spi_mode_e mode;
  uint32_t frequency;
  uint32_t half_cycles;
  uint8_t claims;
  bool mode_valid;
  bool outputs_enabled;
  bool selected;
  bool faulted;
};

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

static void p2_spi_select(FAR struct spi_bitbang_s *dev, uint32_t devid,
                          bool selected);
static uint32_t p2_spi_setfrequency(FAR struct spi_bitbang_s *dev,
                                    uint32_t frequency);
static void p2_spi_setmode(FAR struct spi_bitbang_s *dev,
                           enum spi_mode_e mode);
static uint16_t p2_spi_exchange(FAR struct spi_bitbang_s *dev,
                                uint16_t dataout);
static uint8_t p2_spi_status(FAR struct spi_bitbang_s *dev,
                             uint32_t devid);
#ifdef CONFIG_SPI_CMDDATA
static int p2_spi_cmddata(FAR struct spi_bitbang_s *dev, uint32_t devid,
                          bool cmd);
#endif

/****************************************************************************
 * Private Data
 ****************************************************************************/

static const struct spi_bitbang_ops_s g_p2_spi_ops =
{
  .select       = p2_spi_select,
  .setfrequency = p2_spi_setfrequency,
  .setmode      = p2_spi_setmode,
  .exchange     = p2_spi_exchange,
  .status       = p2_spi_status,
#ifdef CONFIG_SPI_CMDDATA
  .cmddata      = p2_spi_cmddata,
#endif
};

static struct p2_spi_lower_s g_p2_spi_lower =
{
  .mode = SPIDEV_MODE0,
  .frequency = CONFIG_P2_EC32MB_SPI_MAX_FREQUENCY,
  .half_cycles = 1,
  .mode_valid = true,
};

static FAR struct spi_dev_s *g_p2_spi;

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static FAR struct p2_spi_lower_s *p2_spi_lower(
  FAR struct spi_bitbang_s *dev)
{
  return (FAR struct p2_spi_lower_s *)dev->priv;
}

static void p2_spi_pin_write(unsigned int pin, bool high)
{
  if (high)
    {
      p2_sp_out_high(pin);
    }
  else
    {
      p2_sp_out_low(pin);
    }
}

static bool p2_spi_device_supported(uint32_t devid)
{
  if (devid == SPIDEV_USER(0))
    {
      return true;
    }

#ifdef CONFIG_P2_EC32MB_ILI9341_XPT2046
  return devid == SPIDEV_DISPLAY(0) || devid == SPIDEV_TOUCHSCREEN(0);
#else
  return false;
#endif
}

static bool p2_spi_pin_read(unsigned int pin)
{
  unsigned int value;

  __asm__ __volatile__("testp %1 wc\n\twrc %0"
                       : "=r" (value)
                       : "r" (pin));
  return value != 0;
}

static void p2_spi_half_delay(FAR struct p2_spi_lower_s *priv)
{
  __asm__ __volatile__("waitx %0" : : "r" (priv->half_cycles));
}

static int p2_spi_claim(FAR struct p2_spi_lower_s *priv,
                        unsigned int pin, uint8_t mask)
{
  int ret;

  ret = p2_pin_claim(pin, P2_PIN_OWNER_SPI);
  if (ret == 0)
    {
      priv->claims |= mask;
    }

  return ret;
}

static void p2_spi_release(FAR struct p2_spi_lower_s *priv)
{
  if (priv->outputs_enabled)
    {
      /* Deassert every chip select before disabling the shared sources.
       * In the display profile the pin manager restores chip selects high;
       * the loopback profile retains its historical floating safe state.
       */

      p2_sp_out_high(CONFIG_P2_EC32MB_SPI_CS_PIN);
#ifdef CONFIG_P2_EC32MB_ILI9341_XPT2046
      p2_sp_out_high(CONFIG_P2_EC32MB_XPT2046_CS_PIN);
#endif
      p2_sp_dir_low(CONFIG_P2_EC32MB_SPI_MOSI_PIN);
      p2_sp_dir_low(CONFIG_P2_EC32MB_SPI_SCK_PIN);
      p2_sp_dir_low(CONFIG_P2_EC32MB_SPI_CS_PIN);
#ifdef CONFIG_P2_EC32MB_ILI9341_XPT2046
      p2_sp_dir_low(CONFIG_P2_EC32MB_XPT2046_CS_PIN);
#endif
      priv->outputs_enabled = false;
    }

  if ((priv->claims & P2_SPI_CLAIM_MOSI) != 0)
    {
      p2_pin_release(CONFIG_P2_EC32MB_SPI_MOSI_PIN,
                     P2_PIN_OWNER_SPI);
    }

  if ((priv->claims & P2_SPI_CLAIM_SCK) != 0)
    {
      p2_pin_release(CONFIG_P2_EC32MB_SPI_SCK_PIN,
                     P2_PIN_OWNER_SPI);
    }

  if ((priv->claims & P2_SPI_CLAIM_CS) != 0)
    {
      p2_pin_release(CONFIG_P2_EC32MB_SPI_CS_PIN,
                     P2_PIN_OWNER_SPI);
    }

#ifdef CONFIG_P2_EC32MB_ILI9341_XPT2046
  if ((priv->claims & P2_SPI_CLAIM_TOUCH_CS) != 0)
    {
      p2_pin_release(CONFIG_P2_EC32MB_XPT2046_CS_PIN,
                     P2_PIN_OWNER_SPI);
    }
#endif

  if ((priv->claims & P2_SPI_CLAIM_MISO) != 0)
    {
      p2_pin_release(CONFIG_P2_EC32MB_SPI_MISO_PIN,
                     P2_PIN_OWNER_SPI);
    }

  priv->claims = 0;
  priv->selected = false;
}

static int p2_spi_activate(FAR struct p2_spi_lower_s *priv)
{
  static const struct p2_pin_config_s input =
  {
    .direction = P2_PIN_DIRECTION_INPUT,
    .drive = P2_PIN_DRIVE_FLOAT,
    .event = P2_PIN_EVENT_NONE,
    .safe = P2_PIN_SAFE_FLOAT,
    .smartpin_mode = P2_SMARTPIN_MODE_DISABLED,
  };

  static const struct p2_pin_config_s output =
  {
    .direction = P2_PIN_DIRECTION_OUTPUT,
    .drive = P2_PIN_DRIVE_PUSH_PULL,
    .event = P2_PIN_EVENT_NONE,
    .safe = P2_PIN_SAFE_FLOAT,
    .smartpin_mode = P2_SMARTPIN_MODE_DISABLED,
  };

#ifdef CONFIG_P2_EC32MB_ILI9341_XPT2046
  static const struct p2_pin_config_s chip_select =
  {
    .direction = P2_PIN_DIRECTION_OUTPUT,
    .drive = P2_PIN_DRIVE_PUSH_PULL,
    .event = P2_PIN_EVENT_NONE,
    .safe = P2_PIN_SAFE_HIGH,
    .smartpin_mode = P2_SMARTPIN_MODE_DISABLED,
  };
#endif

  bool clock_high;
  int ret;

  /* Claims do not drive a pin.  Claim and configure the receiver before
   * the source, then prepare the clock and all chip selects.
   */

  ret = p2_spi_claim(priv, CONFIG_P2_EC32MB_SPI_MISO_PIN,
                     P2_SPI_CLAIM_MISO);
  if (ret < 0)
    {
      goto fail;
    }

  ret = p2_spi_claim(priv, CONFIG_P2_EC32MB_SPI_MOSI_PIN,
                     P2_SPI_CLAIM_MOSI);
  if (ret < 0)
    {
      goto fail;
    }

  ret = p2_spi_claim(priv, CONFIG_P2_EC32MB_SPI_SCK_PIN,
                     P2_SPI_CLAIM_SCK);
  if (ret < 0)
    {
      goto fail;
    }

  ret = p2_spi_claim(priv, CONFIG_P2_EC32MB_SPI_CS_PIN,
                     P2_SPI_CLAIM_CS);
  if (ret < 0)
    {
      goto fail;
    }

#ifdef CONFIG_P2_EC32MB_ILI9341_XPT2046
  ret = p2_spi_claim(priv, CONFIG_P2_EC32MB_XPT2046_CS_PIN,
                     P2_SPI_CLAIM_TOUCH_CS);
  if (ret < 0)
    {
      goto fail;
    }
#endif

  ret = p2_pin_configure(CONFIG_P2_EC32MB_SPI_MISO_PIN,
                         P2_PIN_OWNER_SPI, &input);
  if (ret < 0)
    {
      goto fail;
    }

  ret = p2_pin_configure(CONFIG_P2_EC32MB_SPI_MOSI_PIN,
                         P2_PIN_OWNER_SPI, &output);
  if (ret < 0)
    {
      goto fail;
    }

  ret = p2_pin_configure(CONFIG_P2_EC32MB_SPI_SCK_PIN,
                         P2_PIN_OWNER_SPI, &output);
  if (ret < 0)
    {
      goto fail;
    }

  ret = p2_pin_configure(CONFIG_P2_EC32MB_SPI_CS_PIN,
                         P2_PIN_OWNER_SPI,
#ifdef CONFIG_P2_EC32MB_ILI9341_XPT2046
                         &chip_select);
#else
                         &output);
#endif
  if (ret < 0)
    {
      goto fail;
    }

#ifdef CONFIG_P2_EC32MB_ILI9341_XPT2046
  ret = p2_pin_configure(CONFIG_P2_EC32MB_XPT2046_CS_PIN,
                         P2_PIN_OWNER_SPI, &chip_select);
  if (ret < 0)
    {
      goto fail;
    }
#endif

  /* Keep every pin floating until all ownership/configuration operations
   * have succeeded.  Preload output latches before enabling direction, and
   * enable the P6 source only after P7 is known to be an input.
   */

  p2_sp_disable(CONFIG_P2_EC32MB_SPI_MISO_PIN);
  p2_sp_disable(CONFIG_P2_EC32MB_SPI_MOSI_PIN);
  p2_sp_disable(CONFIG_P2_EC32MB_SPI_SCK_PIN);
  p2_sp_disable(CONFIG_P2_EC32MB_SPI_CS_PIN);
#ifdef CONFIG_P2_EC32MB_ILI9341_XPT2046
  p2_sp_disable(CONFIG_P2_EC32MB_XPT2046_CS_PIN);
#endif

  clock_high = priv->mode == SPIDEV_MODE2 ||
               priv->mode == SPIDEV_MODE3;
  p2_spi_pin_write(CONFIG_P2_EC32MB_SPI_CS_PIN, true);
#ifdef CONFIG_P2_EC32MB_ILI9341_XPT2046
  p2_spi_pin_write(CONFIG_P2_EC32MB_XPT2046_CS_PIN, true);
#endif
  p2_spi_pin_write(CONFIG_P2_EC32MB_SPI_SCK_PIN, clock_high);
  p2_spi_pin_write(CONFIG_P2_EC32MB_SPI_MOSI_PIN, false);

  p2_sp_dir_high(CONFIG_P2_EC32MB_SPI_CS_PIN);
#ifdef CONFIG_P2_EC32MB_ILI9341_XPT2046
  p2_sp_dir_high(CONFIG_P2_EC32MB_XPT2046_CS_PIN);
#endif
  p2_sp_dir_high(CONFIG_P2_EC32MB_SPI_SCK_PIN);
  p2_sp_dir_high(CONFIG_P2_EC32MB_SPI_MOSI_PIN);
  priv->outputs_enabled = true;
  return 0;

fail:
  p2_spi_release(priv);
  return ret;
}

static void p2_spi_select(FAR struct spi_bitbang_s *dev, uint32_t devid,
                          bool selected)
{
  FAR struct p2_spi_lower_s *priv = p2_spi_lower(dev);
  int ret;

  if (!selected)
    {
      p2_spi_release(priv);
      priv->faulted = false;
      return;
    }

  if (priv->selected || priv->faulted)
    {
      return;
    }

  if (!p2_spi_device_supported(devid) || !priv->mode_valid)
    {
      priv->faulted = true;
      return;
    }

  ret = p2_spi_activate(priv);
  if (ret < 0)
    {
      priv->faulted = true;
      return;
    }

#ifdef CONFIG_P2_EC32MB_ILI9341_XPT2046
  if (devid == SPIDEV_TOUCHSCREEN(0))
    {
      p2_sp_out_low(CONFIG_P2_EC32MB_XPT2046_CS_PIN);
    }
  else
#endif
    {
      p2_sp_out_low(CONFIG_P2_EC32MB_SPI_CS_PIN);
    }

  priv->selected = true;
}

static uint32_t p2_spi_setfrequency(FAR struct spi_bitbang_s *dev,
                                    uint32_t frequency)
{
  FAR struct p2_spi_lower_s *priv = p2_spi_lower(dev);
  uint32_t denominator;
  uint32_t half_cycles;

  if (frequency == 0)
    {
      return priv->frequency;
    }

  if (frequency > CONFIG_P2_EC32MB_SPI_MAX_FREQUENCY)
    {
      frequency = CONFIG_P2_EC32MB_SPI_MAX_FREQUENCY;
    }

  denominator = frequency * 2u;
  half_cycles = (CONFIG_P2_SYSCLK_HZ + denominator - 1u) / denominator;
  if (half_cycles == 0)
    {
      half_cycles = 1;
    }

  priv->half_cycles = half_cycles;
  priv->frequency = CONFIG_P2_SYSCLK_HZ / (half_cycles * 2u);
  return priv->frequency;
}

static void p2_spi_setmode(FAR struct spi_bitbang_s *dev,
                           enum spi_mode_e mode)
{
  FAR struct p2_spi_lower_s *priv = p2_spi_lower(dev);

  if (mode >= SPIDEV_MODE0 && mode <= SPIDEV_MODE3)
    {
      priv->mode = mode;
      priv->mode_valid = true;
    }
  else
    {
      priv->mode_valid = false;
    }
}

static uint16_t p2_spi_exchange(FAR struct spi_bitbang_s *dev,
                                uint16_t dataout)
{
  FAR struct p2_spi_lower_s *priv = p2_spi_lower(dev);
  bool clock_high;
  bool phase;
  uint16_t datain = 0;
  uint16_t mask;
  unsigned int nbits = 8;

  if (!priv->selected || priv->faulted || !priv->mode_valid)
    {
      return UINT16_MAX;
    }

  clock_high = priv->mode == SPIDEV_MODE2 ||
               priv->mode == SPIDEV_MODE3;
  phase = priv->mode == SPIDEV_MODE1 || priv->mode == SPIDEV_MODE3;

#ifdef CONFIG_SPI_BITBANG_VARWIDTH
  nbits = dev->nbits;
#endif

  if (nbits == 0 || nbits > 16)
    {
      priv->faulted = true;
      return UINT16_MAX;
    }

  for (mask = (uint16_t)(1u << (nbits - 1)); mask != 0; mask >>= 1)
    {
      if (!phase)
        {
          p2_spi_pin_write(CONFIG_P2_EC32MB_SPI_MOSI_PIN,
                           (dataout & mask) != 0);
          p2_spi_half_delay(priv);
          p2_spi_pin_write(CONFIG_P2_EC32MB_SPI_SCK_PIN, !clock_high);
          if (p2_spi_pin_read(CONFIG_P2_EC32MB_SPI_MISO_PIN))
            {
              datain |= mask;
            }

          p2_spi_half_delay(priv);
          p2_spi_pin_write(CONFIG_P2_EC32MB_SPI_SCK_PIN, clock_high);
        }
      else
        {
          p2_spi_pin_write(CONFIG_P2_EC32MB_SPI_SCK_PIN, !clock_high);
          p2_spi_pin_write(CONFIG_P2_EC32MB_SPI_MOSI_PIN,
                           (dataout & mask) != 0);
          p2_spi_half_delay(priv);
          p2_spi_pin_write(CONFIG_P2_EC32MB_SPI_SCK_PIN, clock_high);
          if (p2_spi_pin_read(CONFIG_P2_EC32MB_SPI_MISO_PIN))
            {
              datain |= mask;
            }

          p2_spi_half_delay(priv);
        }
    }

  return datain;
}

static uint8_t p2_spi_status(FAR struct spi_bitbang_s *dev,
                             uint32_t devid)
{
  (void)dev;
  return p2_spi_device_supported(devid) ? SPI_STATUS_PRESENT : 0;
}

#ifdef CONFIG_SPI_CMDDATA
static int p2_spi_cmddata(FAR struct spi_bitbang_s *dev, uint32_t devid,
                          bool cmd)
{
  (void)dev;

#ifdef CONFIG_P2_EC32MB_ILI9341_XPT2046
  if (devid == SPIDEV_DISPLAY(0))
    {
      p2_spi_pin_write(CONFIG_P2_EC32MB_ILI9341_DC_PIN, !cmd);
      return 0;
    }
#endif

  return p2_spi_device_supported(devid) ? -ENOSYS : -ENODEV;
}
#endif

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int p2_spi_initialize(void)
{
  int ret;

  if (g_p2_spi != NULL)
    {
      return 0;
    }

  g_p2_spi = spi_create_bitbang(&g_p2_spi_ops, &g_p2_spi_lower);
  if (g_p2_spi == NULL)
    {
      return -ENOMEM;
    }

  ret = spi_register(g_p2_spi, 0);
  if (ret < 0)
    {
      spi_destroy_bitbang(g_p2_spi);
      g_p2_spi = NULL;
      return ret;
    }

  return 0;
}

FAR struct spi_dev_s *p2_spi_getdev(void)
{
  return g_p2_spi;
}
