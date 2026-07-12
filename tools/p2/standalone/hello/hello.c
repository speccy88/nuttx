/****************************************************************************
 * tools/p2/standalone/hello/hello.c
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
 * Native p2llvm proof-of-life for the P2-EC32MB Rev-B module.
 *
 * This intentionally uses only the pinned libp2 headers and
 * startup/runtime.  It does not use stdio, p2llvm libc, the stock P2ES clock
 * header, or libp2's unbounded UART transmit routine.
 *
 ****************************************************************************/

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <propeller2.h>

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_RCFAST_MODE                0x000000f0u
#define P2_CLOCK_SETUP               0x010008f4u
#define P2_CLOCK_FINAL               0x010008f7u
#define P2_CLOCK_LOCK_WAIT_CYCLES    300000u
#define P2_SYSTEM_FREQUENCY_HZ       180000000u

#define P2_UART_RX_PIN               63u
#define P2_UART_TX_PIN               62u
#define P2_UART_BAUD                 230400u
#define P2_UART_TX_TIMEOUT_TICKS     (P2_SYSTEM_FREQUENCY_HZ / 100u)

#define P2_LED0_PIN                  38u
#define P2_LED1_PIN                  39u
#define P2_LED_TOGGLE_TICKS          (P2_SYSTEM_FREQUENCY_HZ / 100u)

#define P2_DATA_COOKIE               0x50324441u

/****************************************************************************
 * Private Types
 ****************************************************************************/

typedef unsigned int p2_u32;
typedef signed int p2_s32;

typedef char p2_u32_must_be_32_bits[(sizeof(p2_u32) == 4) ? 1 : -1];

/****************************************************************************
 * Private Data
 ****************************************************************************/

static volatile p2_u32 g_data_cookie = P2_DATA_COOKIE;
static volatile p2_u32 g_bss_cookie;

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static inline p2_u32 p2_counter(void)
{
  p2_u32 value;

  __asm__ volatile ("getct %0" : "=r" (value));
  return value;
}

static inline p2_u32 p2_get_ptra(void)
{
  p2_u32 value;

  __asm__ volatile ("mov %0, ptra" : "=r" (value));
  return value;
}

static void p2_clock_configure(void)
{
  /* Select RCFAST before programming the PLL.  P2_CLOCK_SETUP leaves the
   * clock source on RCFAST while the PLL settles; only P2_CLOCK_FINAL
   * selects the PLL.  Keep this wait at or above 300,000 RCFAST cycles.
   */

  hubset(P2_RCFAST_MODE);
  _clkmode = P2_CLOCK_FINAL;
  _clkfreq = P2_SYSTEM_FREQUENCY_HZ;
  hubset(P2_CLOCK_SETUP);
  waitx(P2_CLOCK_LOCK_WAIT_CYCLES);
  hubset(P2_CLOCK_FINAL);
}

static void p2_uart_configure(void)
{
  p2_u32 bit_period;

  dirl(P2_UART_RX_PIN);
  dirl(P2_UART_TX_PIN);

  bit_period = (P2_SYSTEM_FREQUENCY_HZ / P2_UART_BAUD) << 16;
  bit_period &= 0xfffffc00u;
  bit_period |= 7u;

  wrpin(P_ASYNC_TX | P_TT_01, P2_UART_TX_PIN);
  wxpin(bit_period, P2_UART_TX_PIN);
  dirh(P2_UART_TX_PIN);

  wrpin(P_ASYNC_RX, P2_UART_RX_PIN);
  wxpin(bit_period, P2_UART_RX_PIN);
  dirh(P2_UART_RX_PIN);
}

static int p2_uart_send(char ch)
{
  p2_u32 deadline;
  int done;

  wypin((unsigned int)(unsigned char)ch, P2_UART_TX_PIN);
  waitx(20u);
  deadline = p2_counter() + P2_UART_TX_TIMEOUT_TICKS;

  do
    {
      testp(P2_UART_TX_PIN, done);
      if (done != 0)
        {
          return 0;
        }
    }
  while ((p2_s32)(p2_counter() - deadline) < 0);

  return -1;
}

static int p2_uart_puts(const char *text)
{
  while (*text != '\0')
    {
      if (p2_uart_send(*text++) < 0)
        {
          return -1;
        }
    }

  return 0;
}

static int p2_uart_puthex(p2_u32 value)
{
  static const char hex[] = "0123456789ABCDEF";
  unsigned int shift;

  if (p2_uart_puts("0x") < 0)
    {
      return -1;
    }

  for (shift = 28u; ; shift -= 4u)
    {
      if (p2_uart_send(hex[(value >> shift) & 0x0fu]) < 0)
        {
          return -1;
        }

      if (shift == 0u)
        {
          break;
        }
    }

  return 0;
}

static int p2_uart_try_getc(char *ch)
{
  p2_u32 value;
  int available;

  testp(P2_UART_RX_PIN, available);
  if (available == 0)
    {
      return 0;
    }

  rdpin(value, P2_UART_RX_PIN);
  *ch = (char)(value >> 24);
  return 1;
}

static void p2_serial_fault(void)
{
  drvh(P2_LED0_PIN);
  drvl(P2_LED1_PIN);

  for (; ; )
    {
      waitx(P2_SYSTEM_FREQUENCY_HZ);
    }
}

static void p2_emit(const char *text)
{
  if (p2_uart_puts(text) < 0)
    {
      p2_serial_fault();
    }
}

static void p2_emit_hex_line(const char *prefix, p2_u32 value)
{
  if (p2_uart_puts(prefix) < 0 ||
      p2_uart_puthex(value) < 0 ||
      p2_uart_puts("\r\n") < 0)
    {
      p2_serial_fault();
    }
}

static void p2_toggle_leds(void)
{
  outl(P2_LED0_PIN);
  outl(P2_LED1_PIN);
  dirh(P2_LED0_PIN);
  dirh(P2_LED1_PIN);

  outnot(P2_LED0_PIN);
  outnot(P2_LED1_PIN);
  waitx(P2_LED_TOGGLE_TICKS);
  outnot(P2_LED0_PIN);
  outnot(P2_LED1_PIN);
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

/****************************************************************************
 * Name: main
 ****************************************************************************/

__attribute__((section(".text.main"), used))
int main(void)
{
  char command;

  p2_clock_configure();
  p2_uart_configure();
  p2_toggle_leds();

  p2_emit("P2HELLO:ENTRY\r\n");

  if (g_data_cookie == P2_DATA_COOKIE)
    {
      p2_emit("P2HELLO:DATA=OK\r\n");
    }
  else
    {
      p2_emit("P2HELLO:DATA=FAIL\r\n");
    }

  if (g_bss_cookie == 0u)
    {
      p2_emit("P2HELLO:BSS=OK\r\n");
      g_bss_cookie = P2_DATA_COOKIE;
    }
  else
    {
      p2_emit("P2HELLO:BSS=FAIL\r\n");
    }

  p2_emit_hex_line("P2HELLO:PTRA=", p2_get_ptra());
  p2_emit_hex_line("P2HELLO:COUNTER=", p2_counter());
  p2_emit("P2HELLO:READY\r\n");

  do
    {
      waitx(1000u);
    }
  while (p2_uart_try_getc(&command) == 0);

  if (command == '?')
    {
      p2_emit("P2HELLO:ECHO=?\r\n");
    }
  else
    {
      p2_emit("P2HELLO:ECHO=INVALID\r\n");
    }

  for (; ; )
    {
      waitx(P2_SYSTEM_FREQUENCY_HZ);
    }
}
