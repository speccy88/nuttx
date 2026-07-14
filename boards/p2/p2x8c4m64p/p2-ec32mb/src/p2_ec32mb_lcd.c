/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_lcd.c
 *
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

#include <nuttx/config.h>

#include <errno.h>
#include <stdint.h>
#include <syslog.h>

#include <nuttx/arch.h>
#include <nuttx/board.h>
#include <nuttx/lcd/ili9341.h>
#include <nuttx/lcd/lcd.h>
#include <nuttx/spi/spi.h>

#include "p2_ec32mb_pins.h"
#include "p2_ec32mb_smartpin.h"

#if CONFIG_P2_EC32MB_ILI9341_RESET_PIN == CONFIG_P2_EC32MB_ILI9341_DC_PIN || \
    CONFIG_P2_EC32MB_ILI9341_RESET_PIN == CONFIG_P2_EC32MB_SPI_MOSI_PIN || \
    CONFIG_P2_EC32MB_ILI9341_RESET_PIN == CONFIG_P2_EC32MB_SPI_MISO_PIN || \
    CONFIG_P2_EC32MB_ILI9341_RESET_PIN == CONFIG_P2_EC32MB_SPI_SCK_PIN || \
    CONFIG_P2_EC32MB_ILI9341_RESET_PIN == CONFIG_P2_EC32MB_SPI_CS_PIN || \
    CONFIG_P2_EC32MB_ILI9341_RESET_PIN == CONFIG_P2_EC32MB_XPT2046_CS_PIN || \
    CONFIG_P2_EC32MB_ILI9341_DC_PIN == CONFIG_P2_EC32MB_SPI_MOSI_PIN || \
    CONFIG_P2_EC32MB_ILI9341_DC_PIN == CONFIG_P2_EC32MB_SPI_MISO_PIN || \
    CONFIG_P2_EC32MB_ILI9341_DC_PIN == CONFIG_P2_EC32MB_SPI_SCK_PIN || \
    CONFIG_P2_EC32MB_ILI9341_DC_PIN == CONFIG_P2_EC32MB_SPI_CS_PIN || \
    CONFIG_P2_EC32MB_ILI9341_DC_PIN == CONFIG_P2_EC32MB_XPT2046_CS_PIN
#  error "P2 ILI9341 control pins must be distinct from the SPI pins"
#endif

#define P2_ILI9341_FREQUENCY CONFIG_P2_EC32MB_ILI9341_FREQUENCY

struct p2_ili9341_s
{
  struct ili9341_lcd_s dev;
  FAR struct spi_dev_s *spi;
};

static struct p2_ili9341_s g_p2_ili9341;
static FAR struct lcd_dev_s *g_p2_lcd;

static int p2_lcd_claim_output(unsigned int pin)
{
  static const struct p2_pin_config_s output =
  {
    .direction = P2_PIN_DIRECTION_OUTPUT,
    .drive = P2_PIN_DRIVE_PUSH_PULL,
    .event = P2_PIN_EVENT_NONE,
    .safe = P2_PIN_SAFE_HIGH,
    .smartpin_mode = P2_SMARTPIN_MODE_DISABLED,
  };
  int ret;

  ret = p2_pin_claim(pin, P2_PIN_OWNER_SPI);
  if (ret < 0)
    {
      return ret;
    }

  ret = p2_pin_configure(pin, P2_PIN_OWNER_SPI, &output);
  if (ret < 0)
    {
      p2_pin_release(pin, P2_PIN_OWNER_SPI);
      return ret;
    }

  p2_sp_disable(pin);
  p2_sp_out_high(pin);
  p2_sp_dir_high(pin);
  return 0;
}

static void p2_lcd_select(FAR struct ili9341_lcd_s *lcd)
{
  FAR struct p2_ili9341_s *priv = (FAR struct p2_ili9341_s *)lcd;

  SPI_LOCK(priv->spi, true);
  SPI_SETMODE(priv->spi, SPIDEV_MODE0);
  SPI_SETBITS(priv->spi, 8);
  SPI_SETFREQUENCY(priv->spi, P2_ILI9341_FREQUENCY);
  SPI_SELECT(priv->spi, SPIDEV_DISPLAY(0), true);
}

static void p2_lcd_deselect(FAR struct ili9341_lcd_s *lcd)
{
  FAR struct p2_ili9341_s *priv = (FAR struct p2_ili9341_s *)lcd;

  SPI_CMDDATA(priv->spi, SPIDEV_DISPLAY(0), false);
  SPI_SELECT(priv->spi, SPIDEV_DISPLAY(0), false);
  SPI_LOCK(priv->spi, false);
}

static int p2_lcd_sendcmd(FAR struct ili9341_lcd_s *lcd, uint8_t cmd)
{
  FAR struct p2_ili9341_s *priv = (FAR struct p2_ili9341_s *)lcd;

  SPI_CMDDATA(priv->spi, SPIDEV_DISPLAY(0), true);
  SPI_SEND(priv->spi, cmd);
  SPI_CMDDATA(priv->spi, SPIDEV_DISPLAY(0), false);
  return 0;
}

static int p2_lcd_sendparam(FAR struct ili9341_lcd_s *lcd, uint8_t param)
{
  FAR struct p2_ili9341_s *priv = (FAR struct p2_ili9341_s *)lcd;

  SPI_SEND(priv->spi, param);
  return 0;
}

static int p2_lcd_recvparam(FAR struct ili9341_lcd_s *lcd,
                            FAR uint8_t *param)
{
  FAR struct p2_ili9341_s *priv = (FAR struct p2_ili9341_s *)lcd;

  SPI_RECVBLOCK(priv->spi, param, 1);
  return 0;
}

static int p2_lcd_sendgram(FAR struct ili9341_lcd_s *lcd,
                           FAR const uint16_t *pixels, uint32_t count)
{
  FAR struct p2_ili9341_s *priv = (FAR struct p2_ili9341_s *)lcd;

  SPI_SETBITS(priv->spi, 16);
  SPI_SNDBLOCK(priv->spi, pixels, count);
  SPI_SETBITS(priv->spi, 8);
  return 0;
}

static int p2_lcd_recvgram(FAR struct ili9341_lcd_s *lcd,
                           FAR uint16_t *pixels, uint32_t count)
{
  FAR struct p2_ili9341_s *priv = (FAR struct p2_ili9341_s *)lcd;

  SPI_SETBITS(priv->spi, 16);
  SPI_RECVBLOCK(priv->spi, pixels, count);
  SPI_SETBITS(priv->spi, 8);
  return 0;
}

static int p2_lcd_backlight(FAR struct ili9341_lcd_s *lcd, int level)
{
  (void)lcd;

  /* BLK is tied directly to 3.3 V.  MAXPOWER is one, so the only requested
   * nonzero level is already physically selected.
   */

  return level == CONFIG_LCD_MAXPOWER ? 0 : -ENOSYS;
}

int board_lcd_initialize(void)
{
  FAR struct p2_ili9341_s *priv = &g_p2_ili9341;
  FAR struct spi_dev_s *spi;
  int ret;

  if (g_p2_lcd != NULL)
    {
      return 0;
    }

  spi = p2_spi_getdev();
  if (spi == NULL)
    {
      return -ENODEV;
    }

  ret = p2_lcd_claim_output(CONFIG_P2_EC32MB_ILI9341_RESET_PIN);
  if (ret < 0)
    {
      return ret;
    }

  ret = p2_lcd_claim_output(CONFIG_P2_EC32MB_ILI9341_DC_PIN);
  if (ret < 0)
    {
      p2_pin_release(CONFIG_P2_EC32MB_ILI9341_RESET_PIN,
                     P2_PIN_OWNER_SPI);
      return ret;
    }

  up_mdelay(10);
  p2_sp_out_low(CONFIG_P2_EC32MB_ILI9341_RESET_PIN);
  up_mdelay(10);
  p2_sp_out_high(CONFIG_P2_EC32MB_ILI9341_RESET_PIN);
  up_mdelay(120);

  priv->spi = spi;
  priv->dev.select = p2_lcd_select;
  priv->dev.deselect = p2_lcd_deselect;
  priv->dev.sendcmd = p2_lcd_sendcmd;
  priv->dev.sendparam = p2_lcd_sendparam;
  priv->dev.recvparam = p2_lcd_recvparam;
  priv->dev.sendgram = p2_lcd_sendgram;
  priv->dev.recvgram = p2_lcd_recvgram;
  priv->dev.backlight = p2_lcd_backlight;

  g_p2_lcd = ili9341_initialize(&priv->dev, 0);
  if (g_p2_lcd == NULL)
    {
      return -ENODEV;
    }

  return g_p2_lcd->setpower(g_p2_lcd, CONFIG_LCD_MAXPOWER);
}

FAR struct lcd_dev_s *board_lcd_getdev(int lcddev)
{
  return lcddev == 0 ? g_p2_lcd : NULL;
}

void board_lcd_uninitialize(void)
{
  if (g_p2_lcd != NULL)
    {
      g_p2_lcd->setpower(g_p2_lcd, 0);
    }
}
