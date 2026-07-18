/****************************************************************************
 * arch/p2/include/python_container.h
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

#ifndef __ARCH_P2_INCLUDE_PYTHON_CONTAINER_H
#define __ARCH_P2_INCLUDE_PYTHON_CONTAINER_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <nuttx/compiler.h>

#include <arch/overlay.h>

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_PYTHON_CONTAINER_FINGERPRINT_SIZE 32

/****************************************************************************
 * Public Types
 ****************************************************************************/

/* All callbacks return zero only when the complete requested range was
 * transferred.  A negative errno is preserved; a positive return is treated
 * as -EIO.  The parser performs its own overflow and source-size checks
 * before invoking a callback.
 */

typedef int (*p2_python_container_read_t)(FAR void *arg, uint64_t address,
                                          FAR void *buffer, size_t size);
typedef int (*p2_python_container_write_t)(FAR void *arg, uint64_t address,
                                           FAR const void *buffer,
                                           size_t size);
typedef int (*p2_python_container_zero_t)(FAR void *arg, uint64_t address,
                                          size_t size);

struct p2_python_container_source_s
{
  p2_python_container_read_t read;
  FAR void *arg;
  uint64_t size;
};

/* If read and write are NULL, the tagged address is used directly through
 * memcpy/memset.  That mode requires this object to be compiled with the P2
 * unified-memory pass.  Supplying one of read/write but not the other is an
 * error.  zero is optional; a missing zero callback is implemented as
 * bounded zero-filled writes.
 */

struct p2_python_container_target_s
{
  p2_python_container_read_t read;
  p2_python_container_write_t write;
  p2_python_container_zero_t zero;
  FAR void *arg;
};

struct p2_python_container_contract_s
{
  uint8_t build_fingerprint[P2_PYTHON_CONTAINER_FINGERPRINT_SIZE];
  uint32_t overlay_load_address;
  uint32_t overlay_slot_size;
};

struct p2_python_container_info_s
{
  uint32_t file_size;
  uint32_t manifest_size;
  uint32_t section_count;
  uint32_t group_count;
  uint32_t stub_count;
  uint32_t overlay_load_address;
  uint32_t overlay_slot_size;
};

/* p2_python_container_initialize() copies the exact container group table to
 * group_workspace before publishing it through p2_overlay_install_groups().
 * The workspace is caller-owned Hub memory and may be reused after the
 * initializer returns because the overlay runtime installs its own copy.
 */

struct p2_python_container_config_s
{
  struct p2_python_container_source_s source;
  struct p2_python_container_target_s target;
  struct p2_python_container_contract_s contract;
  uintptr_t backing_address;
  size_t backing_capacity;
  FAR struct p2_overlay_group_s *group_workspace;
  size_t group_workspace_count;
};

/* Runtime state is caller-owned and must remain alive while overlay calls
 * can occur: the overlay loader retains its address.  Treat fields as
 * read-only.
 */

struct p2_python_container_s
{
  struct p2_python_container_target_s target;
  uintptr_t backing_address;
  uint32_t backing_size;
  uint32_t group_table_offset;
  uint32_t group_count;
  uintptr_t stdlib_romfs;
  uint32_t stdlib_romfs_size;
  uint32_t overlay_load_address;
  uint32_t overlay_slot_size;
  uint32_t state;
};

/* Memory-region callbacks are useful for a memory-backed container and for
 * deterministic host tests.  address is the logical address corresponding
 * to data[0], not necessarily zero.
 */

struct p2_python_container_memory_s
{
  FAR uint8_t *data;
  uint64_t address;
  size_t size;
};

/****************************************************************************
 * Public Function Prototypes
 ****************************************************************************/

int p2_python_container_memory_read(FAR void *arg, uint64_t address,
                                    FAR void *buffer, size_t size);
int p2_python_container_memory_write(FAR void *arg, uint64_t address,
                                     FAR const void *buffer, size_t size);
int p2_python_container_memory_zero(FAR void *arg, uint64_t address,
                                    size_t size);

/* Validate the complete container, including manifest SHA-256 and all
 * payload CRCs, without changing target memory or overlay runtime state.
 */

int p2_python_container_validate(
  FAR const struct p2_python_container_source_s *source,
  FAR const struct p2_python_container_contract_s *contract,
  FAR struct p2_python_container_info_s *info);

/* Validate, copy to tagged PSRAM, validate the copied image again,
 * initialize external data/zero ranges, install overlay groups, and finally
 * publish the loader.  The result is cleared on every failure and is never
 * published to the overlay dispatcher before all preceding checks succeed.
 */

int p2_python_container_initialize(
  FAR struct p2_python_container_s *container,
  FAR const struct p2_python_container_config_s *config);

int p2_python_container_get_stdlib(
  FAR const struct p2_python_container_s *container,
  FAR const void **address, FAR size_t *size);

/* This callback is registered with the resident overlay runtime.  It accepts
 * only exact group records from the validated container backing image and
 * copies tagged PSRAM to the one configured Hub execution slot.
 */

int p2_python_container_overlay_loader(FAR void *arg, uint32_t group,
                                       uintptr_t source,
                                       FAR void *destination,
                                       size_t image_size);

#endif /* __ARCH_P2_INCLUDE_PYTHON_CONTAINER_H */
