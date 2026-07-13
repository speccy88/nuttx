/****************************************************************************
 * arch/p2/src/common/p2_serial.c
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

#include <stdbool.h>
#include <errno.h>
#include <stdint.h>

#include <nuttx/irq.h>
#include <nuttx/serial/serial.h>

#include <arch/board/board.h>

#include "p2_clock.h"
#include "p2_internal.h"

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_ASYNC_TX_MODE           (0x3cu | 0x40u)
#define P2_UART_TIMEOUT_TICKS      (CONFIG_P2_SYSCLK_HZ / 100u)

#if defined(CONFIG_UART0_SERIALDRIVER) && \
    defined(CONFIG_STANDARD_SERIAL)
#  define USE_SERIALDRIVER 1
#endif

#ifdef USE_SERIALDRIVER

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct p2_uart_priv_s
{
  volatile uint32_t rxenabled;
  uint32_t rxcog;
  bool servicing;
};

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

void p2_lowsetup(void);

static int p2_uart_setup(struct uart_dev_s *dev);
static void p2_uart_shutdown(struct uart_dev_s *dev);
static int p2_uart_attach(struct uart_dev_s *dev);
static void p2_uart_detach(struct uart_dev_s *dev);
static int p2_uart_ioctl(struct file *filep, int cmd, unsigned long arg);
static int p2_uart_receive(struct uart_dev_s *dev, unsigned int *status);
static void p2_uart_rxint(struct uart_dev_s *dev, bool enable);
static bool p2_uart_rxavailable(struct uart_dev_s *dev);
static void p2_uart_send(struct uart_dev_s *dev, int ch);
static void p2_uart_txint(struct uart_dev_s *dev, bool enable);
static bool p2_uart_txready(struct uart_dev_s *dev);
static bool p2_uart_txempty(struct uart_dev_s *dev);

/****************************************************************************
 * Private Data
 ****************************************************************************/

static const struct uart_ops_s g_p2_uart_ops =
{
  .setup          = p2_uart_setup,
  .shutdown       = p2_uart_shutdown,
  .attach         = p2_uart_attach,
  .detach         = p2_uart_detach,
  .ioctl          = p2_uart_ioctl,
  .receive        = p2_uart_receive,
  .rxint          = p2_uart_rxint,
  .rxavailable    = p2_uart_rxavailable,
#ifdef CONFIG_SERIAL_IFLOWCONTROL
  .rxflowcontrol  = NULL,
#endif
  .send           = p2_uart_send,
  .txint          = p2_uart_txint,
  .txready        = p2_uart_txready,
  .txempty        = p2_uart_txempty,
};

static char g_p2_uart_rxbuffer[CONFIG_UART0_RXBUFSIZE];
static char g_p2_uart_txbuffer[CONFIG_UART0_TXBUFSIZE];

static struct p2_uart_priv_s g_p2_uart_priv;

static struct uart_dev_s g_p2_uart_dev =
{
  .recv =
  {
    .size   = CONFIG_UART0_RXBUFSIZE,
    .buffer = g_p2_uart_rxbuffer,
  },
  .xmit =
  {
    .size   = CONFIG_UART0_TXBUFSIZE,
    .buffer = g_p2_uart_txbuffer,
  },
  .ops  = &g_p2_uart_ops,
  .priv = &g_p2_uart_priv,
};

#endif /* USE_SERIALDRIVER */

/****************************************************************************
 * Public Data
 ****************************************************************************/

/* A dedicated peripheral cog samples the asynchronous RX pin into this SPSC
 * ring.  Only that cog advances head; only scheduler cog 0 advances tail.
 * P2 Hub accesses are coherent and ordered.
 */

volatile uint8_t g_p2_uart_rx_ring[P2_UART_RX_RING_SIZE] aligned_data(4);
volatile uint32_t g_p2_uart_rx_head;
volatile uint32_t g_p2_uart_rx_tail;
volatile uint32_t g_p2_uart_rx_dropped;
volatile uint32_t g_p2_uart_rx_alive;

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static inline uint32_t p2_counter(void)
{
  uint32_t value;

  __asm__ __volatile__("getct %0" : "=r" (value));
  return value;
}

static inline int p2_tx_complete(void)
{
  int complete;

  __asm__ __volatile__("testp %1 wc\n\twrc %0"
                       : "=r" (complete)
                       : "ri" (BOARD_CONSOLE_TX_PIN));
  return complete;
}

#ifdef USE_SERIALDRIVER

/****************************************************************************
 * Name: p2_uart_setup
 ****************************************************************************/

static int p2_uart_setup(struct uart_dev_s *dev)
{
  (void)dev;
  return OK;
}

/****************************************************************************
 * Name: p2_uart_shutdown
 ****************************************************************************/

static void p2_uart_shutdown(struct uart_dev_s *dev)
{
  (void)dev;
}

/****************************************************************************
 * Name: p2_uart_attach
 *
 * Description:
 *   A dedicated peripheral cog drains P63 into the Hub ring.  RX is then
 *   serviced by p2_serialpoll() from the idle loop.  This attach succeeds
 *   because polling provides the receive notification path; it does not
 *   pretend that a hardware IRQ was attached.
 ****************************************************************************/

static int p2_uart_attach(struct uart_dev_s *dev)
{
  (void)dev;
  return OK;
}

/****************************************************************************
 * Name: p2_uart_detach
 ****************************************************************************/

static void p2_uart_detach(struct uart_dev_s *dev)
{
  struct p2_uart_priv_s *priv = dev->priv;

  priv->rxenabled = false;
}

/****************************************************************************
 * Name: p2_uart_ioctl
 ****************************************************************************/

static int p2_uart_ioctl(struct file *filep, int cmd, unsigned long arg)
{
  (void)filep;
  (void)cmd;
  (void)arg;
  return -ENOTTY;
}

/****************************************************************************
 * Name: p2_uart_receive
 ****************************************************************************/

static int p2_uart_receive(struct uart_dev_s *dev, unsigned int *status)
{
  uint32_t value;
  uint32_t tail;

  (void)dev;
  *status = 0;

  tail = g_p2_uart_rx_tail;
  value = g_p2_uart_rx_ring[tail & P2_UART_RX_RING_MASK];
  g_p2_uart_rx_tail = tail + 1;
  return (int)(uint8_t)value;
}

/****************************************************************************
 * Name: p2_uart_rxint
 ****************************************************************************/

static void p2_uart_rxint(struct uart_dev_s *dev, bool enable)
{
  struct p2_uart_priv_s *priv = dev->priv;

  priv->rxenabled = enable;
}

/****************************************************************************
 * Name: p2_uart_rxavailable
 ****************************************************************************/

static bool p2_uart_rxavailable(struct uart_dev_s *dev)
{
  (void)dev;
  return g_p2_uart_rx_tail != g_p2_uart_rx_head;
}

/****************************************************************************
 * Name: p2_uart_send
 ****************************************************************************/

static void p2_uart_send(struct uart_dev_s *dev, int ch)
{
  (void)dev;
  p2_lowputc(ch);
}

/****************************************************************************
 * Name: p2_uart_txint
 *
 * Description:
 *   Smart Pin TX is drained synchronously.  p2_lowputc() bounds every byte
 *   by P2_UART_TIMEOUT_TICKS, so this cannot wait forever when the pin event
 *   is missing.
 ****************************************************************************/

static void p2_uart_txint(struct uart_dev_s *dev, bool enable)
{
  if (enable)
    {
      uart_xmitchars(dev);
    }
}

/****************************************************************************
 * Name: p2_uart_txready
 ****************************************************************************/

static bool p2_uart_txready(struct uart_dev_s *dev)
{
  (void)dev;

  /* send() waits for each byte to complete, so there is no outstanding
   * character when uart_xmitchars() asks whether it may submit the next.
   */

  return true;
}

/****************************************************************************
 * Name: p2_uart_txempty
 ****************************************************************************/

static bool p2_uart_txempty(struct uart_dev_s *dev)
{
  (void)dev;
  return p2_tx_complete() != 0;
}

static void p2_uart_service(void)
{
  irqstate_t flags;
  bool receive;

  /* This function runs from both up_idle() and the timer ISR.  Protect the
   * test-and-set with IRQ masking so the timer cannot re-enter
   * uart_recvchars() while idle-context service owns the upper-half ring.
   */

  flags = enter_critical_section();
  if (g_p2_uart_priv.servicing)
    {
      leave_critical_section(flags);
      return;
    }

  g_p2_uart_priv.servicing = true;
  receive = g_p2_uart_priv.rxenabled &&
            p2_uart_rxavailable(&g_p2_uart_dev);
  leave_critical_section(flags);

  if (receive)
    {
      uart_recvchars(&g_p2_uart_dev);
    }

  flags = enter_critical_section();
  g_p2_uart_priv.servicing = false;
  leave_critical_section(flags);
}

#endif /* USE_SERIALDRIVER */

/****************************************************************************
 * Public Functions
 ****************************************************************************/

uint32_t p2_console_baud_ticks(void)
{
  return p2_baud_ticks(CONFIG_P2_SYSCLK_HZ, CONFIG_UART0_BAUD);
}

void p2_lowsetup(void)
{
  uint32_t bit_period;

  /* Smart Pin asynchronous serial stores the bit period in the upper 16
   * bits of X and the 8-N-1 frame length minus one in the low field.
   */

  bit_period = p2_console_baud_ticks() << 16;
  bit_period &= 0xfffffc00u;
  bit_period |= 7u;

  __asm__ __volatile__("dirl %0" : : "ri" (BOARD_CONSOLE_RX_PIN));
  __asm__ __volatile__("dirl %0" : : "ri" (BOARD_CONSOLE_TX_PIN));
  __asm__ __volatile__("wrpin %0, %1"
                       :
                       : "ri" (P2_ASYNC_TX_MODE),
                         "ri" (BOARD_CONSOLE_TX_PIN));
  __asm__ __volatile__("wxpin %0, %1"
                       :
                       : "r" (bit_period),
                         "ri" (BOARD_CONSOLE_TX_PIN));
  __asm__ __volatile__("dirh %0" : : "ri" (BOARD_CONSOLE_TX_PIN));
  __asm__ __volatile__("wrpin %0, %1"
                       :
                       : "ri" (P2_UART_ASYNC_RX_MODE),
                         "ri" (BOARD_CONSOLE_RX_PIN));
  __asm__ __volatile__("wxpin %0, %1"
                       :
                       : "r" (bit_period),
                         "ri" (BOARD_CONSOLE_RX_PIN));
  __asm__ __volatile__("dirh %0" : : "ri" (BOARD_CONSOLE_RX_PIN));
}

void p2_lowputc(int ch)
{
  uint32_t deadline;
  uint32_t value = (uint32_t)(uint8_t)ch;

  __asm__ __volatile__("wypin %0, %1"
                       :
                       : "r" (value), "ri" (BOARD_CONSOLE_TX_PIN));
  __asm__ __volatile__("waitx #20");

  deadline = p2_counter() + P2_UART_TIMEOUT_TICKS;
  while (!p2_tx_complete())
    {
      if ((int32_t)(p2_counter() - deadline) >= 0)
        {
          return;
        }
    }
}

#ifdef CONFIG_P2_BOOT_TRACE
void p2_boot_trace(const char *message)
{
  while (*message != '\0')
    {
      p2_lowputc(*message++);
    }

  p2_lowputc('\r');
  p2_lowputc('\n');
}
#endif

void up_putc(int ch)
{
  p2_lowputc(ch);
}

void p2_serialinit(void)
{
#ifdef USE_SERIALDRIVER
  uint32_t deadline;

  p2_lowsetup();

  /* Transfer Smart Pin RX enable ownership to the drain cog. */

  __asm__ __volatile__("dirl %0" : : "ri" (BOARD_CONSOLE_RX_PIN));

  g_p2_uart_rx_head = 0;
  g_p2_uart_rx_tail = 0;
  g_p2_uart_rx_dropped = 0;
  g_p2_uart_rx_alive = 0;
  g_p2_uart_priv.servicing = false;
  g_p2_uart_priv.rxcog = p2_uart_rx_cog_start() == 0;
  deadline = p2_counter() + P2_UART_TIMEOUT_TICKS;
  while (g_p2_uart_priv.rxcog && g_p2_uart_rx_alive == 0 &&
         (int32_t)(p2_counter() - deadline) < 0)
    {
    }

  g_p2_uart_priv.rxcog = g_p2_uart_priv.rxcog &&
                         g_p2_uart_rx_alive != 0;
  p2_boot_trace(g_p2_uart_priv.rxcog ? "P2K:UART:RXCOG=OK" :
                                      "P2K:UART:RXCOG=FAILED");

#ifdef CONFIG_UART0_SERIAL_CONSOLE
  g_p2_uart_dev.isconsole = true;
  uart_register("/dev/console", &g_p2_uart_dev);
#endif

  uart_register("/dev/ttyS0", &g_p2_uart_dev);
#endif
}

void p2_serialpoll(void)
{
#ifdef USE_SERIALDRIVER
  p2_uart_service();
#endif
}

int p2_serialinterrupt(int irq, void *context, void *arg)
{
  (void)irq;
  (void)context;
  (void)arg;

#ifdef USE_SERIALDRIVER
  p2_uart_service();
#endif
  return OK;
}
