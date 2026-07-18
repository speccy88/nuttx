/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_sdio.c
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
#include <sys/stat.h>

#include <nuttx/arch.h>
#include <nuttx/clock.h>
#include <nuttx/compiler.h>
#include <nuttx/fs/fs.h>
#include <nuttx/mmcsd.h>
#include <nuttx/mutex.h>
#include <nuttx/sched.h>
#include <nuttx/sdio.h>

#include <arch/board/board.h>

#include "p2_ec32mb_sdio_wire.h"

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_SDIO_DAT0_PIN             BOARD_SDIO_DAT0_PIN
#define P2_SDIO_DAT1_PIN             BOARD_SDIO_DAT1_PIN
#define P2_SDIO_DAT2_PIN             BOARD_SDIO_DAT2_PIN
#define P2_SDIO_DAT3_PIN             BOARD_SDIO_DAT3_PIN
#define P2_SDIO_CMD_PIN              BOARD_SDIO_CMD_PIN
#define P2_SDIO_CLK_PIN              BOARD_SDIO_CLK_PIN
#define P2_SDIO_ACTIVITY_PIN         BOARD_SDIO_ACTIVITY_PIN
#define P2_SDIO_POWER_PIN            BOARD_SDIO_POWER_PIN

#define P2_SDIO_PIN_HIGH_15K         0x00001000u
#define P2_SDIO_PIN_LOW_FLOAT        0x00000000u
#define P2_SDIO_PULLUP_MODE          (P2_SDIO_PIN_HIGH_15K | \
                                      P2_SDIO_PIN_LOW_FLOAT)

#define P2_SDIO_COG_NONE             UINT8_MAX
#define P2_SDIO_CMD6                 (MMCSD_CMDIDX6 | MMCSD_R1_RESPONSE | \
                                      MMCSD_RDDATAXFR)
#define P2_SDIO_CMD6_CHECK_HS        0x00fffff1u
#define P2_SDIO_CMD6_SWITCH_HS       0x80fffff1u
#define P2_SDIO_DEFAULT_MAX_HZ       25000000u
#define P2_SDIO_HIGH_SPEED_MAX_HZ    50000000u
#define P2_SDIO_R1_ERROR_MASK        UINT32_C(0xfdffe088)
#define P2_SDIO_SWITCH_CLOCKS        8u
#define P2_SDIO_INPUT_ASYNC          P2_SDIO_PULLUP_MODE
#define P2_SDIO_INPUT_SYNC           (P2_SDIO_PULLUP_MODE | 0x00010000u)
#define P2_SDIO_FAST_BLOCKLEN        512u
#define P2_SDIO_POWER_OFF_USEC       10000u
#define P2_SDIO_POWER_STABLE_USEC    5000u
#define P2_SDIO_FIXTURE_PIN(pin)     ((pin) >= P2_SDIO_DAT0_PIN && \
                                      (pin) <= P2_SDIO_POWER_PIN)

/* The native engine accesses P16-P23 directly from two cogs, outside the
 * configurable Smart-Pin lower halves' runtime owner.  Reject every optional
 * board peripheral that could otherwise claim or drive a fixture pin.
 */

#if defined(CONFIG_P2_EC32MB_GPIO) && \
    (P2_SDIO_FIXTURE_PIN(CONFIG_P2_EC32MB_GPIO_OUT_PIN) || \
     P2_SDIO_FIXTURE_PIN(CONFIG_P2_EC32MB_GPIO_IN_PIN))
#  error "native SD fixture P16-P23 overlaps configured GPIO"
#endif

#if defined(CONFIG_P2_EC32MB_UART1) && \
    (P2_SDIO_FIXTURE_PIN(CONFIG_P2_EC32MB_UART1_TX_PIN) || \
     P2_SDIO_FIXTURE_PIN(CONFIG_P2_EC32MB_UART1_RX_PIN))
#  error "native SD fixture P16-P23 overlaps configured UART1"
#endif

#if defined(CONFIG_P2_EC32MB_PWM) && \
    P2_SDIO_FIXTURE_PIN(CONFIG_P2_EC32MB_PWM_PIN)
#  error "native SD fixture P16-P23 overlaps configured PWM"
#endif

#if defined(CONFIG_P2_EC32MB_CAPTURE) && \
    P2_SDIO_FIXTURE_PIN(CONFIG_P2_EC32MB_CAPTURE_PIN)
#  error "native SD fixture P16-P23 overlaps configured capture"
#endif

#if defined(CONFIG_P2_EC32MB_ADC) && \
    P2_SDIO_FIXTURE_PIN(CONFIG_P2_EC32MB_ADC_PIN)
#  error "native SD fixture P16-P23 overlaps configured ADC"
#endif

#if defined(CONFIG_P2_EC32MB_DAC) && \
    P2_SDIO_FIXTURE_PIN(CONFIG_P2_EC32MB_DAC_PIN)
#  error "native SD fixture P16-P23 overlaps configured DAC"
#endif

#if defined(CONFIG_P2_EC32MB_SPI) && \
    (P2_SDIO_FIXTURE_PIN(CONFIG_P2_EC32MB_SPI_MOSI_PIN) || \
     P2_SDIO_FIXTURE_PIN(CONFIG_P2_EC32MB_SPI_MISO_PIN) || \
     P2_SDIO_FIXTURE_PIN(CONFIG_P2_EC32MB_SPI_SCK_PIN) || \
     P2_SDIO_FIXTURE_PIN(CONFIG_P2_EC32MB_SPI_CS_PIN))
#  error "native SD fixture P16-P23 overlaps configured SPI"
#endif

#if defined(CONFIG_P2_EC32MB_I2C) && \
    (P2_SDIO_FIXTURE_PIN(CONFIG_P2_EC32MB_I2C_SDA_PIN) || \
     P2_SDIO_FIXTURE_PIN(CONFIG_P2_EC32MB_I2C_SCL_PIN))
#  error "native SD fixture P16-P23 overlaps configured I2C"
#endif

#ifndef CONFIG_P2_EC32MB_SDIO_COMMAND_HZ
#  define CONFIG_P2_EC32MB_SDIO_COMMAND_HZ 1000000
#endif

#ifndef CONFIG_P2_EC32MB_SDIO_DIVISOR
#  define CONFIG_P2_EC32MB_SDIO_DIVISOR 4
#endif

#ifndef CONFIG_P2_EC32MB_SDIO_RESPONSE_CLOCKS
#  define CONFIG_P2_EC32MB_SDIO_RESPONSE_CLOCKS 64
#endif

#ifndef CONFIG_P2_EC32MB_SDIO_START_CLOCKS
#  define CONFIG_P2_EC32MB_SDIO_START_CLOCKS 1000000
#endif

#ifndef CONFIG_P2_EC32MB_SDIO_BUSY_TIMEOUT_MS
#  define CONFIG_P2_EC32MB_SDIO_BUSY_TIMEOUT_MS 1000
#endif

#ifndef CONFIG_P2_EC32MB_SDIO_SERVICE_TIMEOUT_MS
#  define CONFIG_P2_EC32MB_SDIO_SERVICE_TIMEOUT_MS 1000
#endif

#ifndef CONFIG_P2_EC32MB_SDIO_MAX_TRANSFER
#  define CONFIG_P2_EC32MB_SDIO_MAX_TRANSFER 262144
#endif

#define P2_SDIO_MAX_CRC_BLOCKS \
  (CONFIG_P2_EC32MB_SDIO_MAX_TRANSFER / P2_SDIO_FAST_BLOCKLEN)

#if CONFIG_P2_EC32MB_SDIO_COMMAND_HZ > 5000000
#  error "native SD command bit-bang clock is limited to 5 MHz"
#endif

#if CONFIG_P2_EC32MB_SDIO_DIVISOR < 2 || \
    CONFIG_P2_EC32MB_SDIO_DIVISOR > 65535
#  error "native SD streamer divisor must be in the range 2..65535"
#endif

#if (CONFIG_P2_EC32MB_SDIO_MAX_TRANSFER % P2_SDIO_FAST_BLOCKLEN) != 0
#  error "native SD maximum transfer must be exactly 512-byte divisible"
#endif

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct p2_sdio_wire_s
{
  volatile uint32_t request;
  volatile uint32_t complete;
  volatile uint32_t operation;
  volatile uint32_t buffer;
  volatile uint32_t blocklen;
  volatile uint32_t nblocks;
  volatile uint32_t clock_x;
  volatile uint32_t xfrq;
  volatile uint32_t scan_half;
  volatile uint32_t start_limit;
  volatile int32_t status;
  volatile uint32_t bytes;
  volatile uint32_t ready;
  volatile uint32_t input_mode;
  volatile uint32_t rx_lag;
  volatile uint32_t crc_buffer;
  volatile uint32_t verify_crc;
};

struct p2_sdio_lower_s
{
  struct sdio_dev_s dev;
  mutex_t policy_lock;
  FAR uint8_t *buffer;
  size_t nbytes;
  uint32_t response_payload[4];
  uint32_t last_cmd;
  uint32_t half_cycles;
  uint32_t data_clock_hz;
  uint32_t command_clock_hz;
  uint32_t timeout_ms;
  uint32_t blocklen;
  uint32_t nblocks;
  uint32_t input_mode;
  uint64_t fast_bytes;
  uint32_t fast_requests;
  uint32_t fast_errors;
  int response_error;
  int service_error;
  uint8_t response_raw[17];
  uint8_t response_len;
  uint8_t service_cog;
  uint16_t active_divisor;
  uint8_t rx_lag;
  sdio_eventset_t waitset;
  bool transfer_armed;
  bool wide;
  bool high_speed;
  bool hs_attempted;
  bool phase_calibrated;
  bool service_failed;
  bool fast_crc16;
};

static_assert(sizeof(struct p2_sdio_wire_s) == P2_SDIO_WIRE_SIZE,
              "native SDIO mailbox layout changed");
static_assert(offsetof(struct p2_sdio_wire_s, complete) ==
              P2_SDIO_WIRE_COMPLETE_OFFSET,
              "native SDIO complete offset changed");
static_assert(offsetof(struct p2_sdio_wire_s, input_mode) ==
              P2_SDIO_WIRE_INPUT_MODE_OFFSET,
              "native SDIO input mode offset changed");
static_assert(offsetof(struct p2_sdio_wire_s, crc_buffer) ==
              P2_SDIO_WIRE_CRC_BUFFER_OFFSET,
              "native SDIO CRC buffer offset changed");
static_assert(offsetof(struct p2_sdio_wire_s, verify_crc) ==
              P2_SDIO_WIRE_VERIFY_CRC_OFFSET,
              "native SDIO CRC policy offset changed");

/****************************************************************************
 * Private Function Prototypes
 ****************************************************************************/

static void p2_sdio_reset(FAR struct sdio_dev_s *dev);
static sdio_capset_t p2_sdio_capabilities(FAR struct sdio_dev_s *dev);
static sdio_statset_t p2_sdio_status(FAR struct sdio_dev_s *dev);
static void p2_sdio_widebus(FAR struct sdio_dev_s *dev, bool enable);
static void p2_sdio_clock(FAR struct sdio_dev_s *dev,
                          enum sdio_clock_e rate);
static int p2_sdio_attach(FAR struct sdio_dev_s *dev);
static int p2_sdio_sendcmd(FAR struct sdio_dev_s *dev, uint32_t cmd,
                           uint32_t arg);
#ifdef CONFIG_SDIO_BLOCKSETUP
static void p2_sdio_blocksetup(FAR struct sdio_dev_s *dev,
                               unsigned int blocklen,
                               unsigned int nblocks);
#endif
static int p2_sdio_recvsetup(FAR struct sdio_dev_s *dev,
                             FAR uint8_t *buffer, size_t nbytes);
static int p2_sdio_sendsetup(FAR struct sdio_dev_s *dev,
                             FAR const uint8_t *buffer, size_t nbytes);
static int p2_sdio_cancel(FAR struct sdio_dev_s *dev);
static int p2_sdio_waitresponse(FAR struct sdio_dev_s *dev, uint32_t cmd);
static int p2_sdio_recv_r1(FAR struct sdio_dev_s *dev, uint32_t cmd,
                           FAR uint32_t *r1);
static int p2_sdio_recv_r2(FAR struct sdio_dev_s *dev, uint32_t cmd,
                           FAR uint32_t r2[4]);
static int p2_sdio_recv_r3(FAR struct sdio_dev_s *dev, uint32_t cmd,
                           FAR uint32_t *r3);
static int p2_sdio_recv_r4(FAR struct sdio_dev_s *dev, uint32_t cmd,
                           FAR uint32_t *r4);
static int p2_sdio_recv_r5(FAR struct sdio_dev_s *dev, uint32_t cmd,
                           FAR uint32_t *r5);
static int p2_sdio_recv_r6(FAR struct sdio_dev_s *dev, uint32_t cmd,
                           FAR uint32_t *r6);
static int p2_sdio_recv_r7(FAR struct sdio_dev_s *dev, uint32_t cmd,
                           FAR uint32_t *r7);
static void p2_sdio_waitenable(FAR struct sdio_dev_s *dev,
                               sdio_eventset_t eventset, uint32_t timeout);
static sdio_eventset_t p2_sdio_eventwait(FAR struct sdio_dev_s *dev);
static void p2_sdio_callbackenable(FAR struct sdio_dev_s *dev,
                                   sdio_eventset_t eventset);
#if defined(CONFIG_SCHED_WORKQUEUE) && defined(CONFIG_SCHED_HPWORK)
static int p2_sdio_registercallback(FAR struct sdio_dev_s *dev,
                                    worker_t callback, FAR void *arg);
#endif
static int p2_sdio_start_service(FAR struct p2_sdio_lower_s *priv);

/****************************************************************************
 * Public Data
 ****************************************************************************/

/* The assembly service resolves this symbol directly. */

volatile struct p2_sdio_wire_s g_p2_sdio_wire
  aligned_data(4);

/****************************************************************************
 * Private Data
 ****************************************************************************/

static struct p2_sdio_lower_s g_p2_sdio =
{
  .dev =
  {
    .mutex = NXMUTEX_INITIALIZER,
    .reset = p2_sdio_reset,
    .capabilities = p2_sdio_capabilities,
    .status = p2_sdio_status,
    .widebus = p2_sdio_widebus,
    .clock = p2_sdio_clock,
    .attach = p2_sdio_attach,
    .sendcmd = p2_sdio_sendcmd,
#ifdef CONFIG_SDIO_BLOCKSETUP
    .blocksetup = p2_sdio_blocksetup,
#endif
    .recvsetup = p2_sdio_recvsetup,
    .sendsetup = p2_sdio_sendsetup,
    .cancel = p2_sdio_cancel,
    .waitresponse = p2_sdio_waitresponse,
    .recv_r1 = p2_sdio_recv_r1,
    .recv_r2 = p2_sdio_recv_r2,
    .recv_r3 = p2_sdio_recv_r3,
    .recv_r4 = p2_sdio_recv_r4,
    .recv_r5 = p2_sdio_recv_r5,
    .recv_r6 = p2_sdio_recv_r6,
    .recv_r7 = p2_sdio_recv_r7,
    .waitenable = p2_sdio_waitenable,
    .eventwait = p2_sdio_eventwait,
    .callbackenable = p2_sdio_callbackenable,
#if defined(CONFIG_SCHED_WORKQUEUE) && defined(CONFIG_SCHED_HPWORK)
    .registercallback = p2_sdio_registercallback,
#endif
    .gotextcsd = NULL,
  },
  .policy_lock = NXMUTEX_INITIALIZER,
  .half_cycles = 1,
  .service_cog = P2_SDIO_COG_NONE,
  .active_divisor = CONFIG_P2_EC32MB_SDIO_DIVISOR,
  .input_mode = P2_SDIO_INPUT_ASYNC,
#ifdef CONFIG_P2_EC32MB_SDIO_VERIFY_FAST_CRC16
  .fast_crc16 = true,
#else
  .fast_crc16 = false,
#endif
};

static uint16_t g_p2_sdio_fast_crc[P2_SDIO_MAX_CRC_BLOCKS][4]
  aligned_data(4);
static uint16_t g_p2_sdio_crc16_table[256];

/****************************************************************************
 * External Symbols
 ****************************************************************************/

extern uint32_t p2_sdio_service_start(void);

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static inline FAR struct p2_sdio_lower_s *p2_sdio_priv(
  FAR struct sdio_dev_s *dev)
{
  return (FAR struct p2_sdio_lower_s *)dev;
}

static inline void p2_sdio_barrier(void)
{
  __asm__ __volatile__("" : : : "memory");
}

static inline uint32_t p2_sdio_counter(void)
{
  uint32_t value;

  __asm__ __volatile__("getct %0" : "=r" (value));
  return value;
}

static uint32_t p2_sdio_timeout_cycles(uint32_t timeout_ms)
{
  uint64_t cycles;

  if (timeout_ms == 0 ||
      timeout_ms > CONFIG_P2_EC32MB_SDIO_SERVICE_TIMEOUT_MS)
    {
      timeout_ms = CONFIG_P2_EC32MB_SDIO_SERVICE_TIMEOUT_MS;
    }

  cycles = (uint64_t)CONFIG_P2_SYSCLK_HZ * timeout_ms / 1000u;
  if (cycles == 0)
    {
      cycles = 1;
    }

  return cycles > INT32_MAX ? INT32_MAX : (uint32_t)cycles;
}

static inline void p2_sdio_wait(uint32_t clocks)
{
  __asm__ __volatile__("waitx %0" : : "r" (clocks));
}

static inline void p2_sdio_pin_high(unsigned int pin)
{
  __asm__ __volatile__("drvh %0" : : "ri" (pin));
}

static inline void p2_sdio_pin_low(unsigned int pin)
{
  __asm__ __volatile__("drvl %0" : : "ri" (pin));
}

static inline void p2_sdio_pin_input(unsigned int pin)
{
  __asm__ __volatile__("dirl %0" : : "ri" (pin));
}

static inline void p2_sdio_pin_output_low(unsigned int pin)
{
  __asm__ __volatile__("outl %0" : : "ri" (pin));
}

static inline void p2_sdio_pin_mode(unsigned int pin, uint32_t mode)
{
  __asm__ __volatile__("wrpin %0, %1" : : "r" (mode), "ri" (pin));
}

static inline uint32_t p2_sdio_data_pins(bool wide)
{
  uint32_t pins;

  __asm__ __volatile__("mov %0, ina" : "=r" (pins));
  pins >>= P2_SDIO_DAT0_PIN;
  return pins & (wide ? 0x0fu : 0x01u);
}

static void p2_sdio_pullup(unsigned int pin)
{
  /* This weak pull-up is a safety net, not a substitute for the fixture's
   * external CMD/DAT pull-ups at record clock rates.
   */

  p2_sdio_pin_input(pin);
  p2_sdio_pin_mode(pin, P2_SDIO_PULLUP_MODE);
  p2_sdio_pin_high(pin);
}

static void p2_sdio_cmd_output(void)
{
  p2_sdio_pin_input(P2_SDIO_CMD_PIN);
  p2_sdio_pin_mode(P2_SDIO_CMD_PIN, 0);
  p2_sdio_pin_high(P2_SDIO_CMD_PIN);
}

static void p2_sdio_clock_low(void)
{
  p2_sdio_pin_input(P2_SDIO_CLK_PIN);
  p2_sdio_pin_mode(P2_SDIO_CLK_PIN, 0);
  p2_sdio_pin_low(P2_SDIO_CLK_PIN);
}

static void p2_sdio_clock_release(void)
{
  /* The command engine and the capture service execute in different COGs.
   * Clear this COG's DIR contribution before publishing a service request so
   * the service can actually disable P21 while configuring its Smart Pin.
   * OUT remains latched low for a no-edge handoff.
   */

  p2_sdio_pin_output_low(P2_SDIO_CLK_PIN);
  p2_sdio_pin_input(P2_SDIO_CLK_PIN);
  p2_sdio_pin_mode(P2_SDIO_CLK_PIN, 0);
}

static void p2_sdio_force_safe(void)
{
  unsigned int pin;

  p2_sdio_clock_low();
  p2_sdio_pullup(P2_SDIO_CMD_PIN);
  for (pin = P2_SDIO_DAT0_PIN; pin <= P2_SDIO_DAT3_PIN; pin++)
    {
      p2_sdio_pullup(pin);
    }

  p2_sdio_pin_input(P2_SDIO_ACTIVITY_PIN);
}

static void p2_sdio_force_powerdown(void)
{
  unsigned int pin;

  /* Do not let an always-powered P2 I/O bank back-power an unpowered card.
   * Hold every connected signal low before disabling the fixture supply.
   * External CMD/DAT pull-ups must be powered from that switched supply.
   */

  p2_sdio_clock_low();
  p2_sdio_pin_mode(P2_SDIO_CMD_PIN, 0);
  p2_sdio_pin_low(P2_SDIO_CMD_PIN);
  for (pin = P2_SDIO_DAT0_PIN; pin <= P2_SDIO_DAT3_PIN; pin++)
    {
      p2_sdio_pin_mode(pin, 0);
      p2_sdio_pin_low(pin);
    }

  p2_sdio_pin_input(P2_SDIO_ACTIVITY_PIN);
}

static uint8_t p2_sdio_crc7(FAR const uint8_t *data, size_t nbytes)
{
  uint8_t crc = 0;
  size_t byte;
  unsigned int bit;

  for (byte = 0; byte < nbytes; byte++)
    {
      uint8_t value = data[byte];

      for (bit = 0; bit < 8; bit++)
        {
          crc <<= 1;
          if (((crc ^ value) & 0x80u) != 0)
            {
              crc ^= 0x09u;
            }

          value <<= 1;
        }
    }

  return crc & 0x7fu;
}

static uint16_t p2_sdio_crc16_bit(uint16_t crc, bool bit)
{
  bool feedback = ((crc & 0x8000u) != 0) ^ bit;

  crc <<= 1;
  if (feedback)
    {
      crc ^= 0x1021u;
    }

  return crc;
}

static void p2_sdio_set_command_clock(FAR struct p2_sdio_lower_s *priv,
                                      uint32_t frequency)
{
  uint32_t half;

  if (frequency == 0)
    {
      frequency = 1;
    }

  half = CONFIG_P2_SYSCLK_HZ / (frequency * 2u);
  if (half == 0)
    {
      half = 1;
    }

  priv->half_cycles = half;
  priv->command_clock_hz = CONFIG_P2_SYSCLK_HZ / (half * 2u);
}

static bool p2_sdio_clock_sample_cmd(FAR struct p2_sdio_lower_s *priv)
{
  int cmd_high;

  p2_sdio_pin_high(P2_SDIO_CLK_PIN);
  p2_sdio_wait(priv->half_cycles);
  __asm__ __volatile__("testp %1 wc\n\twrc %0"
                       : "=r" (cmd_high)
                       : "ri" (P2_SDIO_CMD_PIN));

  p2_sdio_pin_low(P2_SDIO_CLK_PIN);
  p2_sdio_wait(priv->half_cycles);
  return cmd_high != 0;
}

static uint32_t p2_sdio_clock_sample_data(FAR struct p2_sdio_lower_s *priv,
                                          bool wide)
{
  uint32_t value;

  p2_sdio_pin_high(P2_SDIO_CLK_PIN);
  p2_sdio_wait(priv->half_cycles);
  value = p2_sdio_data_pins(wide);
  p2_sdio_pin_low(P2_SDIO_CLK_PIN);
  p2_sdio_wait(priv->half_cycles);
  return value;
}

static void p2_sdio_clock_idle(FAR struct p2_sdio_lower_s *priv,
                               uint32_t clocks)
{
  while (clocks-- > 0)
    {
      p2_sdio_pin_high(P2_SDIO_CLK_PIN);
      p2_sdio_wait(priv->half_cycles);
      p2_sdio_pin_low(P2_SDIO_CLK_PIN);
      p2_sdio_wait(priv->half_cycles);
    }
}

static int p2_sdio_receive_response(FAR struct p2_sdio_lower_s *priv,
                                    uint32_t cmd)
{
  uint32_t response = cmd & MMCSD_RESPONSE_MASK;
  uint8_t expected = cmd & MMCSD_CMDIDX_MASK;
  unsigned int clocks;
  unsigned int bit;
  unsigned int len;
  bool crc_required;

  if (response == MMCSD_NO_RESPONSE)
    {
      priv->response_len = 0;
      p2_sdio_clock_idle(priv, 8);
      return OK;
    }

  len = response == MMCSD_R2_RESPONSE ? 17 : 6;
  memset(priv->response_raw, 0, sizeof(priv->response_raw));

  for (clocks = 0; clocks < CONFIG_P2_EC32MB_SDIO_RESPONSE_CLOCKS;
       clocks++)
    {
      if (!p2_sdio_clock_sample_cmd(priv))
        {
          break;
        }
    }

  if (clocks == CONFIG_P2_EC32MB_SDIO_RESPONSE_CLOCKS)
    {
      return -ETIMEDOUT;
    }

  /* The start bit was consumed by the bounded search. */

  for (bit = 1; bit < len * 8u; bit++)
    {
      unsigned int byte = bit >> 3;

      priv->response_raw[byte] <<= 1;
      if (p2_sdio_clock_sample_cmd(priv))
        {
          priv->response_raw[byte] |= 1u;
        }
    }

  priv->response_len = len;
  if ((priv->response_raw[0] & 0xc0u) != 0 ||
      (priv->response_raw[len - 1] & 1u) == 0)
    {
      return -EIO;
    }

  if (response != MMCSD_R2_RESPONSE && response != MMCSD_R3_RESPONSE &&
      response != MMCSD_R4_RESPONSE &&
      (priv->response_raw[0] & 0x3fu) != expected)
    {
      return -EIO;
    }

  crc_required = response != MMCSD_R3_RESPONSE &&
                 response != MMCSD_R4_RESPONSE;
  if (crc_required &&
      (response == MMCSD_R2_RESPONSE ?
       p2_sdio_crc7(&priv->response_raw[1], len - 2) :
       p2_sdio_crc7(priv->response_raw, len - 1)) !=
      ((priv->response_raw[len - 1] >> 1) & 0x7fu))
    {
      return -EILSEQ;
    }

  if (response == MMCSD_R2_RESPONSE)
    {
      priv->response_payload[0] =
        ((uint32_t)priv->response_raw[1] << 24) |
        ((uint32_t)priv->response_raw[2] << 16) |
        ((uint32_t)priv->response_raw[3] << 8) |
        priv->response_raw[4];
      priv->response_payload[1] =
        ((uint32_t)priv->response_raw[5] << 24) |
        ((uint32_t)priv->response_raw[6] << 16) |
        ((uint32_t)priv->response_raw[7] << 8) |
        priv->response_raw[8];
      priv->response_payload[2] =
        ((uint32_t)priv->response_raw[9] << 24) |
        ((uint32_t)priv->response_raw[10] << 16) |
        ((uint32_t)priv->response_raw[11] << 8) |
        priv->response_raw[12];
      priv->response_payload[3] =
        ((uint32_t)priv->response_raw[13] << 24) |
        ((uint32_t)priv->response_raw[14] << 16) |
        ((uint32_t)priv->response_raw[15] << 8);
    }
  else
    {
      priv->response_payload[0] =
        ((uint32_t)priv->response_raw[1] << 24) |
        ((uint32_t)priv->response_raw[2] << 16) |
        ((uint32_t)priv->response_raw[3] << 8) |
        priv->response_raw[4];
    }

  if (response == MMCSD_R1B_RESPONSE)
    {
      uint64_t limit = (uint64_t)priv->command_clock_hz *
                       CONFIG_P2_EC32MB_SDIO_BUSY_TIMEOUT_MS / 1000u;

      if (limit == 0)
        {
          limit = 1;
        }

      if (limit > UINT32_MAX)
        {
          limit = UINT32_MAX;
        }

      for (clocks = 0; clocks < (uint32_t)limit; clocks++)
        {
          if ((p2_sdio_clock_sample_data(priv, false) & 1u) != 0)
            {
              return OK;
            }
        }

      return -ETIMEDOUT;
    }

  return OK;
}

static int p2_sdio_issue_command(FAR struct p2_sdio_lower_s *priv,
                                 uint32_t cmd, uint32_t arg)
{
  uint8_t frame[6];
  unsigned int byte;
  unsigned int bit;
  int ret;

  frame[0] = 0x40u | (cmd & MMCSD_CMDIDX_MASK);
  frame[1] = arg >> 24;
  frame[2] = arg >> 16;
  frame[3] = arg >> 8;
  frame[4] = arg;
  frame[5] = (p2_sdio_crc7(frame, 5) << 1) | 1u;

  p2_sdio_clock_low();
  p2_sdio_cmd_output();
  for (byte = 0; byte < sizeof(frame); byte++)
    {
      uint8_t value = frame[byte];

      for (bit = 0; bit < 8; bit++)
        {
          if ((value & 0x80u) != 0)
            {
              p2_sdio_pin_high(P2_SDIO_CMD_PIN);
            }
          else
            {
              p2_sdio_pin_low(P2_SDIO_CMD_PIN);
            }

          p2_sdio_wait(priv->half_cycles);
          p2_sdio_pin_high(P2_SDIO_CLK_PIN);
          p2_sdio_wait(priv->half_cycles);
          p2_sdio_pin_low(P2_SDIO_CLK_PIN);
          value <<= 1;
        }
    }

  p2_sdio_pullup(P2_SDIO_CMD_PIN);
  ret = p2_sdio_receive_response(priv, cmd);
  return ret;
}

static int p2_sdio_read_manual(FAR struct p2_sdio_lower_s *priv,
                               FAR uint8_t *buffer, uint32_t blocklen,
                               uint32_t nblocks, bool wide,
                               uint32_t timeout_ms)
{
  uint16_t crc[4];
  uint16_t received[4];
  uint32_t block;
  uint32_t offset = 0;
  uint32_t sample;
  uint32_t scans;
  unsigned int lane;
  unsigned int bit;
  uint32_t deadline = p2_sdio_counter() +
                      p2_sdio_timeout_cycles(timeout_ms);

  for (block = 0; block < nblocks; block++)
    {
      for (scans = 0; scans < CONFIG_P2_EC32MB_SDIO_START_CLOCKS; scans++)
        {
          sample = p2_sdio_clock_sample_data(priv, wide);
          if (sample == 0)
            {
              break;
            }

          if ((int32_t)(p2_sdio_counter() - deadline) >= 0)
            {
              return -ETIMEDOUT;
            }
        }

      if (scans == CONFIG_P2_EC32MB_SDIO_START_CLOCKS)
        {
          return -ETIMEDOUT;
        }

      memset(crc, 0, sizeof(crc));
      for (uint32_t byte = 0; byte < blocklen; byte++)
        {
          uint8_t value = 0;

          if (wide)
            {
              sample = p2_sdio_clock_sample_data(priv, true);
              value = sample << 4;
              for (lane = 0; lane < 4; lane++)
                {
                  crc[lane] = p2_sdio_crc16_bit(crc[lane],
                                                (sample & (1u << lane)) !=
                                                0);
                }

              sample = p2_sdio_clock_sample_data(priv, true);
              value |= sample;
              for (lane = 0; lane < 4; lane++)
                {
                  crc[lane] = p2_sdio_crc16_bit(crc[lane],
                                                (sample & (1u << lane)) !=
                                                0);
                }
            }
          else
            {
              for (bit = 0; bit < 8; bit++)
                {
                  sample = p2_sdio_clock_sample_data(priv, false);
                  value = (value << 1) | sample;
                  crc[0] = p2_sdio_crc16_bit(crc[0], sample != 0);
                }
            }

          buffer[offset++] = value;
          if ((int32_t)(p2_sdio_counter() - deadline) >= 0)
            {
              return -ETIMEDOUT;
            }
        }

      memset(received, 0, sizeof(received));
      for (bit = 0; bit < 16; bit++)
        {
          sample = p2_sdio_clock_sample_data(priv, wide);
          for (lane = 0; lane < (wide ? 4u : 1u); lane++)
            {
              received[lane] = (received[lane] << 1) |
                               ((sample >> lane) & 1u);
            }
        }

      for (lane = 0; lane < (wide ? 4u : 1u); lane++)
        {
          if (received[lane] != crc[lane])
            {
              return -EILSEQ;
            }
        }

      sample = p2_sdio_clock_sample_data(priv, wide);
      if (sample != (wide ? 0x0fu : 1u))
        {
          return -EIO;
        }
    }

  return OK;
}

static void p2_sdio_stop_service(FAR struct p2_sdio_lower_s *priv)
{
  if (priv->service_cog < 8u)
    {
      __asm__ __volatile__("cogstop %0" : :
                           "r" ((uint32_t)priv->service_cog));
    }

  priv->service_cog = P2_SDIO_COG_NONE;
  priv->service_failed = true;
  priv->phase_calibrated = false;
  priv->data_clock_hz = 0;
  p2_sdio_force_safe();
}

static int p2_sdio_restart_service(FAR struct p2_sdio_lower_s *priv)
{
  int ret;

  p2_sdio_stop_service(priv);
  ret = p2_sdio_start_service(priv);
  if (ret < 0)
    {
      return ret;
    }

  /* A replacement COG has not inherited proof of the failed stream.  Keep
   * subsequent reads on the CRC-verified slow path until a full reset and
   * phase calibration, and expose that live rate in telemetry.
   */

  priv->active_divisor = priv->half_cycles * 2u;
  priv->data_clock_hz = priv->command_clock_hz;

  /* p2_sdio_start_service() only clears the fail-closed latch after the
   * replacement COG has published READY.  A timed-out COG must never be
   * treated as usable merely because COGINIT returned a cog number.
   */

  return OK;
}

static int p2_sdio_abort_data(FAR struct p2_sdio_lower_s *priv)
{
  int ret;

  ret = p2_sdio_issue_command(priv, MMCSD_CMD12, 0);
  if (ret == OK &&
      (priv->response_payload[0] & P2_SDIO_R1_ERROR_MASK) != 0)
    {
      ret = -EIO;
    }

  if (ret < 0)
    {
      /* Without a verified CMD12/R1b the card may still be in Sending-data
       * state.  Do not permit another record read against stale card state.
       * A full reprobe/power-cycle is required to recover this instance.
       */

      priv->service_error = ret;
      p2_sdio_stop_service(priv);
    }

  return ret;
}

static void p2_sdio_crc16_table_initialize(void)
{
  unsigned int value;
  unsigned int bit;

  for (value = 0; value < 256; value++)
    {
      uint16_t crc = (uint16_t)value << 8;

      for (bit = 0; bit < 8; bit++)
        {
          crc = (crc & 0x8000u) != 0 ? (crc << 1) ^ 0x1021u : crc << 1;
        }

      g_p2_sdio_crc16_table[value] = crc;
    }
}

static int p2_sdio_validate_fast_crc(FAR const uint8_t *buffer,
                                     uint32_t blocklen, uint32_t nblocks)
{
  uint32_t block;

  for (block = 0; block < nblocks; block++)
    {
      FAR const uint8_t *data = buffer + (size_t)block * blocklen;
      uint16_t crc[4];
      uint32_t offset = 0;
      unsigned int lane;

      memset(crc, 0, sizeof(crc));
      while (offset + 4u <= blocklen)
        {
          uint8_t lane_byte[4];
          unsigned int byte;

          memset(lane_byte, 0, sizeof(lane_byte));
          for (byte = 0; byte < 4; byte++)
            {
              uint8_t value = data[offset++];
              uint8_t high = value >> 4;
              uint8_t low = value & 0x0fu;

              for (lane = 0; lane < 4; lane++)
                {
                  lane_byte[lane] =
                    (lane_byte[lane] << 2) |
                    (((high >> lane) & 1u) << 1) |
                    ((low >> lane) & 1u);
                }
            }

          for (lane = 0; lane < 4; lane++)
            {
              uint8_t index = (crc[lane] >> 8) ^ lane_byte[lane];

              crc[lane] = (crc[lane] << 8) ^
                          g_p2_sdio_crc16_table[index];
            }
        }

      while (offset < blocklen)
        {
          uint8_t value = data[offset++];
          uint8_t high = value >> 4;
          uint8_t low = value & 0x0fu;

          for (lane = 0; lane < 4; lane++)
            {
              crc[lane] = p2_sdio_crc16_bit(crc[lane],
                                            ((high >> lane) & 1u) != 0);
              crc[lane] = p2_sdio_crc16_bit(crc[lane],
                                            ((low >> lane) & 1u) != 0);
            }
        }

      for (lane = 0; lane < 4; lane++)
        {
          if (crc[lane] != g_p2_sdio_fast_crc[block][lane])
            {
              return -EILSEQ;
            }
        }
    }

  return OK;
}

static int p2_sdio_read_service(FAR struct p2_sdio_lower_s *priv,
                                FAR uint8_t *buffer, uint32_t blocklen,
                                uint32_t nblocks, size_t nbytes,
                                uint32_t timeout_ms, bool account,
                                bool verify_crc)
{
  uint32_t divisor = priv->active_divisor;
  uint32_t sequence;
  uint32_t deadline;
  uint32_t timeout_cycles;
  uint64_t cycles;

  if (priv->service_failed || priv->service_cog >= 8u ||
      nblocks > P2_SDIO_MAX_CRC_BLOCKS)
    {
      return priv->service_error < 0 ? priv->service_error : -EIO;
    }

  g_p2_sdio_wire.operation = P2_SDIO_WIRE_OP_READ_BLOCKS;
  g_p2_sdio_wire.buffer = (uintptr_t)buffer;
  g_p2_sdio_wire.blocklen = blocklen;
  g_p2_sdio_wire.nblocks = nblocks;
  g_p2_sdio_wire.clock_x = ((divisor / 2u) << 16) | divisor;
  g_p2_sdio_wire.xfrq =
    (uint32_t)((UINT64_C(0x80000000) + divisor - 1u) / divisor);
  g_p2_sdio_wire.scan_half = divisor / 2u;
  if (g_p2_sdio_wire.scan_half == 0)
    {
      g_p2_sdio_wire.scan_half = 1;
    }

  g_p2_sdio_wire.start_limit = CONFIG_P2_EC32MB_SDIO_START_CLOCKS;
  g_p2_sdio_wire.input_mode = priv->input_mode;
  g_p2_sdio_wire.rx_lag = priv->rx_lag;
  g_p2_sdio_wire.crc_buffer = (uintptr_t)g_p2_sdio_fast_crc;
  g_p2_sdio_wire.verify_crc = verify_crc;
  g_p2_sdio_wire.status = -EINPROGRESS;
  g_p2_sdio_wire.bytes = 0;
  sequence = g_p2_sdio_wire.request + 1u;
  if (sequence == 0)
    {
      sequence = 1;
    }

  p2_sdio_clock_release();
  p2_sdio_barrier();
  g_p2_sdio_wire.request = sequence;
  p2_sdio_barrier();

  cycles = p2_sdio_timeout_cycles(timeout_ms);
  timeout_cycles = (uint32_t)cycles;
  deadline = p2_sdio_counter() + timeout_cycles;
  while (g_p2_sdio_wire.complete != sequence)
    {
      if ((int32_t)(p2_sdio_counter() - deadline) >= 0)
        {
          int restart_ret;

          priv->service_error = -ETIMEDOUT;
          if (account)
            {
              priv->fast_errors++;
            }

          restart_ret = p2_sdio_restart_service(priv);
          return restart_ret < 0 ? restart_ret : -ETIMEDOUT;
        }
    }

  p2_sdio_barrier();
  p2_sdio_clock_low();
  if (g_p2_sdio_wire.status < 0 ||
      g_p2_sdio_wire.bytes != nbytes)
    {
      priv->service_error = g_p2_sdio_wire.status < 0 ?
                            g_p2_sdio_wire.status : -EIO;
      if (account)
        {
          priv->fast_errors++;
        }

      return priv->service_error;
    }

  if (verify_crc)
    {
      priv->service_error =
        p2_sdio_validate_fast_crc(buffer, blocklen, nblocks);
      if (priv->service_error < 0)
        {
          if (account)
            {
              priv->fast_errors++;
            }

          return priv->service_error;
        }
    }

  priv->service_error = OK;

  if (account)
    {
      priv->fast_requests++;
      priv->fast_bytes += nbytes;
    }

  return OK;
}

static int p2_sdio_phase_read(FAR struct p2_sdio_lower_s *priv,
                              FAR uint8_t response[64])
{
  int ret;

  ret = p2_sdio_issue_command(priv, P2_SDIO_CMD6,
                              P2_SDIO_CMD6_CHECK_HS);
  if (ret < 0)
    {
      /* CMD6 may have entered Sending-data even though its R1 was damaged.
       * A single-block switch-status transfer has no reliable CMD12 escape;
       * fail closed until the board-level power-cycle/reprobe path runs.
       */

      priv->service_error = ret;
      p2_sdio_stop_service(priv);
      return ret;
    }

  if ((priv->response_payload[0] & P2_SDIO_R1_ERROR_MASK) != 0)
    {
      ret = -EIO;
    }

  if (ret < 0)
    {
      return ret;
    }

  /* Calibration always checks all four native CRC16 lanes, even when the
   * timed record profile explicitly disables per-request CRC calculation.
   */

  ret = p2_sdio_read_service(
    priv, response, 64, 1, 64,
    CONFIG_P2_EC32MB_SDIO_SERVICE_TIMEOUT_MS, false, true);
  if (ret == -ETIMEDOUT)
    {
      priv->service_error = ret;
      p2_sdio_stop_service(priv);
    }

  return ret;
}

static int p2_sdio_calibrate_phase(FAR struct p2_sdio_lower_s *priv)
{
  static const uint32_t modes[2] =
  {
    P2_SDIO_INPUT_ASYNC,
    P2_SDIO_INPUT_SYNC,
  };

  uint8_t first[64] aligned_data(4);
  uint8_t second[64] aligned_data(4);
  bool stable[2][4];
  uint32_t saved_payload[4];
  uint32_t saved_cmd = priv->last_cmd;
  int saved_error = priv->response_error;
  unsigned int max_lag = priv->active_divisor - 1u;
  unsigned int best_length = 0;
  unsigned int best_mode = 0;
  unsigned int best_start = 0;
  unsigned int mode;
  unsigned int lag;
  int ret = -EILSEQ;

  if (max_lag > 3u)
    {
      max_lag = 3u;
    }

  memset(stable, 0, sizeof(stable));
  memcpy(saved_payload, priv->response_payload, sizeof(saved_payload));
  priv->phase_calibrated = false;
  for (mode = 0; mode < 2; mode++)
    {
      priv->input_mode = modes[mode];
      for (lag = 0; lag <= max_lag; lag++)
        {
          priv->rx_lag = lag;
          ret = p2_sdio_phase_read(priv, first);
          if (ret == OK)
            {
              ret = p2_sdio_phase_read(priv, second);
            }

          if (ret == OK && memcmp(first, second, sizeof(first)) == 0)
            {
              stable[mode][lag] = true;
            }

          if (priv->service_failed)
            {
              goto out;
            }
        }
    }

  /* Pick the midpoint of the widest consecutive CRC-valid eye.  Choosing
   * the first passing tap leaves no measured margin and is too fragile for
   * a record profile.  Ties intentionally retain the earlier async mode.
   */

  for (mode = 0; mode < 2; mode++)
    {
      lag = 0;
      while (lag <= max_lag)
        {
          unsigned int start;

          if (!stable[mode][lag])
            {
              lag++;
              continue;
            }

          start = lag;
          while (lag <= max_lag && stable[mode][lag])
            {
              lag++;
            }

          if (lag - start > best_length)
            {
              best_length = lag - start;
              best_mode = mode;
              best_start = start;
            }
        }
    }

  if (best_length > 0)
    {
      priv->input_mode = modes[best_mode];
      priv->rx_lag = best_start + (best_length - 1u) / 2u;
      priv->phase_calibrated = true;
      priv->service_error = OK;
    }

out:
  priv->last_cmd = saved_cmd;
  priv->response_error = saved_error;
  memcpy(priv->response_payload, saved_payload, sizeof(saved_payload));
  return priv->phase_calibrated ? OK : ret < 0 ? ret : -EILSEQ;
}

static int p2_sdio_switch_hs(FAR struct p2_sdio_lower_s *priv, bool set)
{
  uint32_t arg = set ? P2_SDIO_CMD6_SWITCH_HS : P2_SDIO_CMD6_CHECK_HS;
  uint32_t saved_cmd = priv->last_cmd;
  uint32_t saved_payload[4];
  uint8_t response[64] aligned_data(4);
  int saved_error = priv->response_error;
  int ret;

  memcpy(saved_payload, priv->response_payload, sizeof(saved_payload));
  ret = p2_sdio_issue_command(priv, P2_SDIO_CMD6, arg);
  if (ret < 0)
    {
      priv->service_error = ret;
      p2_sdio_stop_service(priv);
    }
  else if ((priv->response_payload[0] & P2_SDIO_R1_ERROR_MASK) != 0)
    {
      ret = -EIO;
    }

  if (ret == OK)
    {
      ret = p2_sdio_read_manual(priv, response, sizeof(response), 1,
                                priv->wide,
                                CONFIG_P2_EC32MB_SDIO_SERVICE_TIMEOUT_MS);
    }

  if (ret == OK && set)
    {
      /* SD High Speed may be used only after at least eight clocks following
       * the CMD6 switch-status end bit.  These run at the old command clock;
       * the caller cannot expose the new data rate until this function
       * returns success.
       */

      p2_sdio_clock_idle(priv, P2_SDIO_SWITCH_CLOCKS);
    }

  if (ret == -ETIMEDOUT)
    {
      priv->service_error = ret;
      p2_sdio_stop_service(priv);
    }

  priv->last_cmd = saved_cmd;
  priv->response_error = saved_error;
  memcpy(priv->response_payload, saved_payload, sizeof(saved_payload));

  if (ret < 0)
    {
      return ret;
    }

  if (set)
    {
      return (response[16] & 0x0fu) == 1u ? OK : -ENOTSUP;
    }

  return ((((uint16_t)response[12] << 8) | response[13]) & (1u << 1)) != 0 ?
         OK : -ENOTSUP;
}

static void p2_sdio_select_data_clock(FAR struct p2_sdio_lower_s *priv)
{
  uint32_t divisor = CONFIG_P2_EC32MB_SDIO_DIVISOR;
  uint32_t maximum = priv->high_speed ? P2_SDIO_HIGH_SPEED_MAX_HZ :
                                       P2_SDIO_DEFAULT_MAX_HZ;

#ifdef CONFIG_P2_EC32MB_SDIO_ALLOW_OVERCLOCK
  if (priv->high_speed)
    {
      maximum = UINT32_MAX;
    }
#endif

  if (maximum != UINT32_MAX && CONFIG_P2_SYSCLK_HZ / divisor > maximum)
    {
      divisor = (CONFIG_P2_SYSCLK_HZ + maximum - 1u) / maximum;
    }

  if (divisor < 2u)
    {
      divisor = 2u;
    }

  priv->active_divisor = divisor;
  priv->data_clock_hz = CONFIG_P2_SYSCLK_HZ / divisor;
}

static void p2_sdio_reset(FAR struct sdio_dev_s *dev)
{
  FAR struct p2_sdio_lower_s *priv = p2_sdio_priv(dev);

  priv->transfer_armed = false;
  priv->response_error = -ENODATA;
  priv->response_len = 0;
  priv->wide = false;
  priv->high_speed = false;
  priv->hs_attempted = false;
  priv->phase_calibrated = false;
  priv->active_divisor = CONFIG_P2_EC32MB_SDIO_DIVISOR;
  priv->input_mode = P2_SDIO_INPUT_ASYNC;
  priv->rx_lag = 0;
  p2_sdio_force_safe();
  if (priv->service_failed)
    {
      (void)p2_sdio_start_service(priv);
    }
}

static sdio_capset_t p2_sdio_capabilities(FAR struct sdio_dev_s *dev)
{
  (void)dev;
  return SDIO_CAPS_4BIT;
}

static sdio_statset_t p2_sdio_status(FAR struct sdio_dev_s *dev)
{
  (void)dev;

  /* The external record fixture has no card-detect input.  Read-only is a
   * deliberate driver property until a native write engine is implemented.
   */

  return SDIO_STATUS_PRESENT | SDIO_STATUS_WRPROTECTED;
}

static void p2_sdio_widebus(FAR struct sdio_dev_s *dev, bool enable)
{
  p2_sdio_priv(dev)->wide = enable;
}

static void p2_sdio_clock(FAR struct sdio_dev_s *dev,
                          enum sdio_clock_e rate)
{
  FAR struct p2_sdio_lower_s *priv = p2_sdio_priv(dev);

  switch (rate)
    {
      case CLOCK_SDIO_DISABLED:
        p2_sdio_clock_low();
        priv->data_clock_hz = 0;
        break;

      case CLOCK_IDMODE:
        p2_sdio_set_command_clock(priv, 400000u);
        priv->wide = false;
        priv->high_speed = false;
        priv->hs_attempted = false;
        p2_sdio_clock_low();
        p2_sdio_pullup(P2_SDIO_CMD_PIN);
        sched_lock();
        p2_sdio_clock_idle(priv, 80);
        sched_unlock();
        priv->data_clock_hz = priv->command_clock_hz;
        break;

      case CLOCK_SD_TRANSFER_4BIT:
        p2_sdio_set_command_clock(priv,
                                  CONFIG_P2_EC32MB_SDIO_COMMAND_HZ);
        if (priv->wide && !priv->hs_attempted)
          {
            priv->hs_attempted = true;
            if (p2_sdio_switch_hs(priv, false) == OK &&
                p2_sdio_switch_hs(priv, true) == OK)
              {
                priv->high_speed = true;
              }
          }

        p2_sdio_select_data_clock(priv);
        if (priv->data_clock_hz > P2_SDIO_DEFAULT_MAX_HZ &&
            (!priv->high_speed || p2_sdio_calibrate_phase(priv) < 0))
          {
            /* A high-rate request is never activated merely because it was
             * configured.  A successful SD CMD6 switch and two identical,
             * CRC16-valid switch-status captures at the candidate RX phase
             * are mandatory.  Failure closes down to the command/slow path.
             */

            priv->phase_calibrated = false;
          }

        if (!priv->phase_calibrated)
          {
            /* The fallback is the C bit-banged engine, whose live clock is
             * command_clock_hz.  Report that actual bus rate rather than a
             * dormant <=25-MHz streamer divisor.
             */

            priv->active_divisor = priv->half_cycles * 2u;
            priv->data_clock_hz = priv->command_clock_hz;
          }
        break;

      case CLOCK_SD_TRANSFER_1BIT:
      case CLOCK_MMC_TRANSFER:
      default:
        p2_sdio_set_command_clock(priv,
                                  CONFIG_P2_EC32MB_SDIO_COMMAND_HZ);
        priv->data_clock_hz = priv->command_clock_hz;
        break;
    }
}

static int p2_sdio_attach(FAR struct sdio_dev_s *dev)
{
  (void)dev;
  return OK;
}

static int p2_sdio_sendcmd(FAR struct sdio_dev_s *dev, uint32_t cmd,
                           uint32_t arg)
{
  FAR struct p2_sdio_lower_s *priv = p2_sdio_priv(dev);
  bool response_uncertain;
  int ret;

  if ((cmd & MMCSD_WRXFR) != 0)
    {
      return -EROFS;
    }

  if ((cmd & MMCSD_RDDATAXFR) != 0 &&
      (!priv->transfer_armed || priv->buffer == NULL))
    {
      return -EINVAL;
    }

  if (priv->service_failed)
    {
      return priv->service_error < 0 ? priv->service_error : -EIO;
    }

  priv->last_cmd = cmd;
  priv->response_error = -EINPROGRESS;
  ret = p2_sdio_issue_command(priv, cmd, arg);
  response_uncertain = ret < 0 && (cmd & MMCSD_RDDATAXFR) != 0;
  if (ret == OK &&
      (cmd & MMCSD_RDDATAXFR) != 0 &&
      (priv->response_payload[0] & P2_SDIO_R1_ERROR_MASK) != 0)
    {
      ret = -EIO;
    }

  priv->response_error = ret;

  /* A read-data command can enter Sending-data even if its response was
   * damaged on the wire.  The generic layer cancels immediately on this
   * error and supplies no data clocks.  CMD18 has a defined CMD12 recovery;
   * single-frame commands (CMD17, ACMD51, and CMD56) instead fail closed
   * until board-level power-cycle/reprobe.  Preserve the original response
   * error for the caller in either case.
   */

  if (response_uncertain)
    {
      if ((cmd & MMCSD_MULTIBLOCK) != 0)
        {
          (void)p2_sdio_abort_data(priv);
        }
      else
        {
          priv->service_error = ret;
          p2_sdio_stop_service(priv);
        }

      priv->response_error = ret;
    }

  return ret;
}

#ifdef CONFIG_SDIO_BLOCKSETUP
static void p2_sdio_blocksetup(FAR struct sdio_dev_s *dev,
                               unsigned int blocklen,
                               unsigned int nblocks)
{
  FAR struct p2_sdio_lower_s *priv = p2_sdio_priv(dev);

  priv->blocklen = blocklen;
  priv->nblocks = nblocks;
}
#endif

static int p2_sdio_recvsetup(FAR struct sdio_dev_s *dev,
                             FAR uint8_t *buffer, size_t nbytes)
{
  FAR struct p2_sdio_lower_s *priv = p2_sdio_priv(dev);

  /* The generic non-DMA MMC/SD path does not consume RECVSETUP's return
   * value.  Clear the armed state before every check so SENDCMD can refuse
   * CMD17/CMD18 instead of starting an unserviceable card transfer.
   */

  priv->buffer = NULL;
  priv->nbytes = 0;
  priv->transfer_armed = false;
  if (buffer == NULL || priv->blocklen == 0 || priv->nblocks == 0 ||
      priv->nblocks > SIZE_MAX / priv->blocklen ||
      nbytes != (size_t)priv->blocklen * priv->nblocks ||
      nbytes > CONFIG_P2_EC32MB_SDIO_MAX_TRANSFER)
    {
      return -EINVAL;
    }

  priv->buffer = buffer;
  priv->nbytes = nbytes;
  priv->transfer_armed = true;
  return OK;
}

static int p2_sdio_sendsetup(FAR struct sdio_dev_s *dev,
                             FAR const uint8_t *buffer, size_t nbytes)
{
  (void)dev;
  (void)buffer;
  (void)nbytes;
  return -EROFS;
}

static int p2_sdio_cancel(FAR struct sdio_dev_s *dev)
{
  FAR struct p2_sdio_lower_s *priv = p2_sdio_priv(dev);

  priv->transfer_armed = false;
  priv->buffer = NULL;
  priv->nbytes = 0;
  return OK;
}

static int p2_sdio_waitresponse(FAR struct sdio_dev_s *dev, uint32_t cmd)
{
  FAR struct p2_sdio_lower_s *priv = p2_sdio_priv(dev);

  return cmd == priv->last_cmd ? priv->response_error : -EINVAL;
}

static int p2_sdio_recv_short(FAR struct sdio_dev_s *dev, uint32_t cmd,
                              FAR uint32_t *response)
{
  FAR struct p2_sdio_lower_s *priv = p2_sdio_priv(dev);

  if (response == NULL || cmd != priv->last_cmd)
    {
      return -EINVAL;
    }

  if (priv->response_error < 0)
    {
      return priv->response_error;
    }

  *response = priv->response_payload[0];
  return OK;
}

static int p2_sdio_recv_r1(FAR struct sdio_dev_s *dev, uint32_t cmd,
                           FAR uint32_t *r1)
{
  return p2_sdio_recv_short(dev, cmd, r1);
}

static int p2_sdio_recv_r2(FAR struct sdio_dev_s *dev, uint32_t cmd,
                           FAR uint32_t r2[4])
{
  FAR struct p2_sdio_lower_s *priv = p2_sdio_priv(dev);

  if (r2 == NULL || cmd != priv->last_cmd)
    {
      return -EINVAL;
    }

  if (priv->response_error < 0)
    {
      return priv->response_error;
    }

  memcpy(r2, priv->response_payload, sizeof(priv->response_payload));
  return OK;
}

static int p2_sdio_recv_r3(FAR struct sdio_dev_s *dev, uint32_t cmd,
                           FAR uint32_t *r3)
{
  return p2_sdio_recv_short(dev, cmd, r3);
}

static int p2_sdio_recv_r4(FAR struct sdio_dev_s *dev, uint32_t cmd,
                           FAR uint32_t *r4)
{
  return p2_sdio_recv_short(dev, cmd, r4);
}

static int p2_sdio_recv_r5(FAR struct sdio_dev_s *dev, uint32_t cmd,
                           FAR uint32_t *r5)
{
  return p2_sdio_recv_short(dev, cmd, r5);
}

static int p2_sdio_recv_r6(FAR struct sdio_dev_s *dev, uint32_t cmd,
                           FAR uint32_t *r6)
{
  return p2_sdio_recv_short(dev, cmd, r6);
}

static int p2_sdio_recv_r7(FAR struct sdio_dev_s *dev, uint32_t cmd,
                           FAR uint32_t *r7)
{
  return p2_sdio_recv_short(dev, cmd, r7);
}

static void p2_sdio_waitenable(FAR struct sdio_dev_s *dev,
                               sdio_eventset_t eventset, uint32_t timeout)
{
  FAR struct p2_sdio_lower_s *priv = p2_sdio_priv(dev);

  priv->waitset = eventset;
  priv->timeout_ms = timeout;
}

static sdio_eventset_t p2_sdio_eventwait(FAR struct sdio_dev_s *dev)
{
  FAR struct p2_sdio_lower_s *priv = p2_sdio_priv(dev);
  sdio_eventset_t waitset = priv->waitset;
  sdio_eventset_t result;
  uint32_t timeout_ms;
  bool policy_locked = false;
  int ret;

  ret = nxmutex_lock(&priv->policy_lock);
  if (ret < 0)
    {
      goto recover;
    }

  policy_locked = true;

  if ((waitset & SDIOWAIT_TRANSFERDONE) == 0)
    {
      /* This read-only lower half only produces transfer-completion events.
       * Reject unsupported wait masks explicitly instead of returning an
       * event the caller did not enable.
       */

      ret = -ENOTSUP;
      goto recover;
    }

  if ((waitset & SDIOWAIT_TIMEOUT) != 0 && priv->timeout_ms == 0)
    {
      ret = -ETIMEDOUT;
      goto recover;
    }

  timeout_ms = (waitset & SDIOWAIT_TIMEOUT) != 0 ?
               priv->timeout_ms :
               CONFIG_P2_EC32MB_SDIO_SERVICE_TIMEOUT_MS;

  if (!priv->transfer_armed || priv->buffer == NULL)
    {
      ret = -EINVAL;
      goto recover;
    }

  if (priv->phase_calibrated && priv->wide &&
      (((uintptr_t)priv->buffer & 3u) == 0) &&
      priv->blocklen == P2_SDIO_FAST_BLOCKLEN &&
      priv->nblocks <= P2_SDIO_MAX_CRC_BLOCKS)
    {
      ret = p2_sdio_read_service(priv, priv->buffer, priv->blocklen,
                                 priv->nblocks, priv->nbytes,
                                 timeout_ms, true,
                                 priv->fast_crc16);
    }
  else
    {
      ret = p2_sdio_read_manual(priv, priv->buffer, priv->blocklen,
                                priv->nblocks, priv->wide,
                                timeout_ms);
    }

recover:
  /* mmcsd_readmultiple() returns immediately when eventwait reports an
   * error, before its usual CMD12.  A failed CMD18 would otherwise leave the
   * card transmitting indefinitely.  The bit-banged command engine remains
   * independent of the streamer, so abort here after a successful timeout
   * restart (or after any other receive/CRC failure).  If the replacement
   * COG did not start, preserve the permanent failure latch.
   */

  if (ret < 0 && (priv->last_cmd & MMCSD_RDDATAXFR) != 0 &&
      !priv->service_failed)
    {
      if ((priv->last_cmd & MMCSD_MULTIBLOCK) != 0)
        {
          int abort_ret = p2_sdio_abort_data(priv);

          if (abort_ret < 0)
            {
              ret = abort_ret;
            }
        }
      else
        {
          priv->service_error = ret;
          p2_sdio_stop_service(priv);
        }
    }

  if (ret == OK)
    {
      result = SDIOWAIT_TRANSFERDONE;
    }
  else if (ret == -ETIMEDOUT && (waitset & SDIOWAIT_TIMEOUT) != 0)
    {
      result = SDIOWAIT_TIMEOUT;
    }
  else
    {
      result = SDIOWAIT_ERROR;
    }

  priv->waitset = 0;
  priv->transfer_armed = false;
  priv->buffer = NULL;
  priv->nbytes = 0;
  if (policy_locked)
    {
      nxmutex_unlock(&priv->policy_lock);
    }

  return result;
}

static void p2_sdio_callbackenable(FAR struct sdio_dev_s *dev,
                                   sdio_eventset_t eventset)
{
  (void)dev;
  (void)eventset;
}

#if defined(CONFIG_SCHED_WORKQUEUE) && defined(CONFIG_SCHED_HPWORK)
static int p2_sdio_registercallback(FAR struct sdio_dev_s *dev,
                                    worker_t callback, FAR void *arg)
{
  (void)dev;
  (void)callback;
  (void)arg;

  /* The external record fixture has no card-detect input.  Do not claim a
   * callback registration that can never notify its caller.
   */

  return -ENOSYS;
}
#endif

static int p2_sdio_start_service(FAR struct p2_sdio_lower_s *priv)
{
  uint32_t deadline;
  uint32_t cog;

  memset((FAR void *)&g_p2_sdio_wire, 0, sizeof(g_p2_sdio_wire));
  p2_sdio_barrier();
  cog = p2_sdio_service_start();
  if (cog >= 8u)
    {
      priv->service_error = -EBUSY;
      priv->service_failed = true;
      return -EBUSY;
    }

  priv->service_cog = cog;
  deadline = p2_sdio_counter() + CONFIG_P2_SYSCLK_HZ / 10u;
  while (g_p2_sdio_wire.ready == 0)
    {
      if ((int32_t)(p2_sdio_counter() - deadline) >= 0)
        {
          priv->service_error = -ETIMEDOUT;
          p2_sdio_stop_service(priv);
          return -ETIMEDOUT;
        }
    }

  priv->service_error = OK;
  priv->service_failed = false;

  return OK;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int p2_sdio_native_initialize(void)
{
  FAR struct p2_sdio_lower_s *priv = &g_p2_sdio;
  struct stat statbuf;
  int ret;

  p2_sdio_crc16_table_initialize();
  p2_sdio_force_powerdown();
  p2_sdio_pin_mode(P2_SDIO_POWER_PIN, 0);
  p2_sdio_pin_low(P2_SDIO_POWER_PIN);
  up_udelay(P2_SDIO_POWER_OFF_USEC);
  p2_sdio_pin_high(P2_SDIO_POWER_PIN);
  up_udelay(P2_SDIO_POWER_STABLE_USEC);
  p2_sdio_force_safe();
  p2_sdio_reset(&priv->dev);

  ret = priv->service_cog < 8u ? OK : p2_sdio_start_service(priv);
  if (ret < 0)
    {
      return ret;
    }

  ret = mmcsd_slotinitialize(0, &priv->dev);
  if (ret == OK && (priv->service_failed || priv->service_cog >= 8u))
    {
      ret = priv->service_error < 0 ? priv->service_error : -EIO;
    }

  if (ret == OK &&
      (nx_stat("/dev/mmcsd0", &statbuf, 1) < 0 ||
       !S_ISBLK(statbuf.st_mode)))
    {
      ret = -ENODEV;
    }

  if (ret < 0)
    {
      p2_sdio_stop_service(priv);
    }

  return ret;
}

int p2_sdio_native_get_info(FAR struct p2_sdio_native_info_s *info)
{
  FAR struct p2_sdio_lower_s *priv = &g_p2_sdio;
  int ret;

  if (info == NULL)
    {
      return -EINVAL;
    }

  ret = nxmutex_lock(&priv->policy_lock);
  if (ret < 0)
    {
      return ret;
    }

  memset(info, 0, sizeof(*info));
  info->sysclk_hz = CONFIG_P2_SYSCLK_HZ;
  info->command_clock_hz = priv->command_clock_hz;
  info->requested_data_clock_hz =
    CONFIG_P2_SYSCLK_HZ / CONFIG_P2_EC32MB_SDIO_DIVISOR;
  info->data_clock_hz = priv->data_clock_hz;
  info->raw_bus_bytes_per_second = priv->data_clock_hz / 2u;
  info->fast_bytes = priv->fast_bytes;
  info->fast_requests = priv->fast_requests;
  info->fast_errors = priv->fast_errors;
  info->requested_divisor = CONFIG_P2_EC32MB_SDIO_DIVISOR;
  info->active_divisor = priv->active_divisor;
  info->service_cog = priv->service_cog;
  info->rx_lag = priv->rx_lag;
  info->wide_bus = priv->wide;
  info->high_speed = priv->high_speed;
  info->phase_calibrated = priv->phase_calibrated;
  info->input_synchronized = priv->input_mode == P2_SDIO_INPUT_SYNC;
  info->overclocked = priv->data_clock_hz >
                      (priv->high_speed ? P2_SDIO_HIGH_SPEED_MAX_HZ :
                                         P2_SDIO_DEFAULT_MAX_HZ);
  info->hil_required = info->requested_data_clock_hz >
                       P2_SDIO_DEFAULT_MAX_HZ;
  info->command_crc7_verified = true;
  info->fallback_crc16_verified = true;
  info->fast_crc16_verified = priv->fast_crc16;
  nxmutex_unlock(&priv->policy_lock);
  return OK;
}

int p2_sdio_native_set_fast_crc16(bool enable)
{
  FAR struct p2_sdio_lower_s *priv = &g_p2_sdio;
  int ret;

  ret = nxmutex_lock(&priv->policy_lock);
  if (ret < 0)
    {
      return ret;
    }

  priv->fast_crc16 = enable;
  nxmutex_unlock(&priv->policy_lock);
  return OK;
}
