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

/* Aligned bulk requests use the P2 streamer's Hub FIFO instead of the
 * HUBEXEC bit-bang loop.  One logical long spans the four byte-interleaved
 * chips and takes four system clocks (two QPI clocks).  APS6404L linear
 * burst is rated to only 84 MHz.  Recovery therefore resets the chips and
 * issues exactly one C0 toggle to select their 32-byte wrapped mode, which
 * is rated to at least 109 MHz at 3.3 V.  End every CE-low interval at that
 * per-chip wrap boundary: 32 chip bytes are 32 logical longs, or 128
 * interleaved Hub bytes.  The service falls back to the 5-MHz scalar path
 * for every unaligned or short edge.
 */

#define P2_PSRAM_STREAM_MIN_BYTES          32
#define P2_PSRAM_STREAM_CHIP_WRAP_BYTES    32
#define P2_PSRAM_STREAM_FRAGMENT_LONGS     32
#define P2_PSRAM_STREAM_FRAGMENT_BYTES     \
  (P2_PSRAM_STREAM_FRAGMENT_LONGS * 4)
#define P2_PSRAM_STREAM_READ                1
#define P2_PSRAM_STREAM_WRITE               2
#define P2_PSRAM_STREAM_COG_ENTRY          0x040
#define P2_PSRAM_STREAM_LUT_TABLE_LONGS       16
#define P2_PSRAM_STREAM_COG_IMAGE_LONGS      128
#define P2_PSRAM_STREAM_QPI_CLOCK_HZ       90000000
#ifndef P2_PSRAM_STREAM_READ_OFFSET
#define P2_PSRAM_STREAM_READ_OFFSET          22
#endif
#define P2_PSRAM_STREAM_READ_DELAY_COMMAND \
  (0x20d00000 + P2_PSRAM_STREAM_READ_OFFSET)
#define P2_PSRAM_STREAM_READ_QPI_CLOCKS    13
#define P2_PSRAM_STREAM_CYCLES_PER_LONG    4
#define P2_PSRAM_STREAM_CE_GUARD_CYCLES    64
#define P2_PSRAM_STREAM_CE_MARGIN_CYCLES   200
#define P2_PSRAM_STREAM_CE_BOUND_CYCLES    \
  (P2_PSRAM_STREAM_FRAGMENT_LONGS * \
   P2_PSRAM_STREAM_CYCLES_PER_LONG + \
   P2_PSRAM_STREAM_READ_QPI_CLOCKS * 2 + \
   P2_PSRAM_STREAM_CE_GUARD_CYCLES)

#define P2_PSRAM_DATA_FIRST_PIN          40
#define P2_PSRAM_DATA_LAST_PIN           55
#define P2_PSRAM_CLOCK_PIN               56
#define P2_PSRAM_CE_PIN                  57

#define P2_PSRAM_HALF_PERIOD_TICKS       16
#define P2_PSRAM_QPI_CLOCK_HZ            5000000
#define P2_PSRAM_CE_LOW_LIMIT_CYCLES     1440

#endif /* __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_PSRAM_WIRE_H */
