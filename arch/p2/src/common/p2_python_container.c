/****************************************************************************
 * arch/p2/src/common/p2_python_container.c
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

#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <arch/overlay.h>
#include <arch/python_container.h>

#ifdef CONFIG_ARCH_P2
#  include <arch/hub_crc32.h>
#endif

/* The initializer and loader must remain executable while the overlay slot
 * is empty or being replaced.  Newer p2llvm builds understand this
 * attribute; the feature test keeps the source buildable with the bootstrap
 * compiler.
 */

#ifndef __has_attribute
#  define __has_attribute(name) 0
#endif

#if __has_attribute(p2_hub_resident)
#  pragma clang attribute push(__attribute__((p2_hub_resident)), \
                                apply_to = function)
#  define P2_CONTAINER_RESIDENT_PRAGMA 1
#endif

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_CONTAINER_MAGIC_SIZE                 8
#define P2_CONTAINER_HEADER_SIZE                UINT32_C(192)
#define P2_CONTAINER_SECTION_SIZE               UINT32_C(96)
#define P2_CONTAINER_GROUP_SIZE                 UINT32_C(16)
#define P2_CONTAINER_STUB_SIZE                  UINT32_C(8)
#define P2_CONTAINER_STUB_NAME_SIZE             UINT32_C(8)
#define P2_CONTAINER_ALIGNMENT                  UINT32_C(16)
#define P2_CONTAINER_MAX_MANIFEST               UINT32_C(0x02000000)
#define P2_CONTAINER_MAX_SECTIONS               UINT32_C(65535)
#define P2_CONTAINER_MAX_STUBS                  UINT32_C(1048576)
#define P2_CONTAINER_MAX_NAME                   UINT32_C(1024)
#define P2_CONTAINER_MAX_SECTION_ALIGNMENT      UINT32_C(0x00100000)
#define P2_CONTAINER_IO_SIZE                    512

#define P2_CONTAINER_VERSION_MAJOR              UINT32_C(1)
#define P2_CONTAINER_VERSION_MINOR              UINT32_C(0)
#define P2_CONTAINER_ENDIAN_TAG                 UINT32_C(0x01020304)

#define P2_CONTAINER_MANIFEST_DIGEST_OFFSET     UINT32_C(0x90)
#define P2_CONTAINER_MANIFEST_DIGEST_SIZE       UINT32_C(32)

#define P2_CONTAINER_HEADER_FLAG_EXTERNAL_INIT  UINT32_C(0x01)
#define P2_CONTAINER_HEADER_FLAG_EXTERNAL_ZERO  UINT32_C(0x02)
#define P2_CONTAINER_HEADER_FLAG_OVERLAYS       UINT32_C(0x04)
#define P2_CONTAINER_HEADER_FLAG_STUBS          UINT32_C(0x08)
#define P2_CONTAINER_HEADER_FLAG_ROMFS          UINT32_C(0x10)
#define P2_CONTAINER_HEADER_FLAG_MASK            UINT32_C(0x1f)

#define P2_CONTAINER_SECTION_EXTERNAL_INIT      UINT32_C(1)
#define P2_CONTAINER_SECTION_EXTERNAL_ZERO      UINT32_C(2)
#define P2_CONTAINER_SECTION_OVERLAY            UINT32_C(3)
#define P2_CONTAINER_SECTION_ROMFS              UINT32_C(4)

#define P2_CONTAINER_CODEC_NONE                 UINT32_C(0)

#define P2_CONTAINER_SECTION_FLAG_REQUIRED      UINT32_C(0x01)
#define P2_CONTAINER_SECTION_FLAG_READ_ONLY     UINT32_C(0x02)
#define P2_CONTAINER_SECTION_FLAG_EXECUTABLE    UINT32_C(0x04)
#define P2_CONTAINER_SECTION_FLAG_FIXED         UINT32_C(0x08)
#define P2_CONTAINER_SECTION_FLAG_MASK          UINT32_C(0x0f)

#define P2_CONTAINER_PSRAM_BASE                 UINT32_C(0x10000000)
#define P2_CONTAINER_PSRAM_END                  UINT32_C(0x12000000)
#define P2_CONTAINER_HUB_LOAD_END               UINT32_C(0x0007c000)

#define P2_CONTAINER_STATE_READY                UINT32_C(0x50325059)
#ifndef CONFIG_ARCH_P2
#  define P2_CONTAINER_CRC_POLYNOMIAL            UINT32_C(0xedb88320)
#endif

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct p2_container_sha256_s
{
  uint32_t state[8];
  uint64_t bytes;
  size_t used;
  uint8_t block[64];
};

struct p2_container_header_s
{
  uint32_t flags;
  uint32_t section_count;
  uint32_t group_count;
  uint32_t stub_count;
  uint32_t section_table_offset;
  uint32_t group_table_offset;
  uint32_t stub_table_offset;
  uint32_t stub_name_table_offset;
  uint32_t string_table_offset;
  uint32_t string_table_size;
  uint32_t manifest_size;
  uint32_t file_size;
  uint32_t overlay_load_address;
  uint32_t overlay_slot_size;
  uint32_t overlay_first_section;
  uint32_t overlay_count;
  uint32_t romfs_file_offset;
  uint32_t romfs_size;
};

struct p2_container_section_s
{
  uint32_t type;
  uint32_t codec;
  uint32_t flags;
  uint32_t id;
  uint32_t name_offset;
  uint32_t name_length;
  uint32_t alignment;
  uint64_t virtual_address;
  uint64_t file_offset;
  uint64_t stored_size;
  uint64_t memory_size;
  uint64_t uncompressed_size;
  uint32_t crc32;
};

struct p2_container_backing_s
{
  FAR const struct p2_python_container_target_s *target;
  uintptr_t base;
  size_t size;
};

/****************************************************************************
 * Private Data
 ****************************************************************************/

static const uint8_t g_p2_container_magic[P2_CONTAINER_MAGIC_SIZE] =
{
  'P', '2', 'P', 'Y', 'C', 'T', 'N', 0
};

static const uint32_t g_p2_container_sha256_k[64] =
{
  UINT32_C(0x428a2f98), UINT32_C(0x71374491),
  UINT32_C(0xb5c0fbcf), UINT32_C(0xe9b5dba5),
  UINT32_C(0x3956c25b), UINT32_C(0x59f111f1),
  UINT32_C(0x923f82a4), UINT32_C(0xab1c5ed5),
  UINT32_C(0xd807aa98), UINT32_C(0x12835b01),
  UINT32_C(0x243185be), UINT32_C(0x550c7dc3),
  UINT32_C(0x72be5d74), UINT32_C(0x80deb1fe),
  UINT32_C(0x9bdc06a7), UINT32_C(0xc19bf174),
  UINT32_C(0xe49b69c1), UINT32_C(0xefbe4786),
  UINT32_C(0x0fc19dc6), UINT32_C(0x240ca1cc),
  UINT32_C(0x2de92c6f), UINT32_C(0x4a7484aa),
  UINT32_C(0x5cb0a9dc), UINT32_C(0x76f988da),
  UINT32_C(0x983e5152), UINT32_C(0xa831c66d),
  UINT32_C(0xb00327c8), UINT32_C(0xbf597fc7),
  UINT32_C(0xc6e00bf3), UINT32_C(0xd5a79147),
  UINT32_C(0x06ca6351), UINT32_C(0x14292967),
  UINT32_C(0x27b70a85), UINT32_C(0x2e1b2138),
  UINT32_C(0x4d2c6dfc), UINT32_C(0x53380d13),
  UINT32_C(0x650a7354), UINT32_C(0x766a0abb),
  UINT32_C(0x81c2c92e), UINT32_C(0x92722c85),
  UINT32_C(0xa2bfe8a1), UINT32_C(0xa81a664b),
  UINT32_C(0xc24b8b70), UINT32_C(0xc76c51a3),
  UINT32_C(0xd192e819), UINT32_C(0xd6990624),
  UINT32_C(0xf40e3585), UINT32_C(0x106aa070),
  UINT32_C(0x19a4c116), UINT32_C(0x1e376c08),
  UINT32_C(0x2748774c), UINT32_C(0x34b0bcb5),
  UINT32_C(0x391c0cb3), UINT32_C(0x4ed8aa4a),
  UINT32_C(0x5b9cca4f), UINT32_C(0x682e6ff3),
  UINT32_C(0x748f82ee), UINT32_C(0x78a5636f),
  UINT32_C(0x84c87814), UINT32_C(0x8cc70208),
  UINT32_C(0x90befffa), UINT32_C(0xa4506ceb),
  UINT32_C(0xbef9a3f7), UINT32_C(0xc67178f2)
};

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static uint16_t p2_container_getle16(FAR const uint8_t *value)
{
  return (uint16_t)((uint16_t)value[0] | (uint16_t)value[1] << 8);
}

static uint32_t p2_container_getle32(FAR const uint8_t *value)
{
  return (uint32_t)value[0] | (uint32_t)value[1] << 8 |
         (uint32_t)value[2] << 16 | (uint32_t)value[3] << 24;
}

static uint64_t p2_container_getle64(FAR const uint8_t *value)
{
  return (uint64_t)p2_container_getle32(value) |
         (uint64_t)p2_container_getle32(value + 4) << 32;
}

static void p2_container_putbe32(FAR uint8_t *value, uint32_t word)
{
  value[0] = (uint8_t)(word >> 24);
  value[1] = (uint8_t)(word >> 16);
  value[2] = (uint8_t)(word >> 8);
  value[3] = (uint8_t)word;
}

static bool p2_container_zero(FAR const uint8_t *value, size_t size)
{
  size_t index;

  for (index = 0; index < size; index++)
    {
      if (value[index] != 0)
        {
          return false;
        }
    }

  return true;
}

static bool p2_container_add(uint64_t left, uint64_t right,
                             FAR uint64_t *result)
{
  if (left > UINT64_MAX - right)
    {
      return false;
    }

  *result = left + right;
  return true;
}

static bool p2_container_mul(uint32_t left, uint32_t right,
                             FAR uint64_t *result)
{
  /* Both operands are 32-bit table counts or fixed entry sizes.  Widen
   * before multiplication so this operation itself cannot overflow.
   */

  *result = (uint64_t)left * right;
  return true;
}

static bool p2_container_align(uint64_t value, uint32_t alignment,
                               FAR uint64_t *result)
{
  uint64_t mask;

  if (alignment == 0 || (alignment & (alignment - 1)) != 0)
    {
      return false;
    }

  mask = alignment - 1;
  if (value > UINT64_MAX - mask)
    {
      return false;
    }

  *result = (value + mask) & ~mask;
  return true;
}

static int p2_container_callback_result(int ret)
{
  return ret <= 0 ? ret : -EIO;
}

static int p2_container_read(
  FAR const struct p2_python_container_source_s *source, uint64_t offset,
  FAR void *buffer, size_t size)
{
  int ret;

  if (source == NULL || source->read == NULL ||
      offset > source->size || size > source->size - offset)
    {
      return -EILSEQ;
    }

  ret = source->read(source->arg, offset, buffer, size);
  return p2_container_callback_result(ret);
}

static int p2_container_target_read(
  FAR const struct p2_python_container_target_s *target, uint64_t address,
  FAR void *buffer, size_t size)
{
  int ret;

  if (target->read == NULL)
    {
      memcpy(buffer, (FAR const void *)(uintptr_t)address, size);
      return 0;
    }

  ret = target->read(target->arg, address, buffer, size);
  return p2_container_callback_result(ret);
}

static int p2_container_target_write(
  FAR const struct p2_python_container_target_s *target, uint64_t address,
  FAR const void *buffer, size_t size)
{
  int ret;

  if (target->write == NULL)
    {
      memcpy((FAR void *)(uintptr_t)address, buffer, size);
      return 0;
    }

  ret = target->write(target->arg, address, buffer, size);
  return p2_container_callback_result(ret);
}

static int p2_container_target_zero(
  FAR const struct p2_python_container_target_s *target, uint64_t address,
  size_t size)
{
  uint8_t zeroes[P2_CONTAINER_IO_SIZE];
  int ret;

  if (target->zero != NULL)
    {
      ret = target->zero(target->arg, address, size);
      return p2_container_callback_result(ret);
    }

  if (target->write == NULL)
    {
      memset((FAR void *)(uintptr_t)address, 0, size);
      return 0;
    }

  memset(zeroes, 0, sizeof(zeroes));
  while (size != 0)
    {
      size_t chunk = size < sizeof(zeroes) ? size : sizeof(zeroes);

      ret = p2_container_target_write(target, address, zeroes, chunk);
      if (ret < 0)
        {
          return ret;
        }

      address += chunk;
      size -= chunk;
    }

  return 0;
}

static uint32_t p2_container_ror(uint32_t value, unsigned int shift)
{
  return value >> shift | value << (32 - shift);
}

static void p2_container_sha256_transform(
  FAR struct p2_container_sha256_s *context, FAR const uint8_t *block)
{
  uint32_t words[64];
  uint32_t a;
  uint32_t b;
  uint32_t c;
  uint32_t d;
  uint32_t e;
  uint32_t f;
  uint32_t g;
  uint32_t h;
  unsigned int index;

  for (index = 0; index < 16; index++)
    {
      words[index] = (uint32_t)block[index * 4] << 24 |
                     (uint32_t)block[index * 4 + 1] << 16 |
                     (uint32_t)block[index * 4 + 2] << 8 |
                     (uint32_t)block[index * 4 + 3];
    }

  for (; index < 64; index++)
    {
      uint32_t s0 = p2_container_ror(words[index - 15], 7) ^
                    p2_container_ror(words[index - 15], 18) ^
                    (words[index - 15] >> 3);
      uint32_t s1 = p2_container_ror(words[index - 2], 17) ^
                    p2_container_ror(words[index - 2], 19) ^
                    (words[index - 2] >> 10);

      words[index] = words[index - 16] + s0 + words[index - 7] + s1;
    }

  a = context->state[0];
  b = context->state[1];
  c = context->state[2];
  d = context->state[3];
  e = context->state[4];
  f = context->state[5];
  g = context->state[6];
  h = context->state[7];

  for (index = 0; index < 64; index++)
    {
      uint32_t sum1 = p2_container_ror(e, 6) ^
                      p2_container_ror(e, 11) ^
                      p2_container_ror(e, 25);
      uint32_t choice = (e & f) ^ (~e & g);
      uint32_t temporary1 = h + sum1 + choice +
                            g_p2_container_sha256_k[index] + words[index];
      uint32_t sum0 = p2_container_ror(a, 2) ^
                      p2_container_ror(a, 13) ^
                      p2_container_ror(a, 22);
      uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
      uint32_t temporary2 = sum0 + majority;

      h = g;
      g = f;
      f = e;
      e = d + temporary1;
      d = c;
      c = b;
      b = a;
      a = temporary1 + temporary2;
    }

  context->state[0] += a;
  context->state[1] += b;
  context->state[2] += c;
  context->state[3] += d;
  context->state[4] += e;
  context->state[5] += f;
  context->state[6] += g;
  context->state[7] += h;
}

static void p2_container_sha256_init(
  FAR struct p2_container_sha256_s *context)
{
  static const uint32_t initial[8] =
  {
    UINT32_C(0x6a09e667), UINT32_C(0xbb67ae85),
    UINT32_C(0x3c6ef372), UINT32_C(0xa54ff53a),
    UINT32_C(0x510e527f), UINT32_C(0x9b05688c),
    UINT32_C(0x1f83d9ab), UINT32_C(0x5be0cd19)
  };

  memcpy(context->state, initial, sizeof(initial));
  context->bytes = 0;
  context->used = 0;
}

static void p2_container_sha256_update(
  FAR struct p2_container_sha256_s *context, FAR const uint8_t *data,
  size_t size)
{
  context->bytes += size;
  while (size != 0)
    {
      size_t available = sizeof(context->block) - context->used;
      size_t chunk = size < available ? size : available;

      memcpy(context->block + context->used, data, chunk);
      context->used += chunk;
      data += chunk;
      size -= chunk;
      if (context->used == sizeof(context->block))
        {
          p2_container_sha256_transform(context, context->block);
          context->used = 0;
        }
    }
}

static void p2_container_sha256_final(
  FAR struct p2_container_sha256_s *context, FAR uint8_t digest[32])
{
  uint64_t bits = context->bytes * 8;
  unsigned int index;

  context->block[context->used++] = UINT8_C(0x80);
  if (context->used > 56)
    {
      memset(context->block + context->used, 0,
             sizeof(context->block) - context->used);
      p2_container_sha256_transform(context, context->block);
      context->used = 0;
    }

  memset(context->block + context->used, 0, 56 - context->used);
  for (index = 0; index < 8; index++)
    {
      context->block[56 + index] = (uint8_t)(bits >> (56 - index * 8));
    }

  p2_container_sha256_transform(context, context->block);
  for (index = 0; index < 8; index++)
    {
      p2_container_putbe32(digest + index * 4, context->state[index]);
    }
}

static int p2_container_manifest_sha256(
  FAR const struct p2_python_container_source_s *source,
  uint32_t manifest_size, FAR uint8_t digest[32])
{
  struct p2_container_sha256_s sha;
  uint8_t buffer[P2_CONTAINER_IO_SIZE];
  uint64_t offset = 0;
  int ret;

  p2_container_sha256_init(&sha);
  while (offset < manifest_size)
    {
      size_t chunk = manifest_size - offset;
      uint64_t end;

      if (chunk > sizeof(buffer))
        {
          chunk = sizeof(buffer);
        }

      ret = p2_container_read(source, offset, buffer, chunk);
      if (ret < 0)
        {
          return ret;
        }

      end = offset + chunk;
      if (offset < P2_CONTAINER_MANIFEST_DIGEST_OFFSET +
                   P2_CONTAINER_MANIFEST_DIGEST_SIZE &&
          end > P2_CONTAINER_MANIFEST_DIGEST_OFFSET)
        {
          uint64_t first = offset > P2_CONTAINER_MANIFEST_DIGEST_OFFSET ?
                           offset : P2_CONTAINER_MANIFEST_DIGEST_OFFSET;
          uint64_t last =
            end < P2_CONTAINER_MANIFEST_DIGEST_OFFSET +
                  P2_CONTAINER_MANIFEST_DIGEST_SIZE ?
            end : P2_CONTAINER_MANIFEST_DIGEST_OFFSET +
                  P2_CONTAINER_MANIFEST_DIGEST_SIZE;

          memset(buffer + first - offset, 0, last - first);
        }

      p2_container_sha256_update(&sha, buffer, chunk);
      offset = end;
    }

  p2_container_sha256_final(&sha, digest);
  return 0;
}

static int p2_container_check_zero(
  FAR const struct p2_python_container_source_s *source, uint64_t offset,
  uint64_t size)
{
  uint8_t buffer[128];
  int ret;

  while (size != 0)
    {
      size_t chunk = size < sizeof(buffer) ? (size_t)size : sizeof(buffer);

      ret = p2_container_read(source, offset, buffer, chunk);
      if (ret < 0)
        {
          return ret;
        }

      if (!p2_container_zero(buffer, chunk))
        {
          return -EILSEQ;
        }

      offset += chunk;
      size -= chunk;
    }

  return 0;
}

static uint32_t p2_container_crc32_update(uint32_t crc,
                                          FAR const uint8_t *data,
                                          size_t size)
{
#ifdef CONFIG_ARCH_P2
  return p2_hub_crc32_update(crc, data, size);
#else
  size_t index;

  for (index = 0; index < size; index++)
    {
      unsigned int bit;

      crc ^= data[index];
      for (bit = 0; bit < 8; bit++)
        {
          crc = crc >> 1 ^
                ((crc & 1) != 0 ? P2_CONTAINER_CRC_POLYNOMIAL : 0);
        }
    }

  return crc;
#endif
}

static int p2_container_crc32(
  FAR const struct p2_python_container_source_s *source, uint64_t offset,
  uint64_t size, FAR uint32_t *result)
{
  uint8_t buffer[P2_CONTAINER_IO_SIZE];
  uint32_t crc = UINT32_C(0xffffffff);
  int ret;

  while (size != 0)
    {
      size_t chunk = size < sizeof(buffer) ? (size_t)size : sizeof(buffer);
      ret = p2_container_read(source, offset, buffer, chunk);
      if (ret < 0)
        {
          return ret;
        }

      crc = p2_container_crc32_update(crc, buffer, chunk);

      offset += chunk;
      size -= chunk;
    }

  *result = crc ^ UINT32_C(0xffffffff);
  return 0;
}

static int p2_container_validate_name(
  FAR const struct p2_python_container_source_s *source,
  FAR const struct p2_container_header_s *header, uint32_t offset,
  uint32_t length)
{
  uint8_t buffer[128];
  uint32_t remaining = length;
  uint64_t address;
  uint32_t codepoint = 0;
  uint32_t minimum = 0;
  unsigned int continuation = 0;
  int ret;

  if (length == 0 || length > P2_CONTAINER_MAX_NAME ||
      offset > header->string_table_size ||
      length > header->string_table_size - offset)
    {
      return -EILSEQ;
    }

  address = (uint64_t)header->string_table_offset + offset;
  while (remaining != 0)
    {
      size_t chunk = remaining < sizeof(buffer) ?
                     remaining : sizeof(buffer);
      size_t index;

      ret = p2_container_read(source, address, buffer, chunk);
      if (ret < 0)
        {
          return ret;
        }

      for (index = 0; index < chunk; index++)
        {
          uint8_t byte = buffer[index];

          if (continuation == 0)
            {
              if (byte < UINT8_C(0x80))
                {
                  if (byte < UINT8_C(0x20))
                    {
                      return -EILSEQ;
                    }

                  continue;
                }
              else if (byte >= UINT8_C(0xc2) && byte <= UINT8_C(0xdf))
                {
                  continuation = 1;
                  codepoint = byte & UINT8_C(0x1f);
                  minimum = UINT32_C(0x80);
                }
              else if (byte >= UINT8_C(0xe0) && byte <= UINT8_C(0xef))
                {
                  continuation = 2;
                  codepoint = byte & UINT8_C(0x0f);
                  minimum = UINT32_C(0x800);
                }
              else if (byte >= UINT8_C(0xf0) && byte <= UINT8_C(0xf4))
                {
                  continuation = 3;
                  codepoint = byte & UINT8_C(0x07);
                  minimum = UINT32_C(0x10000);
                }
              else
                {
                  return -EILSEQ;
                }
            }
          else
            {
              if ((byte & UINT8_C(0xc0)) != UINT8_C(0x80))
                {
                  return -EILSEQ;
                }

              codepoint = codepoint << 6 | (byte & UINT8_C(0x3f));
              continuation--;
              if (continuation == 0 &&
                  (codepoint < minimum || codepoint > UINT32_C(0x10ffff) ||
                   (codepoint >= UINT32_C(0xd800) &&
                    codepoint <= UINT32_C(0xdfff))))
                {
                  return -EILSEQ;
                }
            }
        }

      address += chunk;
      remaining -= chunk;
    }

  return continuation == 0 ? 0 : -EILSEQ;
}

static int p2_container_read_section(
  FAR const struct p2_python_container_source_s *source,
  FAR const struct p2_container_header_s *header, uint32_t index,
  FAR struct p2_container_section_s *section)
{
  uint8_t entry[P2_CONTAINER_SECTION_SIZE];
  uint64_t offset = (uint64_t)header->section_table_offset +
                    (uint64_t)index * P2_CONTAINER_SECTION_SIZE;
  int ret;

  ret = p2_container_read(source, offset, entry, sizeof(entry));
  if (ret < 0)
    {
      return ret;
    }

  if (p2_container_getle32(entry + 24) != 0 ||
      p2_container_getle32(entry + 72) != 0 ||
      !p2_container_zero(entry + 76, 20))
    {
      return -EILSEQ;
    }

  section->type = p2_container_getle16(entry);
  section->codec = p2_container_getle16(entry + 2);
  section->flags = p2_container_getle32(entry + 4);
  section->id = p2_container_getle32(entry + 8);
  section->name_offset = p2_container_getle32(entry + 12);
  section->name_length = p2_container_getle32(entry + 16);
  section->alignment = p2_container_getle32(entry + 20);
  section->virtual_address = p2_container_getle64(entry + 28);
  section->file_offset = p2_container_getle64(entry + 36);
  section->stored_size = p2_container_getle64(entry + 44);
  section->memory_size = p2_container_getle64(entry + 52);
  section->uncompressed_size = p2_container_getle64(entry + 60);
  section->crc32 = p2_container_getle32(entry + 68);
  return 0;
}

static int p2_container_validate_flags(
  FAR const struct p2_container_section_s *section)
{
  uint32_t flags = section->flags;

  if ((flags & ~P2_CONTAINER_SECTION_FLAG_MASK) != 0 ||
      (flags & P2_CONTAINER_SECTION_FLAG_REQUIRED) == 0)
    {
      return -ENOTSUP;
    }

  switch (section->type)
    {
      case P2_CONTAINER_SECTION_EXTERNAL_INIT:
        if ((flags & P2_CONTAINER_SECTION_FLAG_FIXED) == 0 ||
            (flags & P2_CONTAINER_SECTION_FLAG_EXECUTABLE) != 0)
          {
            return -ENOTSUP;
          }
        break;

      case P2_CONTAINER_SECTION_EXTERNAL_ZERO:
        if ((flags & P2_CONTAINER_SECTION_FLAG_FIXED) == 0 ||
            (flags & (P2_CONTAINER_SECTION_FLAG_READ_ONLY |
                      P2_CONTAINER_SECTION_FLAG_EXECUTABLE)) != 0)
          {
            return -ENOTSUP;
          }
        break;

      case P2_CONTAINER_SECTION_OVERLAY:
        if (flags != P2_OVERLAY_GROUP_FLAGS_PACKED_V1)
          {
            return -ENOTSUP;
          }
        break;

      case P2_CONTAINER_SECTION_ROMFS:
        if (flags != (P2_CONTAINER_SECTION_FLAG_REQUIRED |
                      P2_CONTAINER_SECTION_FLAG_READ_ONLY))
          {
            return -ENOTSUP;
          }
        break;

      default:
        return -ENOTSUP;
    }

  return 0;
}

static int p2_container_validate_external_overlap(
  FAR const struct p2_python_container_source_s *source,
  FAR const struct p2_container_header_s *header, uint32_t index,
  FAR const struct p2_container_section_s *section)
{
  uint64_t end = section->virtual_address + section->memory_size;
  uint32_t prior;

  for (prior = 0; prior < index; prior++)
    {
      struct p2_container_section_s other;
      uint64_t other_end;
      int ret = p2_container_read_section(source, header, prior, &other);

      if (ret < 0)
        {
          return ret;
        }

      if (other.type != P2_CONTAINER_SECTION_EXTERNAL_INIT &&
          other.type != P2_CONTAINER_SECTION_EXTERNAL_ZERO)
        {
          continue;
        }

      other_end = other.virtual_address + other.memory_size;
      if (section->virtual_address < other_end &&
          other.virtual_address < end)
        {
          return -EADDRINUSE;
        }
    }

  return 0;
}

static int p2_container_validate_header(
  FAR const struct p2_python_container_source_s *source,
  FAR const struct p2_python_container_contract_s *contract,
  FAR struct p2_container_header_s *header)
{
  uint8_t raw[P2_CONTAINER_HEADER_SIZE];
  uint8_t digest[32];
  uint64_t offset;
  uint64_t size;
  uint64_t end;
  uint64_t wide[8];
  uint8_t difference = 0;
  size_t index;
  int ret;

  if (source == NULL || contract == NULL || source->read == NULL ||
      source->size < P2_CONTAINER_HEADER_SIZE ||
      source->size > UINT32_MAX ||
      p2_container_zero(contract->build_fingerprint,
                        sizeof(contract->build_fingerprint)))
    {
      return -EINVAL;
    }

  ret = p2_container_read(source, 0, raw, sizeof(raw));
  if (ret < 0)
    {
      return ret;
    }

  if (memcmp(raw, g_p2_container_magic, sizeof(g_p2_container_magic)) != 0 ||
      p2_container_getle16(raw + 8) != P2_CONTAINER_VERSION_MAJOR ||
      p2_container_getle16(raw + 10) != P2_CONTAINER_VERSION_MINOR ||
      p2_container_getle16(raw + 12) != P2_CONTAINER_HEADER_SIZE ||
      p2_container_getle16(raw + 14) != P2_CONTAINER_SECTION_SIZE ||
      p2_container_getle16(raw + 16) != P2_CONTAINER_GROUP_SIZE ||
      p2_container_getle16(raw + 18) != P2_CONTAINER_STUB_SIZE ||
      p2_container_getle16(raw + 20) != P2_CONTAINER_STUB_NAME_SIZE ||
      p2_container_getle16(raw + 22) != 0 ||
      p2_container_getle32(raw + 28) != P2_CONTAINER_ENDIAN_TAG ||
      p2_container_getle32(raw + 44) != 0 ||
      !p2_container_zero(raw + 184, 8))
    {
      return -EILSEQ;
    }

  memset(header, 0, sizeof(*header));
  header->flags = p2_container_getle32(raw + 24);
  header->section_count = p2_container_getle32(raw + 32);
  header->group_count = p2_container_getle32(raw + 36);
  header->stub_count = p2_container_getle32(raw + 40);
  for (index = 0; index < 8; index++)
    {
      wide[index] = p2_container_getle64(raw + 48 + index * 8);
      if (wide[index] > UINT32_MAX)
        {
          return -EOVERFLOW;
        }
    }

  header->section_table_offset = (uint32_t)wide[0];
  header->group_table_offset = (uint32_t)wide[1];
  header->stub_table_offset = (uint32_t)wide[2];
  header->stub_name_table_offset = (uint32_t)wide[3];
  header->string_table_offset = (uint32_t)wide[4];
  header->string_table_size = (uint32_t)wide[5];
  header->manifest_size = (uint32_t)wide[6];
  header->file_size = (uint32_t)wide[7];
  header->overlay_load_address = p2_container_getle32(raw + 176);
  header->overlay_slot_size = p2_container_getle32(raw + 180);

  if ((header->flags & ~P2_CONTAINER_HEADER_FLAG_MASK) != 0 ||
      header->section_count > P2_CONTAINER_MAX_SECTIONS ||
      header->group_count > P2_CONTAINER_MAX_SECTIONS ||
      header->stub_count > P2_CONTAINER_MAX_STUBS ||
      header->file_size != source->size ||
      header->manifest_size > P2_CONTAINER_MAX_MANIFEST ||
      header->manifest_size > header->file_size ||
      header->overlay_load_address != contract->overlay_load_address ||
      header->overlay_slot_size != contract->overlay_slot_size ||
      header->overlay_load_address == 0 ||
      (header->overlay_load_address & 3) != 0 ||
      header->overlay_slot_size == 0 ||
      (header->overlay_slot_size & 3) != 0 ||
      header->overlay_load_address > P2_CONTAINER_HUB_LOAD_END ||
      header->overlay_slot_size > P2_CONTAINER_HUB_LOAD_END -
                                  header->overlay_load_address)
    {
      return -ENOEXEC;
    }

  for (index = 0; index < sizeof(contract->build_fingerprint); index++)
    {
      difference |= raw[112 + index] ^ contract->build_fingerprint[index];
    }

  if (difference != 0)
    {
      return -ENOEXEC;
    }

  offset = P2_CONTAINER_HEADER_SIZE;
  if (!p2_container_mul(header->section_count, P2_CONTAINER_SECTION_SIZE,
                        &size) ||
      !p2_container_add(offset, size, &end) ||
      header->section_table_offset != offset)
    {
      return -EOVERFLOW;
    }

  offset = end;
  if (!p2_container_mul(header->group_count, P2_CONTAINER_GROUP_SIZE,
                        &size) ||
      !p2_container_add(offset, size, &end) ||
      header->group_table_offset != offset)
    {
      return -EOVERFLOW;
    }

  offset = end;
  if (!p2_container_mul(header->stub_count, P2_CONTAINER_STUB_SIZE, &size) ||
      !p2_container_add(offset, size, &end) ||
      header->stub_table_offset != offset)
    {
      return -EOVERFLOW;
    }

  offset = end;
  if (!p2_container_mul(header->stub_count,
                        P2_CONTAINER_STUB_NAME_SIZE, &size) ||
      !p2_container_add(offset, size, &end) ||
      header->stub_name_table_offset != offset)
    {
      return -EOVERFLOW;
    }

  offset = end;
  if (!p2_container_add(offset, header->string_table_size, &end) ||
      header->string_table_offset != offset ||
      !p2_container_align(end, P2_CONTAINER_ALIGNMENT, &offset) ||
      header->manifest_size != offset)
    {
      return -EOVERFLOW;
    }

  ret = p2_container_manifest_sha256(source, header->manifest_size, digest);
  if (ret < 0)
    {
      return ret;
    }

  difference = 0;
  for (index = 0; index < sizeof(digest); index++)
    {
      difference |= digest[index] ^ raw[144 + index];
    }

  if (difference != 0)
    {
      return -EILSEQ;
    }

  end = (uint64_t)header->string_table_offset + header->string_table_size;
  return p2_container_check_zero(source, end,
                                 header->manifest_size - end);
}

static int p2_container_validate_sections(
  FAR const struct p2_python_container_source_s *source,
  FAR struct p2_container_header_s *header)
{
  uint32_t expected_flags = 0;
  uint32_t previous_type = 0;
  uint32_t expected_id = 0;
  uint64_t file_cursor = header->manifest_size;
  uint32_t index;

  for (index = 0; index < header->section_count; index++)
    {
      struct p2_container_section_s section;
      uint64_t end;
      uint64_t expected_offset;
      uint32_t checksum;
      uint32_t alignment;
      int ret = p2_container_read_section(source, header, index, &section);

      if (ret < 0)
        {
          return ret;
        }

      if (section.type < P2_CONTAINER_SECTION_EXTERNAL_INIT ||
          section.type > P2_CONTAINER_SECTION_ROMFS ||
          section.type < previous_type ||
          section.codec != P2_CONTAINER_CODEC_NONE ||
          section.alignment == 0 ||
          section.alignment > P2_CONTAINER_MAX_SECTION_ALIGNMENT ||
          (section.alignment & (section.alignment - 1)) != 0)
        {
          return -ENOTSUP;
        }

      if (section.type != previous_type)
        {
          expected_id = section.type == P2_CONTAINER_SECTION_OVERLAY ? 1 : 0;
          previous_type = section.type;
        }

      if (section.id != expected_id++)
        {
          return -EILSEQ;
        }

      ret = p2_container_validate_flags(&section);
      if (ret < 0)
        {
          return ret;
        }

      ret = p2_container_validate_name(source, header, section.name_offset,
                                       section.name_length);
      if (ret < 0)
        {
          return ret;
        }

      if (section.type == P2_CONTAINER_SECTION_EXTERNAL_ZERO)
        {
          if (section.file_offset != 0 || section.stored_size != 0 ||
              section.memory_size == 0 || section.memory_size > UINT32_MAX ||
              section.uncompressed_size != 0 || section.crc32 != 0)
            {
              return -EILSEQ;
            }
        }
      else if (section.stored_size == 0 ||
               section.stored_size > UINT32_MAX ||
               section.memory_size != section.stored_size ||
               section.uncompressed_size != section.stored_size)
        {
          return -EILSEQ;
        }

      switch (section.type)
        {
          case P2_CONTAINER_SECTION_EXTERNAL_INIT:
          case P2_CONTAINER_SECTION_EXTERNAL_ZERO:
            if (!p2_container_add(section.virtual_address,
                                  section.memory_size, &end) ||
                section.virtual_address < P2_CONTAINER_PSRAM_BASE ||
                end > P2_CONTAINER_PSRAM_END ||
                (section.virtual_address & (section.alignment - 1)) != 0)
              {
                return -ERANGE;
              }

            ret = p2_container_validate_external_overlap(source, header,
                                                         index, &section);
            if (ret < 0)
              {
                return ret;
              }

            expected_flags |=
              section.type == P2_CONTAINER_SECTION_EXTERNAL_INIT ?
              P2_CONTAINER_HEADER_FLAG_EXTERNAL_INIT :
              P2_CONTAINER_HEADER_FLAG_EXTERNAL_ZERO;
            break;

          case P2_CONTAINER_SECTION_OVERLAY:
            if (header->overlay_count == 0)
              {
                header->overlay_first_section = index;
              }

            header->overlay_count++;
            expected_flags |= P2_CONTAINER_HEADER_FLAG_OVERLAYS;
            if (section.virtual_address != header->overlay_load_address ||
                section.memory_size > header->overlay_slot_size ||
                (section.memory_size & 3) != 0 ||
                !p2_container_add(section.virtual_address,
                                  section.memory_size, &end) ||
                end > P2_CONTAINER_HUB_LOAD_END ||
                (section.virtual_address & (section.alignment - 1)) != 0)
              {
                return -ERANGE;
              }
            break;

          case P2_CONTAINER_SECTION_ROMFS:
            expected_flags |= P2_CONTAINER_HEADER_FLAG_ROMFS;
            if (header->romfs_size != 0 || section.id != 0 ||
                section.virtual_address != 0)
              {
                return -EILSEQ;
              }

            header->romfs_file_offset = (uint32_t)section.file_offset;
            header->romfs_size = (uint32_t)section.memory_size;
            break;

          default:
            return -ENOTSUP;
        }

      if (section.type == P2_CONTAINER_SECTION_EXTERNAL_ZERO)
        {
          continue;
        }

      alignment = section.alignment > P2_CONTAINER_ALIGNMENT ?
                  section.alignment : P2_CONTAINER_ALIGNMENT;
      if (!p2_container_align(file_cursor, alignment, &expected_offset) ||
          section.file_offset != expected_offset ||
          !p2_container_add(section.file_offset, section.stored_size,
                            &end) ||
          end > header->file_size)
        {
          return -EILSEQ;
        }

      ret = p2_container_check_zero(source, file_cursor,
                                    expected_offset - file_cursor);
      if (ret < 0)
        {
          return ret;
        }

      ret = p2_container_crc32(source, section.file_offset,
                               section.stored_size, &checksum);
      if (ret < 0)
        {
          return ret;
        }

      if (checksum != section.crc32)
        {
          return -EILSEQ;
        }

      file_cursor = end;
    }

  if (header->stub_count != 0)
    {
      expected_flags |= P2_CONTAINER_HEADER_FLAG_STUBS;
    }

  if (file_cursor != header->file_size || header->overlay_count == 0 ||
      header->romfs_size == 0 || header->stub_count == 0 ||
      header->group_count != header->overlay_count + 1 ||
      header->flags != expected_flags)
    {
      return -EILSEQ;
    }

  return 0;
}

static int p2_container_read_group(
  FAR const struct p2_python_container_source_s *source,
  FAR const struct p2_container_header_s *header, uint32_t group,
  FAR uint32_t record[4])
{
  uint8_t raw[P2_CONTAINER_GROUP_SIZE];
  uint64_t offset = (uint64_t)header->group_table_offset +
                    (uint64_t)group * P2_CONTAINER_GROUP_SIZE;
  unsigned int index;
  int ret = p2_container_read(source, offset, raw, sizeof(raw));

  if (ret < 0)
    {
      return ret;
    }

  for (index = 0; index < 4; index++)
    {
      record[index] = p2_container_getle32(raw + index * 4);
    }

  return 0;
}

static int p2_container_validate_groups(
  FAR const struct p2_python_container_source_s *source,
  FAR const struct p2_container_header_s *header)
{
  uint32_t group;

  for (group = 0; group < header->group_count; group++)
    {
      uint32_t record[4];
      int ret = p2_container_read_group(source, header, group, record);

      if (ret < 0)
        {
          return ret;
        }

      if (group == P2_OVERLAY_RESIDENT_GROUP)
        {
          if (record[0] != 0 || record[1] != 0 ||
              record[2] != 0 || record[3] != 0)
            {
              return -EILSEQ;
            }
        }
      else
        {
          struct p2_container_section_s section;

          ret = p2_container_read_section(
            source, header, header->overlay_first_section + group - 1,
            &section);
          if (ret < 0)
            {
              return ret;
            }

          if (record[0] != section.file_offset ||
              record[1] != section.uncompressed_size ||
              record[2] != section.crc32 || record[3] != section.flags)
            {
              return -EILSEQ;
            }
        }
    }

  return 0;
}

static int p2_container_validate_stubs(
  FAR const struct p2_python_container_source_s *source,
  FAR const struct p2_container_header_s *header)
{
  uint32_t index;

  for (index = 0; index < header->stub_count; index++)
    {
      uint8_t raw[P2_CONTAINER_STUB_SIZE];
      uint8_t name[P2_CONTAINER_STUB_NAME_SIZE];
      uint32_t record[4];
      uint32_t group;
      uint32_t entry;
      uint64_t offset;
      int ret;

      offset = (uint64_t)header->stub_table_offset +
               (uint64_t)index * P2_CONTAINER_STUB_SIZE;
      ret = p2_container_read(source, offset, raw, sizeof(raw));
      if (ret < 0)
        {
          return ret;
        }

      offset = (uint64_t)header->stub_name_table_offset +
               (uint64_t)index * P2_CONTAINER_STUB_NAME_SIZE;
      ret = p2_container_read(source, offset, name, sizeof(name));
      if (ret < 0)
        {
          return ret;
        }

      group = p2_container_getle32(raw);
      entry = p2_container_getle32(raw + 4);
      if (group == P2_OVERLAY_RESIDENT_GROUP ||
          group >= header->group_count || (entry & 3) != 0)
        {
          return -EILSEQ;
        }

      ret = p2_container_read_group(source, header, group, record);
      if (ret < 0)
        {
          return ret;
        }

      if (record[1] < 4 || entry > record[1] - 4)
        {
          return -ERANGE;
        }

      ret = p2_container_validate_name(source, header,
                                       p2_container_getle32(name),
                                       p2_container_getle32(name + 4));
      if (ret < 0)
        {
          return ret;
        }
    }

  return 0;
}

static int p2_container_validate_internal(
  FAR const struct p2_python_container_source_s *source,
  FAR const struct p2_python_container_contract_s *contract,
  FAR struct p2_container_header_s *header)
{
  int ret = p2_container_validate_header(source, contract, header);

  if (ret < 0)
    {
      return ret;
    }

  ret = p2_container_validate_sections(source, header);
  if (ret < 0)
    {
      return ret;
    }

  ret = p2_container_validate_groups(source, header);
  if (ret < 0)
    {
      return ret;
    }

  return p2_container_validate_stubs(source, header);
}

static int p2_container_backing_read(FAR void *arg, uint64_t offset,
                                     FAR void *buffer, size_t size)
{
  FAR struct p2_container_backing_s *backing = arg;

  if (offset > backing->size || size > backing->size - offset ||
      backing->base > UINT64_MAX - offset)
    {
      return -EILSEQ;
    }

  return p2_container_target_read(backing->target,
                                  backing->base + offset, buffer, size);
}

static int p2_container_copy_to_backing(
  FAR const struct p2_python_container_source_s *source,
  FAR const struct p2_python_container_target_s *target,
  uintptr_t backing_address, uint32_t size)
{
  uint8_t buffer[P2_CONTAINER_IO_SIZE];
  uint64_t offset = 0;
  int ret;

  while (offset < size)
    {
      size_t chunk = size - offset;

      if (chunk > sizeof(buffer))
        {
          chunk = sizeof(buffer);
        }

      ret = p2_container_read(source, offset, buffer, chunk);
      if (ret < 0)
        {
          return ret;
        }

      ret = p2_container_target_write(target, backing_address + offset,
                                      buffer, chunk);
      if (ret < 0)
        {
          return ret;
        }

      offset += chunk;
    }

  return 0;
}

static int p2_container_backing_overlap(
  FAR const struct p2_python_container_source_s *source,
  FAR const struct p2_container_header_s *header, uintptr_t backing_address)
{
  uint64_t backing_end = (uint64_t)backing_address + header->file_size;
  uint32_t index;

  for (index = 0; index < header->section_count; index++)
    {
      struct p2_container_section_s section;
      uint64_t end;
      int ret = p2_container_read_section(source, header, index, &section);

      if (ret < 0)
        {
          return ret;
        }

      if (section.type != P2_CONTAINER_SECTION_EXTERNAL_INIT &&
          section.type != P2_CONTAINER_SECTION_EXTERNAL_ZERO)
        {
          continue;
        }

      end = section.virtual_address + section.memory_size;
      if ((uint64_t)backing_address < end &&
          section.virtual_address < backing_end)
        {
          return -EADDRINUSE;
        }
    }

  return 0;
}

static int p2_container_initialize_external(
  FAR const struct p2_python_container_source_s *backing,
  FAR const struct p2_python_container_target_s *target,
  FAR const struct p2_container_header_s *header)
{
  uint8_t buffer[P2_CONTAINER_IO_SIZE];
  uint32_t index;

  for (index = 0; index < header->section_count; index++)
    {
      struct p2_container_section_s section;
      uint64_t offset;
      uint64_t remaining;
      int ret = p2_container_read_section(backing, header, index, &section);

      if (ret < 0)
        {
          return ret;
        }

      if (section.type == P2_CONTAINER_SECTION_EXTERNAL_ZERO)
        {
          ret = p2_container_target_zero(target, section.virtual_address,
                                         section.memory_size);
          if (ret < 0)
            {
              return ret;
            }
        }
      else if (section.type == P2_CONTAINER_SECTION_EXTERNAL_INIT)
        {
          offset = section.file_offset;
          remaining = section.stored_size;
          while (remaining != 0)
            {
              size_t chunk = remaining < sizeof(buffer) ?
                             (size_t)remaining : sizeof(buffer);

              ret = p2_container_read(backing, offset, buffer, chunk);
              if (ret < 0)
                {
                  return ret;
                }

              ret = p2_container_target_write(
                target, section.virtual_address +
                        (offset - section.file_offset), buffer, chunk);
              if (ret < 0)
                {
                  return ret;
                }

              offset += chunk;
              remaining -= chunk;
            }
        }
    }

  return 0;
}

static int p2_container_copy_groups(
  FAR const struct p2_python_container_source_s *backing,
  FAR const struct p2_container_header_s *header,
  FAR struct p2_overlay_group_s *workspace, size_t capacity)
{
  uint32_t group;

  if (workspace == NULL || capacity < header->group_count)
    {
      return -ENOSPC;
    }

  for (group = 0; group < header->group_count; group++)
    {
      uint32_t record[4];
      int ret = p2_container_read_group(backing, header, group, record);

      if (ret < 0)
        {
          return ret;
        }

      workspace[group].source = record[0];
      workspace[group].image_size = record[1];
      workspace[group].image_crc32 = record[2];
      workspace[group].flags = record[3];
    }

  return 0;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int p2_python_container_memory_read(FAR void *arg, uint64_t address,
                                    FAR void *buffer, size_t size)
{
  FAR struct p2_python_container_memory_s *memory = arg;
  uint64_t offset;

  if (memory == NULL || memory->data == NULL || buffer == NULL ||
      address < memory->address)
    {
      return -EINVAL;
    }

  offset = address - memory->address;
  if (offset > memory->size || size > memory->size - offset)
    {
      return -ERANGE;
    }

  memcpy(buffer, memory->data + offset, size);
  return 0;
}

int p2_python_container_memory_write(FAR void *arg, uint64_t address,
                                     FAR const void *buffer, size_t size)
{
  FAR struct p2_python_container_memory_s *memory = arg;
  uint64_t offset;

  if (memory == NULL || memory->data == NULL || buffer == NULL ||
      address < memory->address)
    {
      return -EINVAL;
    }

  offset = address - memory->address;
  if (offset > memory->size || size > memory->size - offset)
    {
      return -ERANGE;
    }

  memcpy(memory->data + offset, buffer, size);
  return 0;
}

int p2_python_container_memory_zero(FAR void *arg, uint64_t address,
                                    size_t size)
{
  FAR struct p2_python_container_memory_s *memory = arg;
  uint64_t offset;

  if (memory == NULL || memory->data == NULL || address < memory->address)
    {
      return -EINVAL;
    }

  offset = address - memory->address;
  if (offset > memory->size || size > memory->size - offset)
    {
      return -ERANGE;
    }

  memset(memory->data + offset, 0, size);
  return 0;
}

int p2_python_container_validate(
  FAR const struct p2_python_container_source_s *source,
  FAR const struct p2_python_container_contract_s *contract,
  FAR struct p2_python_container_info_s *info)
{
  struct p2_container_header_s header;
  int ret = p2_container_validate_internal(source, contract, &header);

  if (ret < 0)
    {
      return ret;
    }

  if (info != NULL)
    {
      info->file_size = header.file_size;
      info->manifest_size = header.manifest_size;
      info->section_count = header.section_count;
      info->group_count = header.group_count;
      info->stub_count = header.stub_count;
      info->overlay_load_address = header.overlay_load_address;
      info->overlay_slot_size = header.overlay_slot_size;
    }

  return 0;
}

int p2_python_container_initialize(
  FAR struct p2_python_container_s *container,
  FAR const struct p2_python_container_config_s *config)
{
  struct p2_container_backing_s backing_arg;
  struct p2_python_container_source_s backing;
  FAR const struct p2_python_container_source_s *validated_source;
  struct p2_container_header_s header;
  uint64_t end;
  int ret;

  if (container == NULL || config == NULL)
    {
      return -EINVAL;
    }

  memset(container, 0, sizeof(*container));
  if ((config->target.read == NULL) != (config->target.write == NULL) ||
      config->source.read == NULL ||
      config->backing_address < P2_CONTAINER_PSRAM_BASE ||
      (config->backing_address & (P2_CONTAINER_ALIGNMENT - 1)) != 0 ||
      config->backing_capacity == 0 ||
      !p2_container_add(config->backing_address,
                        config->backing_capacity, &end) ||
      end > P2_CONTAINER_PSRAM_END)
    {
      return -EINVAL;
    }

  /* An in-place claim is intentionally redundant: the enable bit, exact
   * tagged address, and exact source size must all agree.  This prevents a
   * partially initialized config or a range that merely overlaps the
   * backing window from selecting the copy-free path.
   */

  if ((!config->source_is_backing &&
       (config->source_backing_address != 0 ||
        config->source_backing_size != 0)) ||
      (config->source_is_backing &&
       (config->source_backing_address != config->backing_address ||
        config->source_backing_size == 0 ||
        config->source_backing_size != config->source.size)))
    {
      return -EINVAL;
    }

  if (config->source_is_backing &&
      config->source_backing_size > config->backing_capacity)
    {
      return -ENOSPC;
    }

  backing_arg.target = &config->target;
  backing_arg.base = config->backing_address;
  backing_arg.size = config->source.size;
  backing.read = p2_container_backing_read;
  backing.arg = &backing_arg;
  backing.size = config->source.size;

  /* In-place mode validates bytes through the target view, so a bad alias
   * claim cannot cause unvalidated source bytes to be published as backing.
   * The ordinary path continues to validate the independent source first.
   */

  validated_source = config->source_is_backing ? &backing : &config->source;
  ret = p2_container_validate_internal(validated_source, &config->contract,
                                       &header);
  if (ret < 0)
    {
      return ret;
    }

  if (header.file_size > config->backing_capacity ||
      config->group_workspace == NULL ||
      config->group_workspace_count < header.group_count)
    {
      return -ENOSPC;
    }

  ret = p2_container_backing_overlap(validated_source, &header,
                                     config->backing_address);
  if (ret < 0)
    {
      return ret;
    }

  if (!config->source_is_backing)
    {
      ret = p2_container_copy_to_backing(&config->source, &config->target,
                                         config->backing_address,
                                         header.file_size);
      if (ret < 0)
        {
          return ret;
        }

      ret = p2_container_validate_internal(&backing, &config->contract,
                                           &header);
      if (ret < 0)
        {
          return ret;
        }
    }

  ret = p2_container_copy_groups(&backing, &header,
                                 config->group_workspace,
                                 config->group_workspace_count);
  if (ret < 0)
    {
      return ret;
    }

  ret = p2_container_initialize_external(&backing, &config->target, &header);
  if (ret < 0)
    {
      return ret;
    }

  container->target = config->target;
  container->backing_address = config->backing_address;
  container->backing_size = header.file_size;
  container->group_table_offset = header.group_table_offset;
  container->group_count = header.group_count;
  container->stdlib_romfs = config->backing_address +
                            header.romfs_file_offset;
  container->stdlib_romfs_size = header.romfs_size;
  container->overlay_load_address = header.overlay_load_address;
  container->overlay_slot_size = header.overlay_slot_size;

  ret = p2_overlay_install_groups(config->group_workspace,
                                  header.group_count,
                                  config->backing_address,
                                  header.file_size);
  if (ret < 0)
    {
      memset(container, 0, sizeof(*container));
      return ret;
    }

  container->state = P2_CONTAINER_STATE_READY;
  ret = p2_overlay_register_loader(p2_python_container_overlay_loader,
                                   container);
  if (ret < 0)
    {
      int rollback = p2_overlay_uninstall_groups();

      memset(container, 0, sizeof(*container));
      return rollback < 0 ? rollback : ret;
    }

  return 0;
}

int p2_python_container_get_stdlib(
  FAR const struct p2_python_container_s *container,
  FAR const void **address, FAR size_t *size)
{
  if (container == NULL || address == NULL || size == NULL ||
      container->state != P2_CONTAINER_STATE_READY ||
      container->stdlib_romfs == 0 || container->stdlib_romfs_size == 0)
    {
      return -EINVAL;
    }

  *address = (FAR const void *)container->stdlib_romfs;
  *size = container->stdlib_romfs_size;
  return 0;
}

int p2_python_container_overlay_loader(FAR void *arg, uint32_t group,
                                       uintptr_t source,
                                       FAR void *destination,
                                       size_t image_size)
{
  FAR struct p2_python_container_s *container = arg;
  struct p2_overlay_group_s descriptor;
  uint64_t source_offset;
  int ret;

  if (container == NULL || container->state != P2_CONTAINER_STATE_READY ||
      group == P2_OVERLAY_RESIDENT_GROUP ||
      group >= container->group_count || image_size == 0 ||
      image_size > container->overlay_slot_size ||
      destination != (FAR void *)(uintptr_t)
                     container->overlay_load_address)
    {
      return -EINVAL;
    }

  ret = p2_overlay_get_group(group, &descriptor);
  if (ret < 0)
    {
      return ret;
    }

  if (descriptor.source != source || descriptor.image_size != image_size ||
      descriptor.flags != P2_OVERLAY_GROUP_FLAGS_PACKED_V1 ||
      source < container->backing_address)
    {
      return -EILSEQ;
    }

  source_offset = (uint64_t)source - container->backing_address;
  if (source_offset > container->backing_size ||
      image_size > container->backing_size - source_offset)
    {
      return -EILSEQ;
    }

  return p2_container_target_read(&container->target, source, destination,
                                  image_size);
}

#ifdef P2_CONTAINER_RESIDENT_PRAGMA
#  pragma clang attribute pop
#endif
