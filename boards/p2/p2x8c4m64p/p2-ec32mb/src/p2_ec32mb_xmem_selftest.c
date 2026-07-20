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
#define P2_XMEM_CACHE_TEST_SIZE   64u
#define P2_XMEM_CACHE_ALIGNMENT   32u
#define P2_XMEM_CACHE_HITS        UINT64_C(5)
#define P2_XMEM_CACHE_MISSES      UINT64_C(2)
#define P2_XMEM_CACHE_FILLS       UINT64_C(2)
#define P2_XMEM_CACHE_WRITES      UINT64_C(2)
#define P2_XMEM_CACHE_BYPASSES    UINT64_C(1)
#define P2_XMEM_STREAM_PROBE_SIZE 32u
#define P2_XMEM_STREAM_PROBE_BASE (P2_XMEM_PAGE_BOUNDARY + 64u)
#define P2_XMEM_STREAM_WRITE_BASE (P2_XMEM_STREAM_PROBE_BASE + 128u)

#if P2_XMEM_FNV_PRIME != UINT32_C(0x01000193)
#  error "P2 PSRAM full-test FNV shift/add decomposition drifted"
#endif

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

union p2_xmem_i64_s
{
  int64_t value;
  uint32_t word[2];
};

union p2_xmem_double_s
{
  double value;
  uint32_t word[2];
};

union p2_xmem_float_s
{
  float value;
  uint32_t word;
};

/****************************************************************************
 * External Functions
 ****************************************************************************/

extern double __floatdidf(int64_t value);
extern double __floatunsidf(unsigned int value);
extern double __muldf3(double lhs, double rhs);
extern double __adddf3(double lhs, double rhs);
extern float __truncdfsf2(double value);
extern int64_t __fixdfdi(double value);

/****************************************************************************
 * Private Data
 ****************************************************************************/

static uint8_t g_p2_xmem_source[P2_XMEM_TEST_BULK_SIZE] aligned_data(4);
static uint8_t g_p2_xmem_result[P2_XMEM_TEST_BULK_SIZE] aligned_data(4);
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

static void p2_xmem_puthex32(uint32_t value)
{
  static const char hex[] = "0123456789ABCDEF";
  int shift;

  for (shift = 28; shift >= 0; shift -= 4)
    {
      up_putc(hex[(value >> shift) & 15u]);
    }
}

static noinline_function int p2_xmem_floatdidf_vector(
  uint32_t index, uint32_t input_low, uint32_t input_high,
  uint32_t expected_low, uint32_t expected_high)
{
  volatile union p2_xmem_i64_s input;
  union p2_xmem_double_s result;

  p2_xmem_puts("P2XMEM:FLOATDIDF:BEGIN:I=");
  p2_xmem_puthex32(index);
  up_putc('\r');
  up_putc('\n');

  input.word[0] = input_low;
  input.word[1] = input_high;
  result.value = __floatdidf(input.value);
  if (result.word[0] != expected_low || result.word[1] != expected_high)
    {
      p2_xmem_puts("P2XMEM:FLOATDIDF:FAIL:I=");
      p2_xmem_puthex32(index);
      p2_xmem_puts(":EXPECTED=");
      p2_xmem_puthex32(expected_high);
      p2_xmem_puthex32(expected_low);
      p2_xmem_puts(":ACTUAL=");
      p2_xmem_puthex32(result.word[1]);
      p2_xmem_puthex32(result.word[0]);
      up_putc('\r');
      up_putc('\n');
      return -EIO;
    }

  p2_xmem_puts("P2XMEM:FLOATDIDF:PASS:I=");
  p2_xmem_puthex32(index);
  up_putc('\r');
  up_putc('\n');
  return 0;
}

static int p2_xmem_floatdidf_test(void)
{
  int ret;

#define P2_XMEM_FLOATDIDF_VECTOR(i, il, ih, el, eh) \
  do \
    { \
      ret = p2_xmem_floatdidf_vector(i, il, ih, el, eh); \
      if (ret < 0) \
        { \
          return ret; \
        } \
    } \
  while (0)

  P2_XMEM_FLOATDIDF_VECTOR(0, UINT32_C(0x00000000),
                           UINT32_C(0x00000000), UINT32_C(0x00000000),
                           UINT32_C(0x00000000));
  P2_XMEM_FLOATDIDF_VECTOR(1, UINT32_C(0x00000001),
                           UINT32_C(0x00000000), UINT32_C(0x00000000),
                           UINT32_C(0x3ff00000));
  P2_XMEM_FLOATDIDF_VECTOR(2, UINT32_C(0xffffffff),
                           UINT32_C(0xffffffff), UINT32_C(0x00000000),
                           UINT32_C(0xbff00000));
  P2_XMEM_FLOATDIDF_VECTOR(3, UINT32_C(0x68e77800),
                           UINT32_C(0x00000000), UINT32_C(0x00000000),
                           UINT32_C(0x41da39de));
  P2_XMEM_FLOATDIDF_VECTOR(4, UINT32_C(0xffffffff),
                           UINT32_C(0x7fffffff), UINT32_C(0x00000000),
                           UINT32_C(0x43e00000));
  P2_XMEM_FLOATDIDF_VECTOR(5, UINT32_C(0x00000000),
                           UINT32_C(0x80000000), UINT32_C(0x00000000),
                           UINT32_C(0xc3e00000));

#undef P2_XMEM_FLOATDIDF_VECTOR

  p2_xmem_marker("P2XMEM:FLOATDIDF:ALL:PASS");
  return 0;
}

static noinline_function int p2_xmem_softfloat_zero_test(void)
{
  volatile union p2_xmem_i64_s integer;
  volatile union p2_xmem_double_s zero;
  volatile union p2_xmem_double_s nanosecond_scale;
  volatile union p2_xmem_double_s lhs;
  volatile union p2_xmem_double_s rhs;
  union p2_xmem_double_s result;
  union p2_xmem_float_s narrowed;
  union p2_xmem_i64_s fixed;

  integer.word[0] = UINT32_C(0x00000000);
  integer.word[1] = UINT32_C(0x00000000);
  p2_xmem_marker("P2XMEM:FLOATUNSIDF:BEGIN:ZERO");
  result.value = __floatunsidf((unsigned int)integer.value);
  if (result.word[0] != UINT32_C(0x00000000) ||
      result.word[1] != UINT32_C(0x00000000))
    {
      p2_xmem_puts("P2XMEM:FLOATUNSIDF:FAIL:ZERO:EXPECTED=0000000000000000:ACTUAL=");
      p2_xmem_puthex32(result.word[1]);
      p2_xmem_puthex32(result.word[0]);
      up_putc('\r');
      up_putc('\n');
      return -EIO;
    }

  p2_xmem_marker("P2XMEM:FLOATUNSIDF:PASS:ZERO");

  integer.word[0] = UINT32_C(0x00000001);
  integer.word[1] = UINT32_C(0x00000000);
  p2_xmem_marker("P2XMEM:FLOATUNSIDF:BEGIN:ONE");
  result.value = __floatunsidf((unsigned int)integer.value);
  if (result.word[0] != UINT32_C(0x00000000) ||
      result.word[1] != UINT32_C(0x3ff00000))
    {
      p2_xmem_puts("P2XMEM:FLOATUNSIDF:FAIL:ONE:EXPECTED=3FF0000000000000:ACTUAL=");
      p2_xmem_puthex32(result.word[1]);
      p2_xmem_puthex32(result.word[0]);
      up_putc('\r');
      up_putc('\n');
      return -EIO;
    }

  p2_xmem_marker("P2XMEM:FLOATUNSIDF:PASS:ONE");

  zero.word[0] = UINT32_C(0x00000000);
  zero.word[1] = UINT32_C(0x00000000);
  nanosecond_scale.word[0] = UINT32_C(0xe826d695);
  nanosecond_scale.word[1] = UINT32_C(0x3e112e0b);
  p2_xmem_marker("P2XMEM:MULDF3:BEGIN:ZERO");
  result.value = __muldf3(zero.value, nanosecond_scale.value);
  if (result.word[0] != UINT32_C(0x00000000) ||
      result.word[1] != UINT32_C(0x00000000))
    {
      return -EIO;
    }

  p2_xmem_marker("P2XMEM:MULDF3:PASS:ZERO");
  p2_xmem_marker("P2XMEM:ADDDF3:BEGIN:ZERO");
  result.value = __adddf3(zero.value, zero.value);
  if (result.word[0] != UINT32_C(0x00000000) ||
      result.word[1] != UINT32_C(0x00000000))
    {
      return -EIO;
    }

  p2_xmem_marker("P2XMEM:ADDDF3:PASS:ZERO");
  p2_xmem_marker("P2XMEM:SOFTFLOAT:PASS:ZERO");

  lhs.word[0] = UINT32_C(0x00000000);
  lhs.word[1] = UINT32_C(0x3ff80000); /* 1.5 */
  rhs.word[0] = UINT32_C(0x00000000);
  rhs.word[1] = UINT32_C(0x40000000); /* 2.0 */
  p2_xmem_marker("P2XMEM:MULDF3:BEGIN:NONZERO");
  result.value = __muldf3(lhs.value, rhs.value);
  if (result.word[0] != UINT32_C(0x00000000) ||
      result.word[1] != UINT32_C(0x40080000)) /* 3.0 */
    {
      p2_xmem_puts("P2XMEM:MULDF3:FAIL:NONZERO:EXPECTED=4008000000000000:ACTUAL=");
      p2_xmem_puthex32(result.word[1]);
      p2_xmem_puthex32(result.word[0]);
      up_putc('\r');
      up_putc('\n');
      return -EIO;
    }

  p2_xmem_marker("P2XMEM:MULDF3:PASS:NONZERO");

  rhs.word[0] = UINT32_C(0x00000000);
  rhs.word[1] = UINT32_C(0x40020000); /* 2.25 */
  p2_xmem_marker("P2XMEM:ADDDF3:BEGIN:NONZERO");
  result.value = __adddf3(lhs.value, rhs.value);
  if (result.word[0] != UINT32_C(0x00000000) ||
      result.word[1] != UINT32_C(0x400e0000)) /* 3.75 */
    {
      p2_xmem_puts("P2XMEM:ADDDF3:FAIL:NONZERO:EXPECTED=400E000000000000:ACTUAL=");
      p2_xmem_puthex32(result.word[1]);
      p2_xmem_puthex32(result.word[0]);
      up_putc('\r');
      up_putc('\n');
      return -EIO;
    }

  p2_xmem_marker("P2XMEM:ADDDF3:PASS:NONZERO");
  p2_xmem_marker("P2XMEM:SOFTFLOAT:PASS:NONZERO");

  p2_xmem_marker("P2XMEM:TRUNCDFSF2:BEGIN");
  narrowed.value = __truncdfsf2(lhs.value);
  if (narrowed.word != UINT32_C(0x3fc00000)) /* 1.5f */
    {
      p2_xmem_puts("P2XMEM:TRUNCDFSF2:FAIL:EXPECTED=3FC00000:ACTUAL=");
      p2_xmem_puthex32(narrowed.word);
      up_putc('\r');
      up_putc('\n');
      return -EIO;
    }

  p2_xmem_marker("P2XMEM:TRUNCDFSF2:PASS");

  p2_xmem_marker("P2XMEM:FIXDFDI:BEGIN");
  fixed.value = __fixdfdi(lhs.value);
  if (fixed.word[0] != UINT32_C(0x00000001) ||
      fixed.word[1] != UINT32_C(0x00000000))
    {
      p2_xmem_puts("P2XMEM:FIXDFDI:FAIL:EXPECTED=0000000000000001:ACTUAL=");
      p2_xmem_puthex32(fixed.word[1]);
      p2_xmem_puthex32(fixed.word[0]);
      up_putc('\r');
      up_putc('\n');
      return -EIO;
    }

  p2_xmem_marker("P2XMEM:FIXDFDI:PASS");
  p2_xmem_marker("P2XMEM:SOFTFLOAT:PASS:ALL");
  return 0;
}

#ifdef CONFIG_P2_EC32MB_PSRAM_UNIFIED_SELFTEST_FULL
static void p2_xmem_boundary_diag(FAR const char *test,
                                  uint32_t expected_high,
                                  uint32_t expected_low,
                                  uint32_t actual_high,
                                  uint32_t actual_low)
{
  p2_xmem_puts("P2XMEM:BOUNDARY:DIAG:CASE=");
  p2_xmem_puts(test);
  p2_xmem_puts(":EXPECTED=");
  p2_xmem_puthex32(expected_high);
  p2_xmem_puthex32(expected_low);
  p2_xmem_puts(":ACTUAL=");
  p2_xmem_puthex32(actual_high);
  p2_xmem_puthex32(actual_low);
  up_putc('\r');
  up_putc('\n');
}

static void p2_xmem_stream_probe_dump(FAR const char *stage,
                                      FAR const char *kind,
                                      FAR const uint8_t *bytes)
{
  uint32_t index;

  p2_xmem_puts("P2XMEM:");
  p2_xmem_puts(stage);
  p2_xmem_puts(":DIAG:");
  p2_xmem_puts(kind);
  up_putc('=');
  for (index = 0; index < P2_XMEM_STREAM_PROBE_SIZE; index++)
    {
      static const char hex[] = "0123456789ABCDEF";

      up_putc(hex[bytes[index] >> 4]);
      up_putc(hex[bytes[index] & 15u]);
    }

  up_putc('\r');
  up_putc('\n');
}
#endif

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

static uint32_t p2_xmem_hub_le32(FAR const uint8_t *bytes)
{
  return (uint32_t)bytes[0] |
         (uint32_t)bytes[1] << 8 |
         (uint32_t)bytes[2] << 16 |
         (uint32_t)bytes[3] << 24;
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

static int p2_xmem_cache_test(FAR uint8_t *arena)
{
  struct p2_psram_cache_stats_s before;
  struct p2_psram_cache_stats_s after;
  uint8_t setup[P2_XMEM_CACHE_TEST_SIZE];
  uint8_t expected[P2_XMEM_CACHE_TEST_SIZE];
  uint8_t patch[16];
  uint8_t readback[P2_XMEM_CACHE_TEST_SIZE];
  FAR uint8_t *target;
  uintptr_t aligned;
  uint32_t external_address;
  uint32_t index;
  uint32_t value32;
  uint8_t discard;
  int ret;

  /* Keep the two lines under test away from allocator metadata and align
   * them to the cache's 32-byte line geometry.  The largest eviction probe
   * is target + 288, well inside the one-MiB arena.
   */

  aligned = ((uintptr_t)(arena + 0x10000) +
             P2_XMEM_CACHE_ALIGNMENT - 1u) &
            ~((uintptr_t)P2_XMEM_CACHE_ALIGNMENT - 1u);
  target = (FAR uint8_t *)aligned;
  external_address = (uint32_t)(aligned - P2_PSRAM_UNIFIED_BASE);

  for (index = 0; index < P2_XMEM_CACHE_TEST_SIZE; index++)
    {
      setup[index] = (uint8_t)(0x40u + index);
      expected[index] = setup[index];
    }

  ret = p2_psram_unified_transfer(P2_PSRAM_OPERATION_WRITE,
                                  external_address, setup, sizeof(setup));
  if (ret < 0)
    {
      return ret;
    }

  /* Two other lines in each two-way set evict target and target + 32
   * regardless of the cache state left by the preceding scalar test.  Take
   * the baseline only after these setup accesses so the deltas below are
   * exact and independently reproducible.
   */

  discard = __p2_xmem_load8(target + 128u);
  discard ^= __p2_xmem_load8(target + 256u);
  discard ^= __p2_xmem_load8(target + 160u);
  discard ^= __p2_xmem_load8(target + 288u);
  (void)discard;

  ret = p2_psram_get_cache_stats(&before);
  if (ret < 0)
    {
      return ret;
    }

  value32 = __p2_xmem_load32(target + 4u);
  if (value32 != p2_xmem_hub_le32(expected + 4u) ||
      __p2_xmem_load8(target + 5u) != expected[5])
    {
      return -EIO;
    }

  __p2_xmem_store16(target + 6u, UINT16_C(0xa55a));
  expected[6] = UINT8_C(0x5a);
  expected[7] = UINT8_C(0xa5);
  if (__p2_xmem_load32(target + 4u) !=
      p2_xmem_hub_le32(expected + 4u))
    {
      return -EIO;
    }

  if (__p2_xmem_load32(target + 36u) !=
      p2_xmem_hub_le32(expected + 36u))
    {
      return -EIO;
    }

  for (index = 0; index < sizeof(patch); index++)
    {
      patch[index] = (uint8_t)(0xc0u + index);
    }

  ret = p2_psram_unified_transfer(P2_PSRAM_OPERATION_WRITE,
                                  external_address + 28u,
                                  patch, sizeof(patch));
  if (ret < 0)
    {
      return ret;
    }

  for (index = 0; index < sizeof(patch); index++)
    {
      expected[28u + index] = patch[index];
    }

  if (__p2_xmem_load32(target + 28u) !=
        p2_xmem_hub_le32(expected + 28u) ||
      __p2_xmem_load32(target + 32u) !=
        p2_xmem_hub_le32(expected + 32u) ||
      __p2_xmem_load32(target + 40u) !=
        p2_xmem_hub_le32(expected + 40u))
    {
      return -EIO;
    }

  /* This 64-byte read is intentionally not scalar-cacheable.  It proves
   * both writes reached physical PSRAM and contributes the one bypass.
   */

  ret = p2_psram_unified_transfer(P2_PSRAM_OPERATION_READ,
                                  external_address, readback,
                                  sizeof(readback));
  if (ret < 0)
    {
      return ret;
    }

  if (!p2_xmem_buffers_equal(readback, expected, sizeof(readback)))
    {
      return -EIO;
    }

  ret = p2_psram_get_cache_stats(&after);
  if (ret < 0)
    {
      return ret;
    }

  if (after.hits < before.hits ||
      after.misses < before.misses ||
      after.fills < before.fills ||
      after.writes < before.writes ||
      after.bypasses < before.bypasses ||
      after.hits - before.hits != P2_XMEM_CACHE_HITS ||
      after.misses - before.misses != P2_XMEM_CACHE_MISSES ||
      after.fills - before.fills != P2_XMEM_CACHE_FILLS ||
      after.writes - before.writes != P2_XMEM_CACHE_WRITES ||
      after.bypasses - before.bypasses != P2_XMEM_CACHE_BYPASSES)
    {
      return -ERANGE;
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
      geometry->qpi_clock_hz != 5000000u ||
      geometry->bulk_qpi_clock_hz != 90000000u ||
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
static int p2_xmem_stream_write_probe(void)
{
  uint32_t index;
  int ret;

  /* Write one aligned native-format line through RFWORD, then read it back
   * as two sub-threshold scalar chunks.  This independently proves the QPI
   * command/output half of the streamer before diagnosing read turnaround.
   */

  for (index = 0; index < P2_XMEM_STREAM_PROBE_SIZE; index++)
    {
      g_p2_xmem_source[index] =
        (uint8_t)(UINT32_C(0xa7) + index * UINT32_C(0x3b));
    }

  ret = p2_psram_unified_transfer(P2_PSRAM_OPERATION_WRITE,
                                  P2_XMEM_STREAM_WRITE_BASE,
                                  g_p2_xmem_source,
                                  P2_XMEM_STREAM_PROBE_SIZE);
  if (ret < 0)
    {
      return ret;
    }

  memset(g_p2_xmem_result, 0, P2_XMEM_STREAM_PROBE_SIZE);
  ret = p2_psram_unified_transfer(P2_PSRAM_OPERATION_READ,
                                  P2_XMEM_STREAM_WRITE_BASE,
                                  g_p2_xmem_result, 16);
  if (ret < 0)
    {
      return ret;
    }

  ret = p2_psram_unified_transfer(P2_PSRAM_OPERATION_READ,
                                  P2_XMEM_STREAM_WRITE_BASE + 16u,
                                  g_p2_xmem_result + 16, 16);
  if (ret < 0)
    {
      return ret;
    }

  if (!p2_xmem_buffers_equal(g_p2_xmem_result, g_p2_xmem_source,
                             P2_XMEM_STREAM_PROBE_SIZE))
    {
      p2_xmem_stream_probe_dump("STREAM_WRITE", "EXPECTED",
                                g_p2_xmem_source);
      p2_xmem_stream_probe_dump("STREAM_WRITE", "ACTUAL",
                                g_p2_xmem_result);
      return -EIO;
    }

  p2_xmem_marker("P2XMEM:STREAM_WRITE:PASS");
  return 0;
}

static int p2_xmem_stream_read_probe(void)
{
  uint32_t index;
  int ret;

  /* Seed one aligned cache-line-sized region through scalar whole-word
   * writes, then read it with one aligned request which must select the
   * cog-RAM streamer.  On failure, retain the complete byte sequence so
   * read-phase and rotation faults can be diagnosed from one short boot.
   */

  for (index = 0; index < P2_XMEM_STREAM_PROBE_SIZE; index++)
    {
      g_p2_xmem_source[index] =
        (uint8_t)(UINT32_C(0x31) + index * UINT32_C(0x25));
    }

  for (index = 0; index < P2_XMEM_STREAM_PROBE_SIZE; index += 4)
    {
      ret = p2_psram_unified_transfer(P2_PSRAM_OPERATION_WRITE,
                                      P2_XMEM_STREAM_PROBE_BASE + index,
                                      &g_p2_xmem_source[index], 4);
      if (ret < 0)
        {
          return ret;
        }
    }

  memset(g_p2_xmem_result, 0, P2_XMEM_STREAM_PROBE_SIZE);
  ret = p2_psram_unified_transfer(P2_PSRAM_OPERATION_READ,
                                  P2_XMEM_STREAM_PROBE_BASE,
                                  g_p2_xmem_result,
                                  P2_XMEM_STREAM_PROBE_SIZE);
  if (ret < 0)
    {
      return ret;
    }

  if (!p2_xmem_buffers_equal(g_p2_xmem_result, g_p2_xmem_source,
                             P2_XMEM_STREAM_PROBE_SIZE))
    {
      p2_xmem_stream_probe_dump("STREAM_READ", "EXPECTED",
                                g_p2_xmem_source);
      p2_xmem_stream_probe_dump("STREAM_READ", "ACTUAL",
                                g_p2_xmem_result);
      return -EIO;
    }

  p2_xmem_marker("P2XMEM:STREAM_READ:PASS");
  return 0;
}

static int p2_xmem_boundary_test(void)
{
  FAR void *address;
  uint64_t actual64;
  uint32_t actual32;
  uint16_t actual16;
  uint8_t actual8;
  int ret;

  /* Each virtual long interleaves one byte from every chip, so crossing the
   * 1-KiB page boundary on each APS6404L occurs at virtual offset 4096.
   * Exercise unaligned scalar requests that straddle that boundary.
   */

  ret = p2_xmem_stream_write_probe();
  if (ret < 0)
    {
      return ret;
    }

  ret = p2_xmem_stream_read_probe();
  if (ret < 0)
    {
      return ret;
    }

  address = (FAR void *)(P2_PSRAM_UNIFIED_BASE +
                         P2_XMEM_PAGE_BOUNDARY - 1u);
  __p2_xmem_store16(address, UINT16_C(0xa55a));
  actual16 = __p2_xmem_load16(address);
  if (actual16 != UINT16_C(0xa55a))
    {
      p2_xmem_boundary_diag("PAGE_U16", 0, UINT16_C(0xa55a), 0,
                            actual16);
      return -EIO;
    }

  address = (FAR void *)(P2_PSRAM_UNIFIED_BASE +
                         P2_XMEM_PAGE_BOUNDARY - 2u);
  __p2_xmem_store32(address, UINT32_C(0x89abcdef));
  actual32 = __p2_xmem_load32(address);
  if (actual32 != UINT32_C(0x89abcdef))
    {
      p2_xmem_boundary_diag("PAGE_U32", 0, UINT32_C(0x89abcdef), 0,
                            actual32);
      return -EIO;
    }

  address = (FAR void *)(P2_PSRAM_UNIFIED_BASE +
                         P2_XMEM_PAGE_BOUNDARY - 3u);
  __p2_xmem_store64(address, UINT64_C(0x0123456789abcdef));
  actual64 = __p2_xmem_load64(address);
  if (actual64 != UINT64_C(0x0123456789abcdef))
    {
      p2_xmem_boundary_diag("PAGE_U64", UINT32_C(0x01234567),
                            UINT32_C(0x89abcdef),
                            (uint32_t)(actual64 >> 32), (uint32_t)actual64);
      return -EIO;
    }

  /* Classifier ranges are half open.  For every scalar width, prove that an
   * access whose last byte is 0x11ffffff remains valid and reaches PSRAM.
   */

  address = (FAR void *)(P2_PSRAM_UNIFIED_END - 1u);
  __p2_xmem_store8(address, UINT8_C(0x3c));
  actual8 = __p2_xmem_load8(address);
  if (actual8 != UINT8_C(0x3c))
    {
      p2_xmem_boundary_diag("END_U8", 0, UINT8_C(0x3c), 0, actual8);
      return -EIO;
    }

  address = (FAR void *)(P2_PSRAM_UNIFIED_END - 2u);
  __p2_xmem_store16(address, UINT16_C(0x5aa5));
  actual16 = __p2_xmem_load16(address);
  if (actual16 != UINT16_C(0x5aa5))
    {
      p2_xmem_boundary_diag("END_U16", 0, UINT16_C(0x5aa5), 0,
                            actual16);
      return -EIO;
    }

  address = (FAR void *)(P2_PSRAM_UNIFIED_END - 4u);
  __p2_xmem_store32(address, UINT32_C(0xfedcba98));
  actual32 = __p2_xmem_load32(address);
  if (actual32 != UINT32_C(0xfedcba98))
    {
      p2_xmem_boundary_diag("END_U32", 0, UINT32_C(0xfedcba98), 0,
                            actual32);
      return -EIO;
    }

  address = (FAR void *)(P2_PSRAM_UNIFIED_END - 8u);
  __p2_xmem_store64(address, UINT64_C(0xfedcba9876543210));
  actual64 = __p2_xmem_load64(address);
  if (actual64 != UINT64_C(0xfedcba9876543210))
    {
      p2_xmem_boundary_diag("END_U64", UINT32_C(0xfedcba98),
                            UINT32_C(0x76543210),
                            (uint32_t)(actual64 >> 32), (uint32_t)actual64);
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

static inline_function uint32_t p2_xmem_full_fnv_multiply(uint32_t value)
{
  uint32_t times3;
  uint32_t times25;
  uint32_t times403;

  /* 0x01000193 = 2^24 + 403.  Optimizer barriers keep this exact
   * addition chain from being folded back into the P2 toolchain's
   * generic multiplication helper.  The barriers emit no instructions.
   */

  times3 = value << 1;
  __asm__ __volatile__("" : "+r" (times3));
  times3 += value;
  __asm__ __volatile__("" : "+r" (times3));
  times25 = (times3 << 3) + value;
  __asm__ __volatile__("" : "+r" (times25));
  times403 = (times25 << 4) + times3;
  __asm__ __volatile__("" : "+r" (times403));
  return (value << 24) + times403;
}

/* These helpers operate only on the fixed Hub buffer above.  Their
 * __p2_xmem_ names deliberately place them on the compiler pass's native
 * Hub-access side of the unified-memory recursion boundary.
 */

static noinline_function void __p2_xmem_full_fill(uint32_t address)
{
  uint32_t index;

  for (index = 0; index < P2_XMEM_FULL_CHUNK; index++)
    {
      g_p2_xmem_full_buffer[index] =
        p2_xmem_full_pattern(address + index);
    }
}

static noinline_function int
__p2_xmem_full_verify_hash(uint32_t address, FAR uint32_t *hash)
{
  uint32_t current = *hash;
  uint32_t index;

  for (index = 0; index < P2_XMEM_FULL_CHUNK; index++)
    {
      uint8_t value = g_p2_xmem_full_buffer[index];

      if (value != p2_xmem_full_pattern(address + index))
        {
          return -EIO;
        }

      current = p2_xmem_full_fnv_multiply(current ^ value);
    }

  *hash = current;
  return 0;
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
      __p2_xmem_full_fill(address);

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

      ret = __p2_xmem_full_verify_hash(address, &hash);
      if (ret < 0)
        {
          return p2_xmem_fail("FULL", "VERIFY", ret);
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

  ret = p2_xmem_softfloat_zero_test();
  if (ret < 0)
    {
      return p2_xmem_fail("SOFTFLOAT", "ZERO", ret);
    }

  ret = p2_xmem_floatdidf_test();
  if (ret < 0)
    {
      return p2_xmem_fail("FLOATDIDF", "MISMATCH", ret);
    }

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

  ret = p2_xmem_cache_test(arena);
  if (ret < 0)
    {
      free(arena);
      return p2_xmem_fail("CACHE", "COHERENCE", ret);
    }

  p2_xmem_marker("P2XMEM:CACHE:PASS:HITS=5:MISSES=2:FILLS=2:"
                 "WRITES=2:BYPASSES=1");

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
