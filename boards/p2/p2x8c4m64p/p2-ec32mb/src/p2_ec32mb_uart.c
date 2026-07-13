/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_uart.c
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
#include <string.h>
#include <termios.h>

#include <nuttx/serial/serial.h>

#include <arch/board/board.h>

#include "p2_ec32mb_pins.h"
#include "p2_ec32mb_smartpin.h"

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_UART1_TIMEOUT_TICKS       (CONFIG_P2_SYSCLK_HZ / 10u)

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct p2_uart1_priv_s
{
  uint32_t baud;
  bool claimed;
  bool rxenabled;
};

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

static int p2_uart1_setup(struct uart_dev_s *dev);
static void p2_uart1_shutdown(struct uart_dev_s *dev);
static int p2_uart1_attach(struct uart_dev_s *dev);
static void p2_uart1_detach(struct uart_dev_s *dev);
static int p2_uart1_ioctl(struct file *filep, int cmd, unsigned long arg);
static int p2_uart1_receive(struct uart_dev_s *dev, unsigned int *status);
static void p2_uart1_rxint(struct uart_dev_s *dev, bool enable);
static bool p2_uart1_rxavailable(struct uart_dev_s *dev);
static void p2_uart1_send(struct uart_dev_s *dev, int ch);
static void p2_uart1_txint(struct uart_dev_s *dev, bool enable);
static bool p2_uart1_txready(struct uart_dev_s *dev);
static bool p2_uart1_txempty(struct uart_dev_s *dev);

/****************************************************************************
 * Private Data
 ****************************************************************************/

static const struct uart_ops_s g_p2_uart1_ops =
{
  .setup       = p2_uart1_setup,
  .shutdown    = p2_uart1_shutdown,
  .attach      = p2_uart1_attach,
  .detach      = p2_uart1_detach,
  .ioctl       = p2_uart1_ioctl,
  .receive     = p2_uart1_receive,
  .rxint       = p2_uart1_rxint,
  .rxavailable = p2_uart1_rxavailable,
#ifdef CONFIG_SERIAL_IFLOWCONTROL
  .rxflowcontrol = NULL,
#endif
  .send        = p2_uart1_send,
  .txint       = p2_uart1_txint,
  .txready     = p2_uart1_txready,
  .txempty     = p2_uart1_txempty,
};

static char g_p2_uart1_rxbuffer[CONFIG_P2_EC32MB_UART1_RXBUFSIZE];
static char g_p2_uart1_txbuffer[CONFIG_P2_EC32MB_UART1_TXBUFSIZE];

static struct p2_uart1_priv_s g_p2_uart1_priv =
{
  .baud = CONFIG_P2_EC32MB_UART1_BAUD,
};

static struct uart_dev_s g_p2_uart1_dev =
{
  .recv =
    {
      .size = CONFIG_P2_EC32MB_UART1_RXBUFSIZE,
      .buffer = g_p2_uart1_rxbuffer,
    },
  .xmit =
    {
      .size = CONFIG_P2_EC32MB_UART1_TXBUFSIZE,
      .buffer = g_p2_uart1_txbuffer,
    },
  .ops = &g_p2_uart1_ops,
  .priv = &g_p2_uart1_priv,
};

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static uint32_t p2_uart1_config(uint32_t baud)
{
  return ((CONFIG_P2_SYSCLK_HZ / baud) << 16) | 7u;
}

static int p2_uart1_track(unsigned int pin,
                          enum p2_pin_direction_e direction,
                          enum p2_pin_safe_e safe, uint32_t mode)
{
  struct p2_pin_config_s config;

  config.direction = direction;
  config.drive = direction == P2_PIN_DIRECTION_OUTPUT ?
                 P2_PIN_DRIVE_PUSH_PULL : P2_PIN_DRIVE_FLOAT;
  config.event = P2_PIN_EVENT_NONE;
  config.safe = safe;
  config.smartpin_mode = mode;
  return p2_pin_configure(pin, P2_PIN_OWNER_UART, &config);
}

static int p2_uart1_setup(struct uart_dev_s *dev)
{
  struct p2_uart1_priv_s *priv = dev->priv;
  uint32_t config;
  int ret;

  if (priv->claimed)
    {
      return 0;
    }

  if (priv->baud == 0 ||
      CONFIG_P2_EC32MB_UART1_TX_PIN == CONFIG_P2_EC32MB_UART1_RX_PIN)
    {
      return -EINVAL;
    }

  ret = p2_pin_claim(CONFIG_P2_EC32MB_UART1_RX_PIN, P2_PIN_OWNER_UART);
  if (ret < 0)
    {
      return ret;
    }

  ret = p2_uart1_track(CONFIG_P2_EC32MB_UART1_RX_PIN,
                       P2_PIN_DIRECTION_INPUT, P2_PIN_SAFE_FLOAT,
                       P2_SP_ASYNC_RX);
  if (ret < 0)
    {
      goto errout_rx;
    }

  config = p2_uart1_config(priv->baud);
  p2_sp_dir_low(CONFIG_P2_EC32MB_UART1_RX_PIN);
  p2_sp_wrpin(CONFIG_P2_EC32MB_UART1_RX_PIN, P2_SP_ASYNC_RX);
  p2_sp_wxpin(CONFIG_P2_EC32MB_UART1_RX_PIN, config);
  p2_sp_dir_high(CONFIG_P2_EC32MB_UART1_RX_PIN);

  ret = p2_pin_claim(CONFIG_P2_EC32MB_UART1_TX_PIN, P2_PIN_OWNER_UART);
  if (ret < 0)
    {
      goto errout_rx_hardware;
    }

  ret = p2_uart1_track(CONFIG_P2_EC32MB_UART1_TX_PIN,
                       P2_PIN_DIRECTION_OUTPUT, P2_PIN_SAFE_FLOAT,
                       P2_SP_ASYNC_TX);
  if (ret < 0)
    {
      goto errout_tx;
    }

  p2_sp_dir_low(CONFIG_P2_EC32MB_UART1_TX_PIN);
  p2_sp_out_high(CONFIG_P2_EC32MB_UART1_TX_PIN);
  p2_sp_wrpin(CONFIG_P2_EC32MB_UART1_TX_PIN,
              P2_SP_ASYNC_TX | P2_SP_TT_01);
  p2_sp_wxpin(CONFIG_P2_EC32MB_UART1_TX_PIN, config);
  p2_sp_dir_high(CONFIG_P2_EC32MB_UART1_TX_PIN);
  priv->claimed = true;
  return 0;

errout_tx:
  p2_pin_release(CONFIG_P2_EC32MB_UART1_TX_PIN, P2_PIN_OWNER_UART);
errout_rx_hardware:
  p2_sp_disable(CONFIG_P2_EC32MB_UART1_RX_PIN);
errout_rx:
  p2_pin_release(CONFIG_P2_EC32MB_UART1_RX_PIN, P2_PIN_OWNER_UART);
  return ret;
}

static void p2_uart1_shutdown(struct uart_dev_s *dev)
{
  struct p2_uart1_priv_s *priv = dev->priv;

  if (!priv->claimed)
    {
      return;
    }

  priv->rxenabled = false;

  /* Disable the source before the receiver so a direct loopback is never
   * left with a live source and an unconfigured receiving endpoint.
   */

  p2_sp_disable(CONFIG_P2_EC32MB_UART1_TX_PIN);
  p2_sp_disable(CONFIG_P2_EC32MB_UART1_RX_PIN);
  p2_pin_release(CONFIG_P2_EC32MB_UART1_TX_PIN, P2_PIN_OWNER_UART);
  p2_pin_release(CONFIG_P2_EC32MB_UART1_RX_PIN, P2_PIN_OWNER_UART);
  priv->claimed = false;
}

static int p2_uart1_attach(struct uart_dev_s *dev)
{
  (void)dev;
  return 0;
}

static void p2_uart1_detach(struct uart_dev_s *dev)
{
  struct p2_uart1_priv_s *priv = dev->priv;

  priv->rxenabled = false;
}

static int p2_uart1_ioctl(struct file *filep, int cmd, unsigned long arg)
{
  struct inode *inode = filep->f_inode;
  struct uart_dev_s *dev = inode->i_private;
  struct p2_uart1_priv_s *priv = dev->priv;

#ifdef CONFIG_SERIAL_TERMIOS
  struct termios *termiosp = (struct termios *)(uintptr_t)arg;
  speed_t baud;
  uint32_t config;

  switch (cmd)
    {
      case TCGETS:
        if (termiosp == NULL)
          {
            return -EINVAL;
          }

        memset(termiosp, 0, sizeof(*termiosp));
        termiosp->c_cflag = CS8 | CLOCAL | CREAD;
        cfsetispeed(termiosp, priv->baud);
        return 0;

      case TCSETS:
        if (termiosp == NULL)
          {
            return -EINVAL;
          }

        if ((termiosp->c_cflag & CSIZE) != CS8 ||
            (termiosp->c_cflag & (PARENB | CSTOPB)) != 0)
          {
            return -ENOSYS;
          }

        baud = cfgetispeed(termiosp);
        if (baud < 1200 || baud > 1000000)
          {
            return -ERANGE;
          }

        priv->baud = baud;
        config = p2_uart1_config(priv->baud);

        /* Quiesce the source, establish the receiver first, then restore
         * the source with the new bit period.
         */

        p2_sp_dir_low(CONFIG_P2_EC32MB_UART1_TX_PIN);
        p2_sp_dir_low(CONFIG_P2_EC32MB_UART1_RX_PIN);
        p2_sp_wrpin(CONFIG_P2_EC32MB_UART1_RX_PIN, P2_SP_ASYNC_RX);
        p2_sp_wxpin(CONFIG_P2_EC32MB_UART1_RX_PIN, config);
        p2_sp_dir_high(CONFIG_P2_EC32MB_UART1_RX_PIN);
        p2_sp_out_high(CONFIG_P2_EC32MB_UART1_TX_PIN);
        p2_sp_wrpin(CONFIG_P2_EC32MB_UART1_TX_PIN,
                    P2_SP_ASYNC_TX | P2_SP_TT_01);
        p2_sp_wxpin(CONFIG_P2_EC32MB_UART1_TX_PIN, config);
        p2_sp_dir_high(CONFIG_P2_EC32MB_UART1_TX_PIN);
        return 0;

      default:
        break;
    }
#else
  (void)arg;
#endif

  return -ENOTTY;
}

static int p2_uart1_receive(struct uart_dev_s *dev, unsigned int *status)
{
  (void)dev;

  if (status != NULL)
    {
      *status = 0;
    }

  return (int)(p2_sp_rdpin(CONFIG_P2_EC32MB_UART1_RX_PIN) >> 24);
}

static void p2_uart1_rxint(struct uart_dev_s *dev, bool enable)
{
  struct p2_uart1_priv_s *priv = dev->priv;

  priv->rxenabled = enable;
  if (enable && p2_sp_ready(CONFIG_P2_EC32MB_UART1_RX_PIN))
    {
      uart_recvchars(dev);
    }
}

static bool p2_uart1_rxavailable(struct uart_dev_s *dev)
{
  struct p2_uart1_priv_s *priv = dev->priv;

  return priv->claimed && p2_sp_ready(CONFIG_P2_EC32MB_UART1_RX_PIN);
}

static void p2_uart1_send(struct uart_dev_s *dev, int ch)
{
  struct p2_uart1_priv_s *priv = dev->priv;
  uint32_t deadline;

  p2_sp_wypin(CONFIG_P2_EC32MB_UART1_TX_PIN, (uint8_t)ch);
  deadline = p2_sp_counter() + P2_UART1_TIMEOUT_TICKS;

  /* The TX IN flag means that its single-word buffer became free; RDPIN's
   * carry result is the authoritative transmitter-busy indication.  It is
   * valid three clocks after WYPIN.
   */

  __asm__ __volatile__("waitx #1");
  while (p2_sp_busy(CONFIG_P2_EC32MB_UART1_TX_PIN) &&
         (int32_t)(p2_sp_counter() - deadline) < 0);

  /* A direct TX/RX fixture can complete a character between system ticks.
   * Drain it here before issuing the next byte; independent incoming data
   * is also serviced by board_timerhook().
   */

  if (priv->rxenabled && p2_sp_ready(CONFIG_P2_EC32MB_UART1_RX_PIN))
    {
      uart_recvchars(dev);
    }
}

static void p2_uart1_txint(struct uart_dev_s *dev, bool enable)
{
  if (enable)
    {
      uart_xmitchars(dev);
    }
}

static bool p2_uart1_txready(struct uart_dev_s *dev)
{
  struct p2_uart1_priv_s *priv = dev->priv;

  return priv->claimed;
}

static bool p2_uart1_txempty(struct uart_dev_s *dev)
{
  struct p2_uart1_priv_s *priv = dev->priv;

  return !priv->claimed || !p2_sp_busy(CONFIG_P2_EC32MB_UART1_TX_PIN);
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int p2_uart1_initialize(void)
{
  if (CONFIG_P2_EC32MB_UART1_TX_PIN == CONFIG_P2_EC32MB_UART1_RX_PIN)
    {
      return -EINVAL;
    }

  return uart_register("/dev/ttyS1", &g_p2_uart1_dev);
}

void p2_uart1_poll(void)
{
  if (g_p2_uart1_priv.claimed && g_p2_uart1_priv.rxenabled &&
      p2_sp_ready(CONFIG_P2_EC32MB_UART1_RX_PIN))
    {
      uart_recvchars(&g_p2_uart1_dev);
    }
}
