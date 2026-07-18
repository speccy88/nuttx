/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_boot.c
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

#include <nuttx/board.h>
#if defined(CONFIG_FS_PROCFS) || defined(CONFIG_P2_SMARTPIN) || \
    defined(CONFIG_P2_EC32MB_STORAGE_BINDINGS) || \
    defined(CONFIG_USERLED_LOWER)
#  include <syslog.h>
#endif
#ifdef CONFIG_FS_PROCFS
#  include <nuttx/fs/fs.h>
#endif
#ifdef CONFIG_USERLED_LOWER
#  include <nuttx/leds/userled.h>
#endif

#include <arch/board/board.h>
#include <arch/board/board_flash_layout.h>

#include "p2_ec32mb_pins.h"

#ifdef CONFIG_P2_EC32MB_PSRAM_SERVICE
#  include <arch/board/p2_ec32mb_psram.h>
#endif

#ifdef CONFIG_P2_EC32MB_FLASHBOOT
#  if defined(CONFIG_TESTING_P2STORAGE_DESTRUCTIVE) || \
      defined(CONFIG_FSUTILS_MKSMARTFS) || defined(CONFIG_FSUTILS_MKFATFS)
#    error "P2 flashboot image must not contain destructive storage tools"
#  endif
#  if defined(CONFIG_TESTING_P2STORAGE) && \
      !defined(CONFIG_TESTING_P2STORAGE_FLASH_PREMOUNTED)
#    error "P2 flashboot verification requires the premounted read path"
#  endif
#  ifndef CONFIG_BOARDCTL_RESET
#    error "P2 flashboot profile requires BOARDIOC_RESET support"
#  endif
#endif

#if defined(CONFIG_ARCH_LEDS) && defined(CONFIG_USERLED_LOWER)
#  error "P2 Edge LEDs cannot be OS status LEDs and /dev/userleds together"
#endif

#ifdef CONFIG_FS_PROCFS
#  ifdef CONFIG_NSH_PROC_MOUNTPOINT
#    define P2_PROCFS_MOUNTPOINT CONFIG_NSH_PROC_MOUNTPOINT
#  else
#    define P2_PROCFS_MOUNTPOINT "/proc"
#  endif
#endif

/****************************************************************************
 * Private Functions
 ****************************************************************************/

#ifdef CONFIG_ARCH_LEDS
static void p2_led_write(unsigned int pin, bool on)
{
  if (on)
    {
      __asm__ __volatile__("drvh %0" : : "ri" (pin));
    }
  else
    {
      __asm__ __volatile__("drvl %0" : : "ri" (pin));
    }
}

static void p2_led_pair(bool led0, bool led1)
{
  p2_led_write(BOARD_LED0_PIN, led0);
  p2_led_write(BOARD_LED1_PIN, led1);
}
#endif

/****************************************************************************
 * Public Functions
 ****************************************************************************/

#ifdef CONFIG_ARCH_LEDS
void board_autoled_initialize(void)
{
  p2_led_pair(false, false);
}

void board_autoled_on(int led)
{
  switch (led)
    {
      case LED_STARTED:
        p2_led_pair(false, false);
        break;

      case LED_HEAPALLOCATE:
        p2_led_pair(true, false);
        break;

      case LED_IRQSENABLED:
        p2_led_pair(false, true);
        break;

      case LED_STACKCREATED:
        p2_led_pair(true, true);
        break;

      case LED_INIRQ:
        p2_led_write(BOARD_LED1_PIN, false);
        break;

      case LED_SIGNAL:
        p2_led_write(BOARD_LED0_PIN, false);
        break;

      case LED_ASSERTION:
      case LED_PANIC:
        p2_led_pair(true, false);
        break;

      case LED_IDLE:
        p2_led_write(BOARD_LED1_PIN, false);
        break;

      default:
        break;
    }
}

void board_autoled_off(int led)
{
  switch (led)
    {
      case LED_INIRQ:
      case LED_IDLE:
        p2_led_write(BOARD_LED1_PIN, true);
        break;

      case LED_SIGNAL:
        p2_led_write(BOARD_LED0_PIN, true);
        break;

      case LED_ASSERTION:
      case LED_PANIC:
        p2_led_pair(false, true);
        break;

      default:
        break;
    }
}
#endif

void board_late_initialize(void)
{
#ifdef CONFIG_P2_SMARTPIN
  int pin_ret;

  pin_ret = p2_pin_initialize();
  if (pin_ret < 0)
    {
      syslog(LOG_ERR, "ERROR: Failed to initialize P2 pins: %d\n",
             pin_ret);
      return;
    }
#endif

#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED_SELFTEST
  int xmem_ret;

  xmem_ret = p2_psram_unified_selftest();
  if (xmem_ret < 0)
    {
      syslog(LOG_ERR, "ERROR: P2 unified PSRAM self-test failed: %d\n",
             xmem_ret);
      PANIC();
    }
#endif

#ifdef CONFIG_USERLED_LOWER
  int userled_ret;

  userled_ret = userled_lower_initialize("/dev/userleds");
  if (userled_ret < 0)
    {
      syslog(LOG_ERR, "ERROR: Failed to register P2 user LEDs: %d\n",
             userled_ret);
    }
#endif

#ifdef CONFIG_P2_EC32MB_PSRAM
  int psram_ret;

  psram_ret = p2_psram_initialize();
  if (psram_ret < 0)
    {
      syslog(LOG_ERR, "ERROR: Failed to initialize P2 PSRAM: %d\n",
             psram_ret);
    }
#endif

#ifdef CONFIG_P2_EC32MB_GPIO
  int gpio_ret;

  gpio_ret = p2_gpio_initialize();
  if (gpio_ret < 0)
    {
      syslog(LOG_ERR, "ERROR: Failed to initialize P2 GPIO: %d\n",
             gpio_ret);
    }
#endif

#ifdef CONFIG_P2_EC32MB_UART1
  int uart_ret;

  uart_ret = p2_uart1_initialize();
  if (uart_ret < 0)
    {
      syslog(LOG_ERR, "ERROR: Failed to initialize P2 UART1: %d\n",
             uart_ret);
    }
#endif

#ifdef CONFIG_P2_EC32MB_PWM
  int pwm_ret;

  pwm_ret = p2_pwm_initialize();
  if (pwm_ret < 0)
    {
      syslog(LOG_ERR, "ERROR: Failed to initialize P2 PWM: %d\n",
             pwm_ret);
    }
#endif

#ifdef CONFIG_P2_EC32MB_CAPTURE
  int capture_ret;

  capture_ret = p2_capture_initialize();
  if (capture_ret < 0)
    {
      syslog(LOG_ERR, "ERROR: Failed to initialize P2 capture: %d\n",
             capture_ret);
    }
#endif

#ifdef CONFIG_P2_EC32MB_ADC
  int adc_ret;

  adc_ret = p2_adc_initialize();
  if (adc_ret < 0)
    {
      syslog(LOG_ERR, "ERROR: Failed to initialize P2 ADC: %d\n",
             adc_ret);
    }
#endif

#ifdef CONFIG_P2_EC32MB_DAC
  int dac_ret;

  dac_ret = p2_dac_initialize();
  if (dac_ret < 0)
    {
      syslog(LOG_ERR, "ERROR: Failed to initialize P2 DAC: %d\n",
             dac_ret);
    }
#endif

#ifdef CONFIG_P2_EC32MB_SPI
  int spi_ret;

  spi_ret = p2_spi_initialize();
  if (spi_ret < 0)
    {
      syslog(LOG_ERR, "ERROR: Failed to initialize P2 SPI: %d\n",
             spi_ret);
    }
#endif

#ifdef CONFIG_P2_EC32MB_I2C
  int i2c_ret;

  i2c_ret = p2_i2c_initialize();
  if (i2c_ret < 0)
    {
      syslog(LOG_ERR, "ERROR: Failed to initialize P2 I2C: %d\n",
             i2c_ret);
    }
#endif

#ifdef CONFIG_P2_EC32MB_STORAGE_BINDINGS
#  ifdef CONFIG_MTD_W25
  struct p2_w25_info_s w25_info;
  int w25_ret;

  w25_ret = p2_w25_initialize();
  if (w25_ret == -ENODEV)
    {
      syslog(LOG_NOTICE,
             "P2STORAGE:W25=UNAVAILABLE:CHECK_FLASH_SWITCH\n");
    }
  else if (w25_ret < 0)
    {
      syslog(LOG_ERR, "P2STORAGE:W25=FAIL:%d\n", w25_ret);
    }
  else
    {
      w25_ret = p2_w25_get_info(&w25_info);
      if (w25_ret < 0)
        {
          syslog(LOG_ERR, "P2STORAGE:W25_INFO=FAIL:%d\n", w25_ret);
        }
      else
        {
          syslog(LOG_NOTICE,
                 "P2STORAGE:W25=PRIVATE JEDEC=%02X%02X%02X\n",
                 w25_info.jedec[0], w25_info.jedec[1],
                 w25_info.jedec[2]);
          syslog(LOG_NOTICE,
                 "P2STORAGE:W25_FREQUENCY PROBE=%lu ACTIVE=%lu\n",
                 (unsigned long)w25_info.probe_frequency,
                 (unsigned long)w25_info.active_frequency);
          syslog(LOG_NOTICE,
                 "P2STORAGE:W25_GEOMETRY BLOCK=%lu ERASE=%lu "
                 "ERASEBLOCKS=%lu BYTES=%lu\n",
                 (unsigned long)w25_info.raw_blocksize,
                 (unsigned long)w25_info.raw_erasesize,
                 (unsigned long)w25_info.raw_neraseblocks,
                 (unsigned long)(w25_info.raw_erasesize *
                                 w25_info.raw_neraseblocks));
          syslog(LOG_NOTICE,
                 "P2STORAGE:W25_LAYOUT BOOT=0x%08lX+0x%08lX "
                 "DATA=0x%08lX+0x%08lX FIRSTBLOCK=%lu NBLOCKS=%lu\n",
                 (unsigned long)P2_FLASH_BOOT_OFFSET,
                 (unsigned long)P2_FLASH_BOOT_SIZE,
                 (unsigned long)P2_FLASH_FS_OFFSET,
                 (unsigned long)P2_FLASH_FS_SIZE,
                 (unsigned long)w25_info.data_firstblock,
                 (unsigned long)w25_info.data_nblocks);
          syslog(LOG_NOTICE,
                 "P2STORAGE:W25_BOOT_CRC32=%08lX\n",
                 (unsigned long)w25_info.boot_crc32);
#    if defined(CONFIG_MTD_PARTITION) && defined(CONFIG_MTD_SMART) && \
        defined(CONFIG_FS_SMARTFS)
          syslog(LOG_NOTICE,
                 "P2STORAGE:SMARTFS=/dev/smart0 AUTOFORMAT=NO\n");
#    endif
        }
    }
#  endif

#  ifdef CONFIG_MMCSD_SPI
  int mmcsd_ret;

  mmcsd_ret = p2_mmcsd_initialize();
  if (mmcsd_ret < 0)
    {
      syslog(LOG_ERR, "P2STORAGE:MMCSD=FAIL:%d\n", mmcsd_ret);
    }
  else
    {
      /* mmcsd_spislotinitialize() registers the generic block interface
       * even if no card answers its bounded initialization commands.
       */

      syslog(LOG_NOTICE,
             "P2STORAGE:MMCSD_FREQUENCY ID=%lu TRANSFER=%lu\n",
             (unsigned long)CONFIG_MMCSD_IDMODE_CLOCK,
             (unsigned long)CONFIG_MMCSD_SPICLOCK);
      syslog(LOG_NOTICE, "P2STORAGE:MMCSD=/dev/mmcsd0\n");
    }
#  endif
#endif

#ifdef CONFIG_FS_PROCFS
  int ret;

  ret = nx_mount(NULL, P2_PROCFS_MOUNTPOINT, "procfs", 0, NULL);
  if (ret < 0)
    {
      syslog(LOG_ERR, "ERROR: Failed to mount procfs at %s: %d\n",
             P2_PROCFS_MOUNTPOINT, ret);
    }
#endif
}

void board_initialize(void)
{
#ifdef CONFIG_P2_STORAGE
  /* Make both storage chip selects inactive before any upper half binds. */

  p2_storage_board_initialize();
#endif
}

#ifdef CONFIG_SYSTEMTICK_HOOK
void board_timerhook(void)
{
#ifdef CONFIG_P2_EC32MB_GPIO
  p2_gpio_poll();
#endif
#ifdef CONFIG_P2_EC32MB_UART1
  p2_uart1_poll();
#endif
}
#endif
