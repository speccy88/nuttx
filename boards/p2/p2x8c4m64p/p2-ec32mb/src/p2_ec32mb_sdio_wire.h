/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_sdio_wire.h
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

#ifndef __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_SDIO_WIRE_H
#define __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_SDIO_WIRE_H

/* Shared HUB-RAM mailbox consumed by the native COGEXEC capture image.  Keep
 * these offsets usable by both C and the preprocessed assembly source.
 */

#define P2_SDIO_WIRE_REQUEST_OFFSET       0
#define P2_SDIO_WIRE_COMPLETE_OFFSET      4
#define P2_SDIO_WIRE_OPERATION_OFFSET     8
#define P2_SDIO_WIRE_BUFFER_OFFSET       12
#define P2_SDIO_WIRE_BLOCKLEN_OFFSET     16
#define P2_SDIO_WIRE_NBLOCKS_OFFSET      20
#define P2_SDIO_WIRE_CLOCK_X_OFFSET      24
#define P2_SDIO_WIRE_XFRQ_OFFSET         28
#define P2_SDIO_WIRE_SCAN_HALF_OFFSET    32
#define P2_SDIO_WIRE_START_LIMIT_OFFSET  36
#define P2_SDIO_WIRE_STATUS_OFFSET       40
#define P2_SDIO_WIRE_BYTES_OFFSET        44
#define P2_SDIO_WIRE_READY_OFFSET        48
#define P2_SDIO_WIRE_INPUT_MODE_OFFSET   52
#define P2_SDIO_WIRE_RX_LAG_OFFSET       56
#define P2_SDIO_WIRE_CRC_BUFFER_OFFSET   60
#define P2_SDIO_WIRE_VERIFY_CRC_OFFSET   64
#define P2_SDIO_WIRE_SIZE                68

#define P2_SDIO_WIRE_OP_IDLE              0
#define P2_SDIO_WIRE_OP_READ_BLOCKS       1

#define P2_SDIO_WIRE_STATUS_OK             0
#define P2_SDIO_WIRE_STATUS_EIO           -5
#define P2_SDIO_WIRE_STATUS_EINVAL        -22
#define P2_SDIO_WIRE_STATUS_ETIMEDOUT     -110

#endif /* __BOARDS_P2_P2X8C4M64P_P2_EC32MB_SRC_P2_EC32MB_SDIO_WIRE_H */
