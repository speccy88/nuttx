/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_pins.h
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

#ifndef __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_PINS_H
#define __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_PINS_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <stdint.h>

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_PIN_COUNT                 64
#define P2_PIN_COG_NONE              UINT8_MAX

/* WRPIN smart mode occupies bits 1..5.  Electrical controls are tracked
 * independently by enum p2_pin_drive_e.
 */

#define P2_SMARTPIN_MODE_DISABLED    0x00
#define P2_SMARTPIN_MODE_MAX         0x3e

/****************************************************************************
 * Public Types
 ****************************************************************************/

enum p2_pin_role_e
{
  P2_PIN_ROLE_NONE = 0,
  P2_PIN_ROLE_BOARD_LED,
  P2_PIN_ROLE_PSRAM,
  P2_PIN_ROLE_STORAGE,
  P2_PIN_ROLE_CONSOLE
};

enum p2_pin_owner_e
{
  P2_PIN_OWNER_NONE = 0,
  P2_PIN_OWNER_BOARD_LED,
  P2_PIN_OWNER_PSRAM,
  P2_PIN_OWNER_STORAGE,
  P2_PIN_OWNER_CONSOLE,
  P2_PIN_OWNER_GPIO,
  P2_PIN_OWNER_UART,
  P2_PIN_OWNER_PWM,
  P2_PIN_OWNER_CAPTURE,
  P2_PIN_OWNER_ADC,
  P2_PIN_OWNER_DAC,
  P2_PIN_OWNER_SPI,
  P2_PIN_OWNER_I2C
};

enum p2_pin_direction_e
{
  P2_PIN_DIRECTION_DISABLED = 0,
  P2_PIN_DIRECTION_INPUT,
  P2_PIN_DIRECTION_OUTPUT,
  P2_PIN_DIRECTION_BIDIRECTIONAL
};

enum p2_pin_drive_e
{
  P2_PIN_DRIVE_FLOAT = 0,
  P2_PIN_DRIVE_PUSH_PULL,
  P2_PIN_DRIVE_OPEN_DRAIN,
  P2_PIN_DRIVE_PULL_UP,
  P2_PIN_DRIVE_PULL_DOWN,
  P2_PIN_DRIVE_ANALOG
};

enum p2_pin_event_e
{
  P2_PIN_EVENT_NONE = 0,
  P2_PIN_EVENT_SE1,
  P2_PIN_EVENT_SE2,
  P2_PIN_EVENT_SE3,
  P2_PIN_EVENT_SE4
};

enum p2_pin_safe_e
{
  P2_PIN_SAFE_FLOAT = 0,
  P2_PIN_SAFE_LOW,
  P2_PIN_SAFE_HIGH,
  P2_PIN_SAFE_PULL_UP,
  P2_PIN_SAFE_PULL_DOWN
};

struct p2_pin_config_s
{
  uint8_t direction;
  uint8_t drive;
  uint8_t event;
  uint8_t safe;
  uint32_t smartpin_mode;
};

struct p2_pin_state_s
{
  uint8_t pin;
  uint8_t reserved_role;
  uint8_t owner;
  uint8_t owning_cog;
  uint16_t refs;
  uint8_t direction;
  uint8_t drive;
  uint8_t event;
  uint8_t safe;
  uint32_t smartpin_mode;
};

/****************************************************************************
 * Public Function Prototypes
 ****************************************************************************/

int p2_pin_initialize(void);
int p2_pin_reserved_role(unsigned int pin);
int p2_pin_claim(unsigned int pin, enum p2_pin_owner_e owner);
int p2_pin_configure(unsigned int pin, enum p2_pin_owner_e owner,
                     const struct p2_pin_config_s *config);
int p2_pin_release(unsigned int pin, enum p2_pin_owner_e owner);
int p2_pin_get_state(unsigned int pin, struct p2_pin_state_s *state);
int p2_gpio_initialize(void);
void p2_gpio_poll(void);
int p2_uart1_initialize(void);
void p2_uart1_poll(void);
int p2_pwm_initialize(void);
int p2_capture_initialize(void);
int p2_adc_initialize(void);
int p2_dac_initialize(void);
int p2_spi_initialize(void);

#ifdef P2_PIN_MANAGER_HOST_TEST
void p2_pin_test_reset(void);
void p2_pin_test_set_cog(unsigned int cog);
unsigned int p2_pin_test_safe_apply_count(void);
#endif

#endif /* __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_PINS_H */
