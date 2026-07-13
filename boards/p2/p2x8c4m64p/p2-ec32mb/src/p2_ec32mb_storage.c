/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_storage.c
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

#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <nuttx/compiler.h>
#include <nuttx/crc32.h>
#ifdef CONFIG_MMCSD_SPI
#  include <nuttx/mmcsd.h>
#endif
#ifdef CONFIG_MTD_W25
#  include <nuttx/mtd/mtd.h>
#endif
#include <nuttx/mutex.h>
#include <nuttx/sched.h>
#include <nuttx/spi/spi.h>

#include <arch/board/board.h>
#include <arch/board/board_flash_layout.h>

#include "p2_ec32mb_storage_arbiter.h"

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#ifndef CONFIG_P2_STORAGE_LOCK_TIMEOUT_TICKS
#  define CONFIG_P2_STORAGE_LOCK_TIMEOUT_TICKS 500
#endif

#ifndef CONFIG_P2_STORAGE_MAX_FREQUENCY
#  define CONFIG_P2_STORAGE_MAX_FREQUENCY 1000000
#endif

#ifndef CONFIG_P2_EC32MB_W25_PROBE_FREQUENCY
#  define CONFIG_P2_EC32MB_W25_PROBE_FREQUENCY 400000
#endif

#ifdef CONFIG_MTD_W25
#  if CONFIG_P2_EC32MB_W25_PROBE_FREQUENCY > 400000
#    error "W25 wake and JEDEC probing must not exceed 400 kHz"
#  endif
#  if CONFIG_P2_EC32MB_W25_PROBE_FREQUENCY >= CONFIG_W25_SPIFREQUENCY
#    error "W25 probe frequency must be below its normal frequency"
#  endif
#  if CONFIG_W25_SPIFREQUENCY > CONFIG_P2_STORAGE_MAX_FREQUENCY
#    error "W25 normal frequency exceeds the P2 storage ceiling"
#  endif
#endif

#ifdef CONFIG_MMCSD_SPI
#  if CONFIG_MMCSD_IDMODE_CLOCK > 400000
#    error "MMC/SD identification mode must not exceed 400 kHz"
#  endif
#  if CONFIG_MMCSD_IDMODE_CLOCK >= CONFIG_MMCSD_SPICLOCK
#    error "MMC/SD identification clock must be below its transfer clock"
#  endif
#  if CONFIG_MMCSD_SPICLOCK > CONFIG_P2_STORAGE_MAX_FREQUENCY
#    error "MMC/SD transfer clock exceeds the P2 storage ceiling"
#  endif
#endif

#define P2_STORAGE_MIN_HALF_CYCLES 4u

#define P2_W25_JEDEC_ID_COMMAND    0x9fu
#define P2_W25_RELEASE_POWERDOWN   0xabu
#define P2_W25_DUMMY               0xffu
#define P2_W25_JEDEC_WINBOND       0xefu
#define P2_W25_JEDEC_MEMORY_A      0x40u
#define P2_W25_JEDEC_MEMORY_B      0x60u
#define P2_W25_JEDEC_MEMORY_C      0x50u
#define P2_W25_JEDEC_MEMORY_D      0x70u
#define P2_W25_JEDEC_CAPACITY      0x18u
#define P2_W25_DATA_FIRSTBLOCK     2048u
#define P2_W25_DATA_NBLOCKS        63488u
#define P2_W25_RAW_ERASEBLOCKS     4096u
#define P2_W25_DATA_ERASEBLOCKS    3968u
#define P2_W25_CRC_BLOCK_BYTES     256u

static_assert(P2_FLASH_PAGE_BYTES == 256u,
              "W25 partition requires 256-byte blocks");
static_assert(P2_FLASH_ERASE_BYTES == 4096u,
              "W25 partition requires 4-KiB erase blocks");
static_assert(P2_FLASH_SIZE_BYTES / P2_FLASH_ERASE_BYTES ==
              P2_W25_RAW_ERASEBLOCKS,
              "W25 raw erase-block count changed");
static_assert(P2_FLASH_FS_OFFSET / P2_FLASH_PAGE_BYTES ==
              P2_W25_DATA_FIRSTBLOCK,
              "W25 data partition offset changed");
static_assert(P2_FLASH_FS_SIZE / P2_FLASH_PAGE_BYTES ==
              P2_W25_DATA_NBLOCKS,
              "W25 data partition size changed");
static_assert(P2_FLASH_FS_SIZE / P2_FLASH_ERASE_BYTES ==
              P2_W25_DATA_ERASEBLOCKS,
              "W25 data erase-block count changed");
static_assert(P2_FLASH_FS_OFFSET == P2_FLASH_BOOT_SIZE,
              "W25 data partition must follow the boot reservation");
static_assert(P2_FLASH_FS_OFFSET % P2_FLASH_ERASE_BYTES == 0,
              "W25 data partition must be erase aligned");
static_assert(P2_FLASH_FS_OFFSET + P2_FLASH_FS_SIZE ==
              P2_FLASH_SIZE_BYTES,
              "W25 partition layout must cover the device");

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct p2_storage_spi_s
{
  struct spi_dev_s spi;
  enum p2_storage_target_e target;
  enum spi_mode_e mode;
  uint32_t frequency;
  uint32_t half_cycles;
  int last_error;
  uint8_t nbits;
  bool selected;
};

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

static int p2_storage_lock(FAR struct spi_dev_s *dev, bool lock);
static void p2_storage_select(FAR struct spi_dev_s *dev, uint32_t devid,
                              bool selected);
static uint32_t p2_storage_setfrequency(FAR struct spi_dev_s *dev,
                                        uint32_t frequency);
#ifdef CONFIG_SPI_DELAY_CONTROL
static int p2_storage_setdelay(FAR struct spi_dev_s *dev, uint32_t start,
                               uint32_t stop, uint32_t cs,
                               uint32_t interframe);
#endif
static void p2_storage_setmode(FAR struct spi_dev_s *dev,
                               enum spi_mode_e mode);
static void p2_storage_setbits(FAR struct spi_dev_s *dev, int nbits);
#ifdef CONFIG_SPI_HWFEATURES
static int p2_storage_hwfeatures(FAR struct spi_dev_s *dev,
                                 spi_hwfeatures_t features);
#endif
static uint8_t p2_storage_status(FAR struct spi_dev_s *dev,
                                 uint32_t devid);
#ifdef CONFIG_SPI_CMDDATA
static int p2_storage_cmddata(FAR struct spi_dev_s *dev, uint32_t devid,
                              bool cmd);
#endif
static uint32_t p2_storage_send(FAR struct spi_dev_s *dev, uint32_t word);
#ifdef CONFIG_SPI_EXCHANGE
static void p2_storage_exchange(FAR struct spi_dev_s *dev,
                                FAR const void *txbuffer,
                                FAR void *rxbuffer, size_t nwords);
#else
static void p2_storage_sndblock(FAR struct spi_dev_s *dev,
                                FAR const void *buffer, size_t nwords);
static void p2_storage_recvblock(FAR struct spi_dev_s *dev,
                                 FAR void *buffer, size_t nwords);
#endif
static int p2_storage_registercallback(FAR struct spi_dev_s *dev,
                                       spi_mediachange_t callback,
                                       FAR void *arg);

/****************************************************************************
 * Private Data
 ****************************************************************************/

static const struct spi_ops_s g_p2_storage_spi_ops =
{
  .lock = p2_storage_lock,
  .select = p2_storage_select,
  .setfrequency = p2_storage_setfrequency,
#ifdef CONFIG_SPI_DELAY_CONTROL
  .setdelay = p2_storage_setdelay,
#endif
  .setmode = p2_storage_setmode,
  .setbits = p2_storage_setbits,
#ifdef CONFIG_SPI_HWFEATURES
  .hwfeatures = p2_storage_hwfeatures,
#endif
  .status = p2_storage_status,
#ifdef CONFIG_SPI_CMDDATA
  .cmddata = p2_storage_cmddata,
#endif
  .send = p2_storage_send,
#ifdef CONFIG_SPI_EXCHANGE
  .exchange = p2_storage_exchange,
#else
  .sndblock = p2_storage_sndblock,
  .recvblock = p2_storage_recvblock,
#endif
#ifdef CONFIG_SPI_TRIGGER
  .trigger = NULL,
#endif
  .registercallback = p2_storage_registercallback,
};

static struct p2_storage_spi_s g_p2_flash_spi =
{
  .spi =
  {
    .ops = &g_p2_storage_spi_ops,
  },
  .target = P2_STORAGE_TARGET_FLASH,
  .mode = SPIDEV_MODE3,
  .frequency = 400000,
  .half_cycles = CONFIG_P2_SYSCLK_HZ / 800000,
  .nbits = 8,
};

static struct p2_storage_spi_s g_p2_sd_spi =
{
  .spi =
  {
    .ops = &g_p2_storage_spi_ops,
  },
  .target = P2_STORAGE_TARGET_SD,
  .mode = SPIDEV_MODE0,
  .frequency = 400000,
  .half_cycles = CONFIG_P2_SYSCLK_HZ / 800000,
  .nbits = 8,
};

static mutex_t g_p2_storage_mutex = NXMUTEX_INITIALIZER;
static struct p2_storage_arbiter_s g_p2_storage_arbiter;
static bool g_p2_storage_initialized;
#ifdef CONFIG_MTD_W25
static FAR struct mtd_dev_s *g_p2_w25;
static struct p2_w25_info_s g_p2_w25_info;
static bool g_p2_w25_info_valid;
#  if defined(CONFIG_MTD_PARTITION) && defined(CONFIG_MTD_SMART) && \
      defined(CONFIG_FS_SMARTFS)
static FAR struct mtd_dev_s *g_p2_w25_data;
static bool g_p2_w25_smart_initialized;
#  endif
#endif
#ifdef CONFIG_MMCSD_SPI
static bool g_p2_mmcsd_initialized;
#endif

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static struct p2_storage_spi_s *p2_storage_priv(
  FAR struct spi_dev_s *dev)
{
  return (struct p2_storage_spi_s *)dev;
}

static int p2_storage_mutex_lock(void *arg, uint32_t timeout)
{
  (void)arg;
  return nxmutex_ticklock(&g_p2_storage_mutex, timeout);
}

static int p2_storage_mutex_unlock(void *arg)
{
  (void)arg;
  return nxmutex_unlock(&g_p2_storage_mutex);
}

static void p2_storage_pin_high(unsigned int pin)
{
  __asm__ __volatile__("drvh %0" : : "ri" (pin));
}

static void p2_storage_pin_low(unsigned int pin)
{
  __asm__ __volatile__("drvl %0" : : "ri" (pin));
}

static void p2_storage_pin_input(unsigned int pin)
{
  __asm__ __volatile__("dirl %0" : : "ri" (pin));
}

static bool p2_storage_pin_read(unsigned int pin)
{
  int high;

  __asm__ __volatile__("testp %1 wc\n\twrc %0"
                       : "=r" (high)
                       : "ri" (pin));
  return high != 0;
}

static void p2_storage_apply(void *arg,
                             const struct p2_storage_lines_s *lines)
{
  FAR struct p2_storage_arbiter_s *arbiter = arg;

  /* P58 is never driven.  First set P59 to a harmless one bit, then
   * deselect the currently active target before establishing new roles.
   */

  p2_storage_pin_input(BOARD_FLASH_MISO_PIN);
  p2_storage_pin_high(BOARD_FLASH_MOSI_PIN);

  if (arbiter->state == P2_STORAGE_FLASH_SELECTED)
    {
      p2_storage_pin_high(BOARD_FLASH_CS_PIN);
      p2_storage_pin_high(BOARD_FLASH_CLK_PIN);
    }
  else
    {
      p2_storage_pin_high(BOARD_SD_CS_PIN);
      p2_storage_pin_high(BOARD_SD_CLK_PIN);
    }

  if ((lines->levels & P2_STORAGE_FLASH_CLK) == 0)
    {
      p2_storage_pin_low(BOARD_FLASH_CLK_PIN);
    }

  if ((lines->levels & P2_STORAGE_FLASH_CS) == 0)
    {
      p2_storage_pin_low(BOARD_FLASH_CS_PIN);
    }
}

static const struct p2_storage_arbiter_ops_s g_p2_storage_arbiter_ops =
{
  .lock = p2_storage_mutex_lock,
  .unlock = p2_storage_mutex_unlock,
  .apply = p2_storage_apply,
};

static int p2_storage_initialize_once(void)
{
  int ret;

  if (g_p2_storage_initialized)
    {
      return 0;
    }

  ret = p2_storage_arbiter_initialize(&g_p2_storage_arbiter,
                                      &g_p2_storage_arbiter_ops,
                                      &g_p2_storage_arbiter);
  if (ret == 0)
    {
      g_p2_storage_initialized = true;
    }

  return ret;
}

static bool p2_storage_devid_valid(struct p2_storage_spi_s *priv,
                                   uint32_t devid)
{
  if (priv->target == P2_STORAGE_TARGET_FLASH)
    {
      return devid == SPIDEV_FLASH(0);
    }

  return devid == SPIDEV_MMCSD(0);
}

static bool p2_storage_mode_valid(struct p2_storage_spi_s *priv)
{
  return (priv->target == P2_STORAGE_TARGET_FLASH &&
          priv->mode == SPIDEV_MODE3) ||
         (priv->target == P2_STORAGE_TARGET_SD &&
          priv->mode == SPIDEV_MODE0);
}

static void p2_storage_fault(struct p2_storage_spi_s *priv, int error)
{
  priv->last_error = error;
  priv->selected = false;

  if (nxmutex_is_hold(&g_p2_storage_mutex) &&
      g_p2_storage_arbiter.owner == priv->target)
    {
      p2_storage_arbiter_fail(&g_p2_storage_arbiter, priv->target);
    }
}

static bool p2_storage_transfer_ready(struct p2_storage_spi_s *priv)
{
  if (!g_p2_storage_initialized ||
      !nxmutex_is_hold(&g_p2_storage_mutex) ||
      !p2_storage_mode_valid(priv) || priv->nbits != 8 ||
      !p2_storage_arbiter_transaction_allowed(&g_p2_storage_arbiter,
                                               priv->target))
    {
      p2_storage_fault(priv, -EPERM);
      return false;
    }

  return true;
}

static void p2_storage_half_delay(struct p2_storage_spi_s *priv)
{
  __asm__ __volatile__("waitx %0" : : "r" (priv->half_cycles));
}

static uint8_t p2_storage_exchange_byte(struct p2_storage_spi_s *priv,
                                        uint8_t tx)
{
  enum p2_storage_state_e state = g_p2_storage_arbiter.state;
  unsigned int clock;
  uint8_t rx = 0;
  int bit;

  if (!p2_storage_transfer_ready(priv))
    {
      return 0xff;
    }

  if (priv->target == P2_STORAGE_TARGET_FLASH)
    {
      clock = BOARD_FLASH_CLK_PIN;
      p2_storage_pin_high(clock);
    }
  else
    {
      clock = BOARD_SD_CLK_PIN;
      p2_storage_pin_low(clock);
    }

  for (bit = 7; bit >= 0; bit--)
    {
      if ((tx & (1u << bit)) != 0)
        {
          p2_storage_pin_high(BOARD_FLASH_MOSI_PIN);
        }
      else
        {
          p2_storage_pin_low(BOARD_FLASH_MOSI_PIN);
        }

      if (priv->mode == SPIDEV_MODE3)
        {
          p2_storage_pin_low(clock);
          p2_storage_half_delay(priv);
          p2_storage_pin_high(clock);
        }
      else
        {
          p2_storage_half_delay(priv);
          p2_storage_pin_high(clock);
        }

      rx = (uint8_t)(rx << 1);
      if (p2_storage_pin_read(BOARD_FLASH_MISO_PIN))
        {
          rx |= 1;
        }

      p2_storage_half_delay(priv);
      if (priv->mode == SPIDEV_MODE0)
        {
          p2_storage_pin_low(clock);
        }
    }

  p2_storage_pin_high(BOARD_FLASH_MOSI_PIN);
  if (state == P2_STORAGE_IDLE)
    {
      /* Deselect both devices after clocks requested with no selected
       * target.  MMC/SD uses this for the post-CS clocks that release MISO.
       */

      p2_storage_apply(&g_p2_storage_arbiter,
                       &g_p2_storage_arbiter.lines);
    }

  return rx;
}

static int p2_storage_lock(FAR struct spi_dev_s *dev, bool lock)
{
  struct p2_storage_spi_s *priv = p2_storage_priv(dev);
  int ret;

  ret = p2_storage_initialize_once();
  if (ret < 0)
    {
      priv->last_error = ret;
      return ret;
    }

  if (lock)
    {
      ret = p2_storage_arbiter_acquire(&g_p2_storage_arbiter,
                                       priv->target,
                                       CONFIG_P2_STORAGE_LOCK_TIMEOUT_TICKS);
      if (ret == 0)
        {
          priv->selected = false;
          priv->last_error = 0;
        }
    }
  else if (!nxmutex_is_hold(&g_p2_storage_mutex))
    {
      ret = -EPERM;
    }
  else
    {
      ret = p2_storage_arbiter_release(&g_p2_storage_arbiter,
                                       priv->target);
      priv->selected = false;
    }

  if (ret < 0)
    {
      priv->last_error = ret;
    }

  return ret;
}

static void p2_storage_select(FAR struct spi_dev_s *dev, uint32_t devid,
                              bool selected)
{
  struct p2_storage_spi_s *priv = p2_storage_priv(dev);
  int ret;

  if (!selected && !nxmutex_is_hold(&g_p2_storage_mutex))
    {
      /* W25 performs one initial deselect before taking its bus lock.  The
       * pins are already safe after board initialization, so this is a
       * legitimate no-op.  Never touch them if another task owns the bus.
       */

      return;
    }

  if (!p2_storage_devid_valid(priv, devid) ||
      !nxmutex_is_hold(&g_p2_storage_mutex) ||
      g_p2_storage_arbiter.owner != priv->target)
    {
      p2_storage_fault(priv, -EPERM);
      return;
    }

  if (selected)
    {
      if (priv->selected)
        {
          return;
        }

      ret = p2_storage_arbiter_select(&g_p2_storage_arbiter,
                                      priv->target);
    }
  else if (priv->selected)
    {
      ret = p2_storage_arbiter_deselect(&g_p2_storage_arbiter,
                                        priv->target);
    }
  else
    {
      ret = 0;
    }

  if (ret < 0)
    {
      p2_storage_fault(priv, ret);
    }
  else
    {
      priv->selected = selected;
    }
}

static uint32_t p2_storage_setfrequency(FAR struct spi_dev_s *dev,
                                        uint32_t frequency)
{
  struct p2_storage_spi_s *priv = p2_storage_priv(dev);
  uint32_t half_cycles;

  if (frequency == 0)
    {
      return priv->frequency;
    }

  if (frequency > CONFIG_P2_STORAGE_MAX_FREQUENCY)
    {
      frequency = CONFIG_P2_STORAGE_MAX_FREQUENCY;
    }

  half_cycles = (CONFIG_P2_SYSCLK_HZ + (frequency * 2u) - 1u) /
                (frequency * 2u);
  if (half_cycles < P2_STORAGE_MIN_HALF_CYCLES)
    {
      half_cycles = P2_STORAGE_MIN_HALF_CYCLES;
    }

  priv->half_cycles = half_cycles;
  priv->frequency = CONFIG_P2_SYSCLK_HZ / (half_cycles * 2u);
  return priv->frequency;
}

#ifdef CONFIG_SPI_DELAY_CONTROL
static int p2_storage_setdelay(FAR struct spi_dev_s *dev, uint32_t start,
                               uint32_t stop, uint32_t cs,
                               uint32_t interframe)
{
  (void)dev;
  return start == 0 && stop == 0 && cs == 0 && interframe == 0 ? 0 :
         -ENOSYS;
}
#endif

static void p2_storage_setmode(FAR struct spi_dev_s *dev,
                               enum spi_mode_e mode)
{
  struct p2_storage_spi_s *priv = p2_storage_priv(dev);

  priv->mode = mode;
  if (!p2_storage_mode_valid(priv))
    {
      p2_storage_fault(priv, -EINVAL);
    }
}

static void p2_storage_setbits(FAR struct spi_dev_s *dev, int nbits)
{
  struct p2_storage_spi_s *priv = p2_storage_priv(dev);

  priv->nbits = (uint8_t)nbits;
  if (nbits != 8)
    {
      p2_storage_fault(priv, -EINVAL);
    }
}

#ifdef CONFIG_SPI_HWFEATURES
static int p2_storage_hwfeatures(FAR struct spi_dev_s *dev,
                                 spi_hwfeatures_t features)
{
  (void)dev;
  return features == 0 ? 0 : -ENOSYS;
}
#endif

static uint8_t p2_storage_status(FAR struct spi_dev_s *dev,
                                 uint32_t devid)
{
  struct p2_storage_spi_s *priv = p2_storage_priv(dev);

  if (!p2_storage_devid_valid(priv, devid))
    {
      return 0;
    }

  /* The socket has no card-detect signal.  NuttX's SPI MMC/SD binding uses
   * an always-present policy and discovers absence by bounded commands.
   */

  return SPI_STATUS_PRESENT;
}

#ifdef CONFIG_SPI_CMDDATA
static int p2_storage_cmddata(FAR struct spi_dev_s *dev, uint32_t devid,
                              bool cmd)
{
  (void)dev;
  (void)devid;
  (void)cmd;
  return -ENOSYS;
}
#endif

static uint32_t p2_storage_send(FAR struct spi_dev_s *dev, uint32_t word)
{
  return p2_storage_exchange_byte(p2_storage_priv(dev), (uint8_t)word);
}

#ifdef CONFIG_SPI_EXCHANGE
static void p2_storage_exchange(FAR struct spi_dev_s *dev,
                                FAR const void *txbuffer,
                                FAR void *rxbuffer, size_t nwords)
{
  struct p2_storage_spi_s *priv = p2_storage_priv(dev);
  FAR const uint8_t *tx = txbuffer;
  FAR uint8_t *rx = rxbuffer;
  size_t i;

  for (i = 0; i < nwords; i++)
    {
      uint8_t value = p2_storage_exchange_byte(priv,
                                               tx == NULL ? 0xff : tx[i]);

      if (rx != NULL)
        {
          rx[i] = value;
        }
    }
}
#else
static void p2_storage_sndblock(FAR struct spi_dev_s *dev,
                                FAR const void *buffer, size_t nwords)
{
  struct p2_storage_spi_s *priv = p2_storage_priv(dev);
  FAR const uint8_t *tx = buffer;
  size_t i;

  for (i = 0; i < nwords; i++)
    {
      p2_storage_exchange_byte(priv, tx[i]);
    }
}

static void p2_storage_recvblock(FAR struct spi_dev_s *dev,
                                 FAR void *buffer, size_t nwords)
{
  struct p2_storage_spi_s *priv = p2_storage_priv(dev);
  FAR uint8_t *rx = buffer;
  size_t i;

  for (i = 0; i < nwords; i++)
    {
      rx[i] = p2_storage_exchange_byte(priv, 0xff);
    }
}
#endif

static int p2_storage_registercallback(FAR struct spi_dev_s *dev,
                                       spi_mediachange_t callback,
                                       FAR void *arg)
{
  (void)dev;
  (void)callback;
  (void)arg;
  return -ENOSYS;
}

#ifdef CONFIG_MTD_W25
static int p2_w25_read_jedec(FAR struct spi_dev_s *spi,
                             FAR uint8_t jedec[3],
                             FAR uint32_t *probe_frequency,
                             FAR uint32_t *active_frequency)
{
  int ret;
  int unlock_ret;

  ret = SPI_LOCK(spi, true);
  if (ret < 0)
    {
      return ret;
    }

  SPI_SETMODE(spi, CONFIG_W25_SPIMODE);
  SPI_SETBITS(spi, 8);
  SPI_HWFEATURES(spi, 0);
  *probe_frequency = SPI_SETFREQUENCY(
    spi, CONFIG_P2_EC32MB_W25_PROBE_FREQUENCY);

  /* Match the W25 lower half's wake sequence.  Reading the identity before
   * w25_initialize() also avoids racing the status-register write that the
   * writable W25 lower half uses to clear block-protect bits.
   */

  SPI_SELECT(spi, SPIDEV_FLASH(0), true);
  SPI_SEND(spi, P2_W25_RELEASE_POWERDOWN);
  SPI_SELECT(spi, SPIDEV_FLASH(0), false);
  nxsched_usleep(20);

  SPI_SELECT(spi, SPIDEV_FLASH(0), true);
  SPI_SEND(spi, P2_W25_JEDEC_ID_COMMAND);
  jedec[0] = (uint8_t)SPI_SEND(spi, P2_W25_DUMMY);
  jedec[1] = (uint8_t)SPI_SEND(spi, P2_W25_DUMMY);
  jedec[2] = (uint8_t)SPI_SEND(spi, P2_W25_DUMMY);
  SPI_SELECT(spi, SPIDEV_FLASH(0), false);

  /* Leave the logical flash lower half at its normal operating frequency.
   * The generic W25 driver repeats this request for each locked transaction.
   */

  *active_frequency = SPI_SETFREQUENCY(spi, CONFIG_W25_SPIFREQUENCY);

  unlock_ret = SPI_LOCK(spi, false);
  return unlock_ret < 0 ? unlock_ret : 0;
}

static int p2_w25_geometry(FAR struct mtd_dev_s *mtd,
                           FAR struct mtd_geometry_s *geometry)
{
  memset(geometry, 0, sizeof(*geometry));
  return MTD_IOCTL(mtd, MTDIOC_GEOMETRY,
                   (unsigned long)(uintptr_t)geometry);
}

static bool p2_w25_jedec_valid(FAR const uint8_t jedec[3])
{
  return jedec[0] == P2_W25_JEDEC_WINBOND &&
         (jedec[1] == P2_W25_JEDEC_MEMORY_A ||
          jedec[1] == P2_W25_JEDEC_MEMORY_B ||
          jedec[1] == P2_W25_JEDEC_MEMORY_C ||
          jedec[1] == P2_W25_JEDEC_MEMORY_D) &&
         jedec[2] == P2_W25_JEDEC_CAPACITY;
}

static int p2_w25_boot_crc32(FAR struct mtd_dev_s *mtd,
                             FAR uint32_t *result)
{
  uint8_t buffer[P2_W25_CRC_BLOCK_BYTES];
  uint32_t value = 0;
  uint32_t block;

  for (block = 0;
       block < P2_FLASH_BOOT_SIZE / P2_W25_CRC_BLOCK_BYTES;
       block++)
    {
      ssize_t nread = MTD_BREAD(mtd, (off_t)block, 1, buffer);

      if (nread < 0)
        {
          return (int)nread;
        }

      if (nread != 1)
        {
          return -EIO;
        }

      value = crc32part(buffer, sizeof(buffer), value);
    }

  *result = value;
  return 0;
}
#endif

/****************************************************************************
 * Public Functions
 ****************************************************************************/

void p2_storage_board_initialize(void)
{
  p2_storage_initialize_once();
}

FAR struct spi_dev_s *p2_spiflash_spi_initialize(void)
{
  return p2_storage_initialize_once() < 0 ? NULL : &g_p2_flash_spi.spi;
}

FAR struct spi_dev_s *p2_sdspi_initialize(void)
{
  return p2_storage_initialize_once() < 0 ? NULL : &g_p2_sd_spi.spi;
}

#ifdef CONFIG_MTD_W25
int p2_w25_initialize(void)
{
  struct p2_w25_info_s info;
  struct mtd_geometry_s raw_geometry;
  struct mtd_geometry_s data_geometry;
  FAR struct spi_dev_s *spi;
  int ret;

  spi = p2_spiflash_spi_initialize();
  if (spi == NULL)
    {
      return -ENODEV;
    }

  memset(&info, 0, sizeof(info));

  ret = p2_w25_read_jedec(spi, info.jedec,
                          &info.probe_frequency,
                          &info.active_frequency);
  if (ret < 0)
    {
      return ret;
    }

  /* The Rev-B FLASH switch physically disconnects P61 from the W25 chip
   * select.  In SD boot mode the direct probe therefore reads 0xff.  Reject
   * an absent or unexpected part before entering the generic W25 driver,
   * whose initial busy-status loop has no timeout when MISO stays high.
   */

  if (!p2_w25_jedec_valid(info.jedec))
    {
      return -ENODEV;
    }

  /* Keep the raw MTD private.  Phase 13 must partition it before registering
   * any writable device so the boot image is never exposed as filesystem
   * storage.
   */

  if (g_p2_w25 == NULL)
    {
      g_p2_w25 = w25_initialize(spi);
      if (g_p2_w25 == NULL)
        {
          return -ENODEV;
        }
    }

  ret = p2_w25_geometry(g_p2_w25, &raw_geometry);
  if (ret < 0)
    {
      return ret;
    }

  if (raw_geometry.blocksize != P2_FLASH_PAGE_BYTES ||
      raw_geometry.erasesize != P2_FLASH_ERASE_BYTES ||
      raw_geometry.neraseblocks != P2_W25_RAW_ERASEBLOCKS)
    {
      return -ENODEV;
    }

  /* The W25 driver has already validated the manufacturer and memory type.
   * Cross-check the capacity byte against its reported 16-MiB geometry so
   * the board never applies this fixed layout to a smaller supported part.
   */

  if (info.jedec[2] != 0x18u)
    {
      return -ENODEV;
    }

  info.raw_blocksize = raw_geometry.blocksize;
  info.raw_erasesize = raw_geometry.erasesize;
  info.raw_neraseblocks = raw_geometry.neraseblocks;
  info.data_firstblock = P2_W25_DATA_FIRSTBLOCK;
  info.data_nblocks = P2_W25_DATA_NBLOCKS;

  ret = p2_w25_boot_crc32(g_p2_w25, &info.boot_crc32);
  if (ret < 0)
    {
      return ret;
    }

#if defined(CONFIG_MTD_PARTITION) && defined(CONFIG_MTD_SMART) && \
    defined(CONFIG_FS_SMARTFS)
  if (g_p2_w25_data == NULL)
    {
      /* mtd_partition() takes 256-byte read/write block units.  These exact
       * values expose [0x080000, 0x1000000) and make the boot reservation
       * [0x000000, 0x080000) unreachable through the SMART device.
       */

      g_p2_w25_data = mtd_partition(g_p2_w25, 2048, 63488);
      if (g_p2_w25_data == NULL)
        {
          return -ENOMEM;
        }
    }

  ret = p2_w25_geometry(g_p2_w25_data, &data_geometry);
  if (ret < 0)
    {
      return ret;
    }

  if (data_geometry.blocksize != P2_FLASH_PAGE_BYTES ||
      data_geometry.erasesize != P2_FLASH_ERASE_BYTES ||
      data_geometry.neraseblocks != P2_W25_DATA_ERASEBLOCKS)
    {
      return -EINVAL;
    }

  info.data_neraseblocks = data_geometry.neraseblocks;

  if (!g_p2_w25_smart_initialized)
    {
      /* Register /dev/smart0 only.  Formatting and mounting are deliberate
       * later HIL operations; board initialization never requests either.
       */

      ret = smart_initialize(0, g_p2_w25_data, NULL);
      if (ret < 0)
        {
          return ret;
        }

      g_p2_w25_smart_initialized = true;
    }
#else
  (void)data_geometry;
#endif

  g_p2_w25_info = info;
  g_p2_w25_info_valid = true;
  return 0;
}

int p2_w25_get_info(FAR struct p2_w25_info_s *info)
{
  if (info == NULL)
    {
      return -EINVAL;
    }

  if (!g_p2_w25_info_valid)
    {
      return -EAGAIN;
    }

  memcpy(info, &g_p2_w25_info, sizeof(*info));
  return 0;
}
#endif

#ifdef CONFIG_MMCSD_SPI
int p2_mmcsd_initialize(void)
{
  FAR struct spi_dev_s *spi;
  int ret;

  if (g_p2_mmcsd_initialized)
    {
      return 0;
    }

  spi = p2_sdspi_initialize();
  if (spi == NULL)
    {
      return -ENODEV;
    }

  ret = mmcsd_spislotinitialize(0, 0, spi);
  if (ret == 0)
    {
      g_p2_mmcsd_initialized = true;
    }

  return ret;
}
#endif
