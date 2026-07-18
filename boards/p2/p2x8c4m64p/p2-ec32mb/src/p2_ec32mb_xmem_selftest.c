/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_xmem_selftest.c
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

#include <sys/stat.h>

#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include <nuttx/arch.h>
#include <nuttx/board.h>
#include <nuttx/compiler.h>

#include <arch/board/p2_ec32mb_psram.h>

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_XMEM_TEST_ARENA_SIZE   (UINT32_C(1024) * 1024)
#define P2_XMEM_TEST_GROWN_SIZE   (P2_XMEM_TEST_ARENA_SIZE + \
                                   UINT32_C(512) * 1024)
#define P2_XMEM_TEST_BULK_SIZE    256u
#define P2_XMEM_TEST_MOVE_SIZE    192u
#define P2_XMEM_TEST_THREAD_WORDS 256u
#define P2_XMEM_TEST_FRAGMENTS    12u
#define P2_XMEM_FRAGMENT_EDGE     64u
#define P2_XMEM_FULL_CHUNK        4096u
#define P2_XMEM_FULL_PROGRESS     (UINT32_C(4) * 1024 * 1024)
#define P2_XMEM_PAGE_BOUNDARY     (UINT32_C(1024) * P2_PSRAM_CHIP_COUNT)
#define P2_XMEM_FNV_OFFSET        UINT32_C(2166136261)
#define P2_XMEM_FNV_PRIME         UINT32_C(16777619)

/****************************************************************************
 * Private Types
 ****************************************************************************/

begin_packed_struct struct p2_xmem_scalars_s
{
  uint8_t pad0;
  uint16_t value16;
  uint8_t value8;
  uint8_t pad1;
  uint32_t value32;
  uint8_t pad2;
  uint64_t value64;
} end_packed_struct;

struct p2_xmem_worker_s
{
  FAR volatile uint32_t *words;
  uint32_t seed;
  volatile int result;
};

/****************************************************************************
 * Private Data
 ****************************************************************************/

static uint8_t g_p2_xmem_source[P2_XMEM_TEST_BULK_SIZE];
static uint8_t g_p2_xmem_result[P2_XMEM_TEST_BULK_SIZE];
static uint8_t g_p2_xmem_expected[P2_XMEM_TEST_BULK_SIZE];

#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED_SELFTEST_FULL
static uint8_t g_p2_xmem_full_buffer[P2_XMEM_FULL_CHUNK];
static uint32_t g_p2_xmem_full_hash;
static bool g_p2_xmem_full_complete;
#endif

static bool g_p2_xmem_started;

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static void p2_xmem_puts(FAR const char *text)
{
  while (*text != '\0')
    {
      up_putc(*text++);
    }
}

static void p2_xmem_marker(FAR const char *text)
{
  p2_xmem_puts(text);
  up_putc('\r');
  up_putc('\n');
}

static void p2_xmem_start_marker(void)
{
  if (!g_p2_xmem_started)
    {
      p2_xmem_marker("P2XMEM:START:BASE=10000000:SIZE=33554432");
      g_p2_xmem_started = true;
    }
}

static int p2_xmem_fail(FAR const char *stage, FAR const char *reason,
                        int error)
{
  p2_xmem_puts("P2XMEM:FAIL:STAGE=");
  p2_xmem_puts(stage);
  p2_xmem_puts(":REASON=");
  p2_xmem_puts(reason);
  up_putc('\r');
  up_putc('\n');
  return error;
}

static bool p2_xmem_is_tagged(FAR const void *address, uint32_t length)
{
  uintptr_t start = (uintptr_t)address;

  return start >= P2_PSRAM_UNIFIED_BASE &&
         start < P2_PSRAM_UNIFIED_END &&
         length <= P2_PSRAM_UNIFIED_END - start;
}

static bool p2_xmem_buffers_equal(FAR const uint8_t *left,
                                  FAR const uint8_t *right,
                                  uint32_t length)
{
  uint32_t index;

  for (index = 0; index < length; index++)
    {
      if (left[index] != right[index])
        {
          return false;
        }
    }

  return true;
}

static pthread_addr_t p2_xmem_worker(pthread_addr_t argument)
{
  FAR struct p2_xmem_worker_s *worker =
    (FAR struct p2_xmem_worker_s *)argument;
  uint32_t index;

  worker->result = -EIO;
  for (index = 0; index < P2_XMEM_TEST_THREAD_WORDS; index++)
    {
      uint32_t expected = worker->seed ^
                          (index * UINT32_C(0x01010101));

      worker->words[index] = expected;
      if (worker->words[index] != expected)
        {
          return NULL;
        }

      if ((index & 15u) == 0)
        {
          sched_yield();
        }
    }

  worker->result = 0;
  return NULL;
}

static int p2_xmem_scalar_test(FAR uint8_t *arena)
{
  uint8_t hub_bytes[24];
  FAR uint8_t *hub_unaligned;
  volatile struct p2_xmem_scalars_s hub;
  FAR volatile struct p2_xmem_scalars_s *scalars =
    (FAR volatile struct p2_xmem_scalars_s *)(arena + 0x100);

  /* The compiler ABI omits alignment.  Exercise the helper's byte-copy
   * fallback with packed, dynamically addressed Hub values as well as the
   * tagged byte-addressed path below.
   */

  hub.pad0 = UINT8_C(0x1a);
  hub.value16 = UINT16_C(0xa55a);
  hub.value8 = UINT8_C(0x3c);
  hub.pad1 = UINT8_C(0xd4);
  hub.value32 = UINT32_C(0xfedcba98);
  hub.pad2 = UINT8_C(0x7e);
  hub.value64 = UINT64_C(0xfedcba9876543210);

  if (hub.pad0 != UINT8_C(0x1a) ||
      hub.value16 != UINT16_C(0xa55a) ||
      hub.value8 != UINT8_C(0x3c) ||
      hub.pad1 != UINT8_C(0xd4) ||
      hub.value32 != UINT32_C(0xfedcba98) ||
      hub.pad2 != UINT8_C(0x7e) ||
      hub.value64 != UINT64_C(0xfedcba9876543210))
    {
      return -EIO;
    }

  /* Direct ABI calls force dynamic Hub classification and cover the
   * unaligned fallback that provenance-aware compiler lowering can avoid for
   * the packed alloca above.
   */

  hub_unaligned = hub_bytes;
  while (((uintptr_t)hub_unaligned & 1u) == 0)
    {
      hub_unaligned++;
    }

  __p2_xmem_store8(hub_unaligned, UINT8_C(0x69));
  if (__p2_xmem_load8(hub_unaligned) != UINT8_C(0x69))
    {
      return -EIO;
    }

  __p2_xmem_store16(hub_unaligned, UINT16_C(0x96a5));
  if (__p2_xmem_load16(hub_unaligned) != UINT16_C(0x96a5))
    {
      return -EIO;
    }

  __p2_xmem_store32(hub_unaligned, UINT32_C(0x76543210));
  if (__p2_xmem_load32(hub_unaligned) != UINT32_C(0x76543210))
    {
      return -EIO;
    }

  __p2_xmem_store64(hub_unaligned, UINT64_C(0x0f1e2d3c4b5a6978));
  if (__p2_xmem_load64(hub_unaligned) !=
      UINT64_C(0x0f1e2d3c4b5a6978))
    {
      return -EIO;
    }

  scalars->pad0 = UINT8_C(0xa1);
  scalars->value16 = UINT16_C(0x5aa5);
  scalars->value8 = UINT8_C(0xc3);
  scalars->pad1 = UINT8_C(0x4d);
  scalars->value32 = UINT32_C(0x89abcdef);
  scalars->pad2 = UINT8_C(0xe7);
  scalars->value64 = UINT64_C(0x0123456789abcdef);

  if (scalars->pad0 != UINT8_C(0xa1) ||
      scalars->value16 != UINT16_C(0x5aa5) ||
      scalars->value8 != UINT8_C(0xc3) ||
      scalars->pad1 != UINT8_C(0x4d) ||
      scalars->value32 != UINT32_C(0x89abcdef) ||
      scalars->pad2 != UINT8_C(0xe7) ||
      scalars->value64 != UINT64_C(0x0123456789abcdef))
    {
      return -EIO;
    }

  return 0;
}

static int p2_xmem_bulk_test(FAR uint8_t *arena)
{
  FAR uint8_t *external = arena + 0x800;
  FAR uint8_t *external_copy = arena + 0xa00;
  uint32_t index;

  for (index = 0; index < P2_XMEM_TEST_BULK_SIZE; index++)
    {
      g_p2_xmem_source[index] =
        (uint8_t)((index * 37u) ^ (index >> 1) ^ 0x5au);
    }

  /* Call the compiler ABI directly here so HIL proves each runtime entry
   * point independently of the compiler's standard-call rewriting.
   */

  __p2_xmem_memset(external, UINT8_C(0xa5), P2_XMEM_TEST_BULK_SIZE);
  __p2_xmem_memcpy(g_p2_xmem_result, external,
                   P2_XMEM_TEST_BULK_SIZE);
  for (index = 0; index < P2_XMEM_TEST_BULK_SIZE; index++)
    {
      if (g_p2_xmem_result[index] != UINT8_C(0xa5))
        {
          return -EIO;
        }
    }

  __p2_xmem_memcpy(external, g_p2_xmem_source,
                   P2_XMEM_TEST_BULK_SIZE);

  /* Copy between two non-overlapping tagged ranges before exercising both
   * overlap directions below.
   */

  __p2_xmem_memcpy(external_copy, external, P2_XMEM_TEST_BULK_SIZE);
  __p2_xmem_memcpy(g_p2_xmem_result, external_copy,
                   P2_XMEM_TEST_BULK_SIZE);
  if (!p2_xmem_buffers_equal(g_p2_xmem_result, g_p2_xmem_source,
                             P2_XMEM_TEST_BULK_SIZE))
    {
      return -EIO;
    }

  memcpy(g_p2_xmem_expected, g_p2_xmem_source, P2_XMEM_TEST_BULK_SIZE);
  __p2_xmem_memmove(external + 32, external, P2_XMEM_TEST_MOVE_SIZE);
  memmove(g_p2_xmem_expected + 32, g_p2_xmem_expected,
          P2_XMEM_TEST_MOVE_SIZE);
  __p2_xmem_memcpy(g_p2_xmem_result, external,
                   P2_XMEM_TEST_BULK_SIZE);
  if (!p2_xmem_buffers_equal(g_p2_xmem_result, g_p2_xmem_expected,
                             P2_XMEM_TEST_BULK_SIZE))
    {
      return -EIO;
    }

  __p2_xmem_memmove(external, external + 32, P2_XMEM_TEST_MOVE_SIZE);
  memmove(g_p2_xmem_expected, g_p2_xmem_expected + 32,
          P2_XMEM_TEST_MOVE_SIZE);
  __p2_xmem_memcpy(g_p2_xmem_result, external,
                   P2_XMEM_TEST_BULK_SIZE);
  if (!p2_xmem_buffers_equal(g_p2_xmem_result, g_p2_xmem_expected,
                             P2_XMEM_TEST_BULK_SIZE))
    {
      return -EIO;
    }

  /* Retain an ordinary libc path as an end-to-end compiler check.  The
   * unified-memory pass recognizes these exact standard calls despite
   * -fno-builtin and lowers their non-Hub operands to the bulk ABI.
   */

  memset(external_copy, 0x6d, 64);
  memcpy(g_p2_xmem_result, external_copy, 64);
  for (index = 0; index < 64; index++)
    {
      if (g_p2_xmem_result[index] != UINT8_C(0x6d))
        {
          return -EIO;
        }
    }

  memcpy(external_copy, g_p2_xmem_source, 64);
  memcpy(external, external_copy, 64);
  memcpy(g_p2_xmem_expected, g_p2_xmem_source, 64);
  memmove(external + 1, external, 63);
  memmove(g_p2_xmem_expected + 1, g_p2_xmem_expected, 63);
  memcpy(g_p2_xmem_result, external, 64);
  if (!p2_xmem_buffers_equal(g_p2_xmem_result, g_p2_xmem_expected, 64))
    {
      return -EIO;
    }

  return 0;
}

static int p2_xmem_geometry_test(FAR uint8_t *arena)
{
  FAR struct p2_psram_geometry_s *geometry =
    (FAR struct p2_psram_geometry_s *)(arena + 0x6000);
  int ret;

  /* The destination is deliberately tagged.  p2_psram_get_geometry() must
   * release every service lock before its compiler-lowered result copy can
   * re-enter the unified transfer path.
   */

  ret = p2_psram_get_geometry(geometry);
  if (ret < 0 ||
      geometry->size_bytes != P2_PSRAM_SIZE_BYTES ||
      geometry->chip_count != P2_PSRAM_CHIP_COUNT ||
      geometry->chip_size_bytes != P2_PSRAM_CHIP_SIZE_BYTES ||
      geometry->natural_word_bytes != P2_PSRAM_NATURAL_WORD_BYTES ||
      geometry->max_request_bytes < 4096u ||
      geometry->max_request_bytes > 65536u ||
      geometry->qpi_clock_hz == 0 ||
      geometry->ce_low_limit_cycles == 0 ||
      geometry->max_ce_low_cycles > geometry->ce_low_limit_cycles ||
      geometry->service_cog >= 8u)
    {
      return ret < 0 ? ret : -EIO;
    }

  p2_xmem_marker("P2XMEM:GEOMETRY:PASS");
  return 0;
}

static int p2_xmem_concurrent_test(FAR uint8_t *arena)
{
  struct p2_xmem_worker_s workers[2];
  pthread_t threads[2];
  uint32_t index;
  unsigned int worker_index;
  int ret;

  workers[0].words = (FAR volatile uint32_t *)(arena + 0x2000);
  workers[0].seed = UINT32_C(0x13579bdf);
  workers[0].result = -EINPROGRESS;
  workers[1].words = (FAR volatile uint32_t *)(arena + 0x4000);
  workers[1].seed = UINT32_C(0x2468ace0);
  workers[1].result = -EINPROGRESS;

  ret = pthread_create(&threads[0], NULL, p2_xmem_worker, &workers[0]);
  if (ret != 0)
    {
      return -ret;
    }

  ret = pthread_create(&threads[1], NULL, p2_xmem_worker, &workers[1]);
  if (ret != 0)
    {
      pthread_join(threads[0], NULL);
      return -ret;
    }

  ret = pthread_join(threads[0], NULL);
  if (ret == 0)
    {
      ret = pthread_join(threads[1], NULL);
    }
  else
    {
      pthread_join(threads[1], NULL);
    }

  if (ret != 0 || workers[0].result < 0 || workers[1].result < 0)
    {
      return ret != 0 ? -ret : -EIO;
    }

  /* Immediate worker readback catches transaction errors.  Re-read both
   * completed ranges here so a tagged-address alias or a later cross-worker
   * overwrite cannot pass merely because each store was observed promptly.
   */

  for (worker_index = 0; worker_index < 2; worker_index++)
    {
      for (index = 0; index < P2_XMEM_TEST_THREAD_WORDS; index++)
        {
          uint32_t expected = workers[worker_index].seed ^
                              (index * UINT32_C(0x01010101));

          if (workers[worker_index].words[index] != expected)
            {
              return -EIO;
            }
        }
    }

  return 0;
}

static void p2_xmem_fragment_fill(FAR void *block, uint32_t size,
                                  uint8_t pattern)
{
  FAR uint8_t *bytes = (FAR uint8_t *)block;

  memset(bytes, pattern, P2_XMEM_FRAGMENT_EDGE);
  memset(bytes + size - P2_XMEM_FRAGMENT_EDGE, (uint8_t)~pattern,
         P2_XMEM_FRAGMENT_EDGE);
}

static bool p2_xmem_fragment_verify(FAR const void *block, uint32_t size,
                                    uint8_t pattern)
{
  FAR const uint8_t *bytes = (FAR const uint8_t *)block;
  uint32_t index;

  for (index = 0; index < P2_XMEM_FRAGMENT_EDGE; index++)
    {
      if (bytes[index] != pattern ||
          bytes[size - P2_XMEM_FRAGMENT_EDGE + index] != (uint8_t)~pattern)
        {
          return false;
        }
    }

  return true;
}

static int p2_xmem_fragmentation_test(void)
{
  FAR void *blocks[P2_XMEM_TEST_FRAGMENTS];
  uint32_t sizes[P2_XMEM_TEST_FRAGMENTS];
  uint8_t patterns[P2_XMEM_TEST_FRAGMENTS];
  bool saw_tagged = false;
  uint32_t index;
  int ret = -ENOMEM;

  memset(blocks, 0, sizeof(blocks));
  for (index = 0; index < P2_XMEM_TEST_FRAGMENTS; index++)
    {
      uint32_t size = UINT32_C(98304) + index * UINT32_C(4096);

      blocks[index] = malloc(size);
      if (blocks[index] == NULL)
        {
          goto errout;
        }

      saw_tagged |= p2_xmem_is_tagged(blocks[index], size);
      sizes[index] = size;
      patterns[index] = (uint8_t)(0x30u + index);
      p2_xmem_fragment_fill(blocks[index], sizes[index], patterns[index]);
    }

  for (index = 0; index < P2_XMEM_TEST_FRAGMENTS; index++)
    {
      if (!p2_xmem_fragment_verify(blocks[index], sizes[index],
                                   patterns[index]))
        {
          ret = -EIO;
          goto errout;
        }
    }

  for (index = 1; index < P2_XMEM_TEST_FRAGMENTS; index += 2)
    {
      free(blocks[index]);
      blocks[index] = NULL;
    }

  for (index = 0; index < P2_XMEM_TEST_FRAGMENTS; index += 2)
    {
      if (!p2_xmem_fragment_verify(blocks[index], sizes[index],
                                   patterns[index]))
        {
          ret = -EIO;
          goto errout;
        }
    }

  for (index = 1; index < P2_XMEM_TEST_FRAGMENTS; index += 2)
    {
      uint32_t size = UINT32_C(49152) + index * UINT32_C(2048);

      blocks[index] = malloc(size);
      if (blocks[index] == NULL)
        {
          goto errout;
        }

      saw_tagged |= p2_xmem_is_tagged(blocks[index], size);
      sizes[index] = size;
      patterns[index] = (uint8_t)(0x70u + index);
      p2_xmem_fragment_fill(blocks[index], sizes[index], patterns[index]);
    }

  for (index = 0; index < P2_XMEM_TEST_FRAGMENTS; index++)
    {
      if (!p2_xmem_fragment_verify(blocks[index], sizes[index],
                                   patterns[index]))
        {
          ret = -EIO;
          goto errout;
        }
    }

  for (index = 0; index < P2_XMEM_TEST_FRAGMENTS; index++)
    {
      free(blocks[index]);
    }

  return saw_tagged ? 0 : -EFAULT;

errout:
  for (index = 0; index < P2_XMEM_TEST_FRAGMENTS; index++)
    {
      free(blocks[index]);
    }

  return ret;
}

#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED_FAULT_INJECT_RAW_LOCK
static void p2_xmem_raw_lock_fault_test(void) noreturn_function;
static void p2_xmem_raw_lock_fault_test(void)
{
  int ret;

  ret = p2_psram_unified_arm_raw_lock_stall();
  if (ret < 0)
    {
      p2_xmem_fail("FAULT_RAW_LOCK", "ARM", ret);
      board_reset(0);
    }

  p2_xmem_marker("P2XMEM:FAULT_RAW_LOCK:ARMED");
  ret = p2_psram_unified_transfer(P2_PSRAM_OPERATION_READ, 0,
                                  g_p2_xmem_result, 1);
  if (ret != -ETIMEDOUT)
    {
      p2_xmem_fail("FAULT_RAW_LOCK", "TIMEOUT", ret);
      board_reset(0);
    }

  ret = p2_psram_unified_transfer(P2_PSRAM_OPERATION_READ, 0,
                                  g_p2_xmem_result, 1);
  if (ret != -ENODEV)
    {
      p2_xmem_fail("FAULT_RAW_LOCK", "NOT_TERMINAL", ret);
      board_reset(0);
    }

  p2_xmem_marker("P2XMEM:FAULT_RAW_LOCK:PASS:TERMINAL");
  board_reset(0);

  for (; ; )
    {
    }
}
#endif

#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED_SELFTEST_FULL
static int p2_xmem_boundary_test(void)
{
  FAR void *address;

  /* Each virtual long interleaves one byte from every chip, so crossing the
   * 1-KiB page boundary on each APS6404L occurs at virtual offset 4096.
   * Exercise unaligned scalar requests that straddle that boundary.
   */

  address = (FAR void *)(P2_PSRAM_UNIFIED_BASE +
                         P2_XMEM_PAGE_BOUNDARY - 1u);
  __p2_xmem_store16(address, UINT16_C(0xa55a));
  if (__p2_xmem_load16(address) != UINT16_C(0xa55a))
    {
      return -EIO;
    }

  address = (FAR void *)(P2_PSRAM_UNIFIED_BASE +
                         P2_XMEM_PAGE_BOUNDARY - 2u);
  __p2_xmem_store32(address, UINT32_C(0x89abcdef));
  if (__p2_xmem_load32(address) != UINT32_C(0x89abcdef))
    {
      return -EIO;
    }

  address = (FAR void *)(P2_PSRAM_UNIFIED_BASE +
                         P2_XMEM_PAGE_BOUNDARY - 3u);
  __p2_xmem_store64(address, UINT64_C(0x0123456789abcdef));
  if (__p2_xmem_load64(address) != UINT64_C(0x0123456789abcdef))
    {
      return -EIO;
    }

  /* Classifier ranges are half open.  For every scalar width, prove that an
   * access whose last byte is 0x11ffffff remains valid and reaches PSRAM.
   */

  address = (FAR void *)(P2_PSRAM_UNIFIED_END - 1u);
  __p2_xmem_store8(address, UINT8_C(0x3c));
  if (__p2_xmem_load8(address) != UINT8_C(0x3c))
    {
      return -EIO;
    }

  address = (FAR void *)(P2_PSRAM_UNIFIED_END - 2u);
  __p2_xmem_store16(address, UINT16_C(0x5aa5));
  if (__p2_xmem_load16(address) != UINT16_C(0x5aa5))
    {
      return -EIO;
    }

  address = (FAR void *)(P2_PSRAM_UNIFIED_END - 4u);
  __p2_xmem_store32(address, UINT32_C(0xfedcba98));
  if (__p2_xmem_load32(address) != UINT32_C(0xfedcba98))
    {
      return -EIO;
    }

  address = (FAR void *)(P2_PSRAM_UNIFIED_END - 8u);
  __p2_xmem_store64(address, UINT64_C(0xfedcba9876543210));
  if (__p2_xmem_load64(address) != UINT64_C(0xfedcba9876543210))
    {
      return -EIO;
    }

  p2_xmem_marker("P2XMEM:BOUNDARY:PASS");
  return 0;
}

static uint8_t p2_xmem_full_pattern(uint32_t address)
{
  return (uint8_t)(address ^ (address >> 8) ^ (address >> 16) ^
                   (address >> 24) ^ UINT32_C(0xa5));
}

static void p2_xmem_full_progress(FAR const char *operation,
                                  uint32_t completed)
{
  static const char hex[] = "0123456789ABCDEF";
  int shift;

  p2_xmem_puts("P2XMEM:FULL:PROGRESS:");
  p2_xmem_puts(operation);
  up_putc('=');
  for (shift = 28; shift >= 0; shift -= 4)
    {
      up_putc(hex[(completed >> shift) & 15u]);
    }

  up_putc('\r');
  up_putc('\n');
}

static void p2_xmem_hash_marker(uint32_t hash)
{
  char marker[] = "P2XMEM:FULL:PASS:FNV=00000000";
  static const char hex[] = "0123456789ABCDEF";
  const uint32_t digits = sizeof("P2XMEM:FULL:PASS:FNV=") - 1u;
  uint32_t index;

  for (index = 0; index < 8; index++)
    {
      marker[digits + index] =
        hex[(hash >> ((7u - index) * 4u)) & 15u];
    }

  p2_xmem_marker(marker);
}
#endif

/****************************************************************************
 * Public Functions
 ****************************************************************************/

#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED_SELFTEST_FULL
int p2_psram_unified_fulltest(void)
{
  uint32_t address;
  uint32_t hash = P2_XMEM_FNV_OFFSET;
  uint32_t index;
  int ret;

  p2_xmem_start_marker();

  ret = p2_xmem_boundary_test();
  if (ret < 0)
    {
      return p2_xmem_fail("BOUNDARY", "MISMATCH", ret);
    }

  for (address = 0; address < P2_PSRAM_SIZE_BYTES;
       address += P2_XMEM_FULL_CHUNK)
    {
      for (index = 0; index < P2_XMEM_FULL_CHUNK; index++)
        {
          g_p2_xmem_full_buffer[index] =
            p2_xmem_full_pattern(address + index);
        }

      ret = p2_psram_unified_transfer(P2_PSRAM_OPERATION_WRITE, address,
                                      g_p2_xmem_full_buffer,
                                      P2_XMEM_FULL_CHUNK);
      if (ret < 0)
        {
          return p2_xmem_fail("FULL", "WRITE", ret);
        }

      if (((address + P2_XMEM_FULL_CHUNK) % P2_XMEM_FULL_PROGRESS) == 0)
        {
          p2_xmem_full_progress("WRITE", address + P2_XMEM_FULL_CHUNK);
        }
    }

  for (address = 0; address < P2_PSRAM_SIZE_BYTES;
       address += P2_XMEM_FULL_CHUNK)
    {
      ret = p2_psram_unified_transfer(P2_PSRAM_OPERATION_READ, address,
                                      g_p2_xmem_full_buffer,
                                      P2_XMEM_FULL_CHUNK);
      if (ret < 0)
        {
          return p2_xmem_fail("FULL", "READ", ret);
        }

      for (index = 0; index < P2_XMEM_FULL_CHUNK; index++)
        {
          uint8_t value = g_p2_xmem_full_buffer[index];

          if (value != p2_xmem_full_pattern(address + index))
            {
              return p2_xmem_fail("FULL", "VERIFY", -EIO);
            }

          hash = (hash ^ value) * P2_XMEM_FNV_PRIME;
        }

      if (((address + P2_XMEM_FULL_CHUNK) % P2_XMEM_FULL_PROGRESS) == 0)
        {
          p2_xmem_full_progress("READ", address + P2_XMEM_FULL_CHUNK);
        }
    }

  g_p2_xmem_full_hash = hash;
  g_p2_xmem_full_complete = true;
  return 0;
}
#endif

int p2_psram_unified_selftest(void)
{
  FAR uint8_t *arena;
  FAR uint8_t *grown;
  struct stat st;
  int ret;

  p2_xmem_start_marker();

  errno = 0;
  if (stat(P2_PSRAM_DEVICE_PATH, &st) == 0)
    {
      return p2_xmem_fail("NODEV", "PRESENT", -EEXIST);
    }

  if (errno != ENOENT)
    {
      return p2_xmem_fail("NODEV", "STAT", -errno);
    }

  p2_xmem_marker("P2XMEM:NODEV:PASS");

  arena = (FAR uint8_t *)malloc(P2_XMEM_TEST_ARENA_SIZE);
  if (arena == NULL)
    {
      return p2_xmem_fail("HEAP", "ALLOC", -ENOMEM);
    }

  if (!p2_xmem_is_tagged(arena, P2_XMEM_TEST_ARENA_SIZE))
    {
      free(arena);
      return p2_xmem_fail("HEAP", "NOT_TAGGED", -EFAULT);
    }

  ret = p2_xmem_scalar_test(arena);
  if (ret < 0)
    {
      free(arena);
      return p2_xmem_fail("SCALAR", "MISMATCH", ret);
    }

  p2_xmem_marker("P2XMEM:SCALAR:PASS");

  ret = p2_xmem_bulk_test(arena);
  if (ret < 0)
    {
      free(arena);
      return p2_xmem_fail("BULK", "MISMATCH", ret);
    }

  p2_xmem_marker("P2XMEM:BULK:PASS");

  ret = p2_xmem_geometry_test(arena);
  if (ret < 0)
    {
      free(arena);
      return p2_xmem_fail("GEOMETRY", "TAGGED", ret);
    }

  ret = p2_xmem_concurrent_test(arena);
  if (ret < 0)
    {
      free(arena);
      return p2_xmem_fail("CONCURRENT", "WORKER", ret);
    }

  p2_xmem_marker("P2XMEM:CONCURRENT:PASS");

  memcpy(g_p2_xmem_expected, arena + 0x800, P2_XMEM_TEST_BULK_SIZE);
  grown = (FAR uint8_t *)realloc(arena, P2_XMEM_TEST_GROWN_SIZE);
  if (grown == NULL || !p2_xmem_is_tagged(grown, P2_XMEM_TEST_GROWN_SIZE))
    {
      free(grown == NULL ? arena : grown);
      return p2_xmem_fail("HEAP", "REALLOC", -ENOMEM);
    }

  memcpy(g_p2_xmem_result, grown + 0x800, P2_XMEM_TEST_BULK_SIZE);
  if (!p2_xmem_buffers_equal(g_p2_xmem_result, g_p2_xmem_expected,
                             P2_XMEM_TEST_BULK_SIZE))
    {
      free(grown);
      return p2_xmem_fail("HEAP", "PRESERVE", -EIO);
    }

  free(grown);
  ret = p2_xmem_fragmentation_test();
  if (ret < 0)
    {
      return p2_xmem_fail("HEAP", "FRAGMENT", ret);
    }

  p2_xmem_marker("P2XMEM:HEAP:PASS");

#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED_SELFTEST_FULL
  if (!g_p2_xmem_full_complete)
    {
      return p2_xmem_fail("FULL", "NOT_RUN", -EIO);
    }

  p2_xmem_hash_marker(g_p2_xmem_full_hash);
#endif

  p2_xmem_marker("P2XMEM:PASS");
#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED_FAULT_INJECT_RAW_LOCK
  p2_xmem_raw_lock_fault_test();
#endif
  return 0;
}
