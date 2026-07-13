/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_storage_arbiter.h
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

#ifndef __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_STORAGE_ARBITER_H
#define __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_STORAGE_ARBITER_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <stdbool.h>
#include <stdint.h>

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

/* The bit positions below describe P58 through P61, not absolute P2 pin
 * masks.  P58 is deliberately absent from P2_STORAGE_OUTPUTS because MISO
 * must remain an input in every state.
 */

#define P2_STORAGE_MISO       (1u << 0) /* P58 */
#define P2_STORAGE_MOSI       (1u << 1) /* P59 */
#define P2_STORAGE_FLASH_CLK  (1u << 2) /* P60, also SD nCS */
#define P2_STORAGE_FLASH_CS   (1u << 3) /* P61, also SD CLK */
#define P2_STORAGE_SD_CS      P2_STORAGE_FLASH_CLK
#define P2_STORAGE_SD_CLK     P2_STORAGE_FLASH_CS

#define P2_STORAGE_OUTPUTS    (P2_STORAGE_MOSI | P2_STORAGE_FLASH_CLK | \
                               P2_STORAGE_FLASH_CS)

/****************************************************************************
 * Public Types
 ****************************************************************************/

enum p2_storage_state_e
{
  P2_STORAGE_IDLE = 0,
  P2_STORAGE_FLASH_SELECTED,
  P2_STORAGE_SD_SELECTED,
  P2_STORAGE_RECOVERY
};

enum p2_storage_target_e
{
  P2_STORAGE_TARGET_NONE = 0,
  P2_STORAGE_TARGET_FLASH,
  P2_STORAGE_TARGET_SD
};

struct p2_storage_lines_s
{
  uint8_t outputs;
  uint8_t levels;
};

struct p2_storage_arbiter_ops_s
{
  int  (*lock)(void *arg, uint32_t timeout);
  int  (*unlock)(void *arg);
  void (*apply)(void *arg, const struct p2_storage_lines_s *lines);
};

struct p2_storage_arbiter_s
{
  const struct p2_storage_arbiter_ops_s *ops;
  void *arg;
  struct p2_storage_lines_s lines;
  enum p2_storage_state_e state;
  enum p2_storage_target_e owner;
};

/****************************************************************************
 * Public Function Prototypes
 ****************************************************************************/

int p2_storage_arbiter_initialize(
  struct p2_storage_arbiter_s *arbiter,
  const struct p2_storage_arbiter_ops_s *ops,
  void *arg);
int p2_storage_arbiter_acquire(struct p2_storage_arbiter_s *arbiter,
                               enum p2_storage_target_e target,
                               uint32_t timeout);
int p2_storage_arbiter_select(struct p2_storage_arbiter_s *arbiter,
                              enum p2_storage_target_e target);
int p2_storage_arbiter_deselect(struct p2_storage_arbiter_s *arbiter,
                                enum p2_storage_target_e target);
int p2_storage_arbiter_fail(struct p2_storage_arbiter_s *arbiter,
                            enum p2_storage_target_e target);
int p2_storage_arbiter_recover(struct p2_storage_arbiter_s *arbiter,
                               enum p2_storage_target_e target);
int p2_storage_arbiter_release(struct p2_storage_arbiter_s *arbiter,
                               enum p2_storage_target_e target);
bool p2_storage_arbiter_transaction_allowed(
  const struct p2_storage_arbiter_s *arbiter,
  enum p2_storage_target_e target);

#endif /* __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_STORAGE_ARBITER_H */
