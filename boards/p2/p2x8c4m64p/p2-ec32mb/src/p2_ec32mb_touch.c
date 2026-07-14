/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_touch.c
 *
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

#include <nuttx/config.h>

#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <nuttx/arch.h>
#include <nuttx/irq.h>
#include <nuttx/spi/spi.h>
#include <nuttx/input/ads7843e.h>

#include <arch/board/board.h>

#include "p2_ec32mb_pins.h"

#define P2_TOUCH_HIGH_15K          0x00001000
#define P2_TOUCH_LOW_FLOAT         0x00000700
#define P2_XPT_CMD_Y               0x93
#define P2_XPT_CMD_X               0xd3
#define P2_XPT_CMD_Z1              0xb3
#define P2_XPT_CMD_Z2              0xc3
#define P2_XPT_CMD_PENIRQ          0x90

#if CONFIG_P2_EC32MB_XPT2046_PEN_PIN == CONFIG_P2_EC32MB_SPI_MOSI_PIN || \
    CONFIG_P2_EC32MB_XPT2046_PEN_PIN == CONFIG_P2_EC32MB_SPI_MISO_PIN || \
    CONFIG_P2_EC32MB_XPT2046_PEN_PIN == CONFIG_P2_EC32MB_SPI_SCK_PIN || \
    CONFIG_P2_EC32MB_XPT2046_PEN_PIN == CONFIG_P2_EC32MB_SPI_CS_PIN || \
    CONFIG_P2_EC32MB_XPT2046_PEN_PIN == CONFIG_P2_EC32MB_XPT2046_CS_PIN || \
    CONFIG_P2_EC32MB_XPT2046_PEN_PIN == CONFIG_P2_EC32MB_ILI9341_RESET_PIN || \
    CONFIG_P2_EC32MB_XPT2046_PEN_PIN == CONFIG_P2_EC32MB_ILI9341_DC_PIN
#  error "P2 XPT2046 PEN pin must be distinct from display and SPI pins"
#endif

static xcpt_t g_p2_touch_isr;
static bool g_p2_touch_enabled;
static bool g_p2_touch_last;
static uint32_t g_p2_touch_polls;
static uint32_t g_p2_touch_dispatches;

static uint16_t p2_touch_command(FAR struct spi_dev_s *spi, uint8_t command)
{
  uint8_t response[2];

  SPI_SELECT(spi, SPIDEV_TOUCHSCREEN(0), true);
  SPI_SEND(spi, command);
  up_udelay(3);
  SPI_RECVBLOCK(spi, response, sizeof(response));
  SPI_SELECT(spi, SPIDEV_TOUCHSCREEN(0), false);
  return (((uint16_t)response[0] << 8) | response[1]) >> 4;
}

static bool p2_touch_read(void)
{
  unsigned int value;

  __asm__ __volatile__("testp %1 wc\n\twrc %0"
                       : "=r" (value)
                       : "r" (CONFIG_P2_EC32MB_XPT2046_PEN_PIN));
  return value != 0;
}

#ifndef CONFIG_P2_EC32MB_TOUCHPEN_GPIO_ONLY
static int p2_touch_attach(FAR struct ads7843e_config_s *state, xcpt_t isr)
{
  irqstate_t flags;

  (void)state;
  flags = enter_critical_section();
  g_p2_touch_isr = isr;
  leave_critical_section(flags);
  return 0;
}

static void p2_touch_enable(FAR struct ads7843e_config_s *state, bool enable)
{
  irqstate_t flags;

  (void)state;
  flags = enter_critical_section();
  g_p2_touch_last = p2_touch_read();
  g_p2_touch_enabled = enable;
  leave_critical_section(flags);
}

static void p2_touch_clear(FAR struct ads7843e_config_s *state)
{
  (void)state;
}

static bool p2_touch_busy(FAR struct ads7843e_config_s *state)
{
  (void)state;
  return false;
}

static bool p2_touch_pendown(FAR struct ads7843e_config_s *state)
{
  (void)state;
  return !p2_touch_read();
}

static struct ads7843e_config_s g_p2_touch_config =
{
  .frequency = CONFIG_ADS7843E_FREQUENCY,
#ifndef CONFIG_ADS7843E_MULTIPLE
  .irq = 0,
#endif
  .attach = p2_touch_attach,
  .enable = p2_touch_enable,
  .clear = p2_touch_clear,
  .busy = p2_touch_busy,
  .pendown = p2_touch_pendown,
};
#endif

int p2_touch_initialize(void)
{
  static const struct p2_pin_config_s input =
  {
    .direction = P2_PIN_DIRECTION_INPUT,
#ifdef CONFIG_P2_EC32MB_TOUCHPEN_FLOAT_INPUT
    .drive = P2_PIN_DRIVE_FLOAT,
    .safe = P2_PIN_SAFE_FLOAT,
#else
    .drive = P2_PIN_DRIVE_PULL_UP,
    .safe = P2_PIN_SAFE_PULL_UP,
#endif
    .event = P2_PIN_EVENT_NONE,
    .smartpin_mode = P2_SMARTPIN_MODE_DISABLED,
  };
  FAR struct spi_dev_s *spi;
  int ret;

  spi = p2_spi_getdev();
  if (spi == NULL)
    {
      return -ENODEV;
    }

  ret = p2_pin_claim(CONFIG_P2_EC32MB_XPT2046_PEN_PIN,
                     P2_PIN_OWNER_SPI);
  if (ret < 0)
    {
      return ret;
    }

  ret = p2_pin_configure(CONFIG_P2_EC32MB_XPT2046_PEN_PIN,
                         P2_PIN_OWNER_SPI, &input);
  if (ret < 0)
    {
      p2_pin_release(CONFIG_P2_EC32MB_XPT2046_PEN_PIN,
                     P2_PIN_OWNER_SPI);
      return ret;
    }

  __asm__ __volatile__("dirl %0" : :
                       "r" (CONFIG_P2_EC32MB_XPT2046_PEN_PIN));
#ifdef CONFIG_P2_EC32MB_TOUCHPEN_FLOAT_INPUT
  __asm__ __volatile__("wrpin %0, %1" : : "r" (0),
                       "r" (CONFIG_P2_EC32MB_XPT2046_PEN_PIN));
#else
  __asm__ __volatile__("wrpin %0, %1" : :
                       "r" (P2_TOUCH_HIGH_15K | P2_TOUCH_LOW_FLOAT),
                       "r" (CONFIG_P2_EC32MB_XPT2046_PEN_PIN));
  __asm__ __volatile__("outh %0" : :
                       "r" (CONFIG_P2_EC32MB_XPT2046_PEN_PIN));
  __asm__ __volatile__("dirh %0" : :
                       "r" (CONFIG_P2_EC32MB_XPT2046_PEN_PIN));
#endif

  g_p2_touch_last = p2_touch_read();
#ifdef CONFIG_P2_EC32MB_TOUCHPEN_GPIO_ONLY
  return 0;
#else
  return ads7843e_register(spi, &g_p2_touch_config,
                           CONFIG_ADS7843E_DEVMINOR);
#endif
}

void p2_touch_poll(void)
{
  xcpt_t isr;
  bool current;

  if (!g_p2_touch_enabled || g_p2_touch_isr == NULL)
    {
      return;
    }

  g_p2_touch_polls++;
  current = p2_touch_read();

  /* PENIRQ is active-low and level-sensitive.  Re-dispatch a stable low
   * level after the ADS7843E worker re-enables us, matching what a real
   * level-triggered GPIO interrupt would do.  A stable high level is idle.
   */

  if (current == g_p2_touch_last && current)
    {
      return;
    }

  g_p2_touch_last = current;
  g_p2_touch_dispatches++;
  isr = g_p2_touch_isr;
  isr(0, NULL, NULL);
}

int p2_touch_get_diag(FAR struct p2_touch_diag_s *diag)
{
  irqstate_t flags;

  if (diag == NULL)
    {
      return -EINVAL;
    }

  flags = enter_critical_section();
  diag->polls = g_p2_touch_polls;
  diag->dispatches = g_p2_touch_dispatches;
  diag->attached = g_p2_touch_isr != NULL;
  diag->enabled = g_p2_touch_enabled;
  diag->down = !p2_touch_read();
  leave_critical_section(flags);
  return 0;
}

int p2_touch_read_pen_level(FAR bool *high)
{
  if (high == NULL)
    {
      return -EINVAL;
    }

  *high = p2_touch_read();
  return 0;
}

int p2_touch_read_raw(FAR struct p2_touch_raw_s *sample)
{
  FAR struct spi_dev_s *spi;

  if (sample == NULL)
    {
      return -EINVAL;
    }

  spi = p2_spi_getdev();
  if (spi == NULL)
    {
      return -ENODEV;
    }

  SPI_LOCK(spi, true);
  SPI_SETMODE(spi, SPIDEV_MODE0);
  SPI_SETBITS(spi, 8);
  SPI_HWFEATURES(spi, 0);
  SPI_SETFREQUENCY(spi, CONFIG_ADS7843E_FREQUENCY);

  sample->y = p2_touch_command(spi, P2_XPT_CMD_Y);
  sample->x = p2_touch_command(spi, P2_XPT_CMD_X);
  sample->z1 = p2_touch_command(spi, P2_XPT_CMD_Z1);
  sample->z2 = p2_touch_command(spi, P2_XPT_CMD_Z2);
  (void)p2_touch_command(spi, P2_XPT_CMD_PENIRQ);
  sample->pen_down = !p2_touch_read();

  SPI_LOCK(spi, false);
  return 0;
}
