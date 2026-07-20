/****************************************************************************
 * arch/p2/include/hub_crc32.h
 *
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

#ifndef __ARCH_P2_INCLUDE_HUB_CRC32_H
#define __ARCH_P2_INCLUDE_HUB_CRC32_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <stddef.h>
#include <stdint.h>

#include <nuttx/compiler.h>

/****************************************************************************
 * Public Function Prototypes
 ****************************************************************************/

/* Update a reflected CRC-32 accumulator from a native Hub byte buffer.
 *
 * This is the raw incremental operation: callers supply and receive the
 * running accumulator without either the initial or final XOR.  The buffer
 * must be normal Hub memory rather than a unified-memory PSRAM pointer.
 */

uint32_t p2_hub_crc32_update(uint32_t crc, FAR const uint8_t *data,
                             size_t size);

#endif /* __ARCH_P2_INCLUDE_HUB_CRC32_H */
