/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_psram_wire.h
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
 ****************************************************************************/

#ifndef __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_PSRAM_WIRE_H
#define __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_PSRAM_WIRE_H

/* Keep this header preprocessor-only so the timing assembly can include
 * it.
 */

#define P2_PSRAM_WIRE_OPERATION_OFFSET   0
#define P2_PSRAM_WIRE_ADDRESS_OFFSET     4
#define P2_PSRAM_WIRE_TX_LANES_OFFSET    8
#define P2_PSRAM_WIRE_RX_LANES_OFFSET    12
#define P2_PSRAM_WIRE_STATUS_OFFSET      16
#define P2_PSRAM_WIRE_CE_CYCLES_OFFSET  20
#define P2_PSRAM_WIRE_SIZE               24

#define P2_PSRAM_WIRE_RECOVER            1
#define P2_PSRAM_WIRE_READ_WORD          2
#define P2_PSRAM_WIRE_WRITE_WORD         3
#define P2_PSRAM_WIRE_SAFE               4

#define P2_PSRAM_DATA_FIRST_PIN          40
#define P2_PSRAM_DATA_LAST_PIN           55
#define P2_PSRAM_CLOCK_PIN               56
#define P2_PSRAM_CE_PIN                  57

#define P2_PSRAM_HALF_PERIOD_TICKS       16
#define P2_PSRAM_QPI_CLOCK_HZ            5000000
#define P2_PSRAM_CE_LOW_LIMIT_CYCLES     1440

#endif /* __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_PSRAM_WIRE_H */
