/****************************************************************************
 * arch/p2/src/common/p2_rng.c
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

#include <sys/param.h>

#include <errno.h>
#include <stdint.h>
#include <string.h>

#include <nuttx/drivers/drivers.h>
#include <nuttx/fs/fs.h>
#ifdef CONFIG_P2_RNG_BLAKE2S
#  include <nuttx/crypto/blake2s.h>
#endif

#if defined(CONFIG_DEV_RANDOM) || defined(CONFIG_DEV_URANDOM_ARCH)

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static uint32_t p2_getrnd(void)
{
  uint32_t value;

  __asm__ __volatile__("getrnd %0" : "=r" (value));
  return value;
}

static ssize_t p2_rng_read(FAR struct file *filep, FAR char *buffer,
                           size_t buflen)
{
  size_t remaining = buflen;

  UNUSED(filep);

  while (remaining > 0)
    {
#ifdef CONFIG_P2_RNG_BLAKE2S
      uint32_t raw[16];
      uint8_t conditioned[BLAKE2S_OUTBYTES];
      size_t chunk = remaining < sizeof(conditioned) ?
                     remaining : sizeof(conditioned);
      unsigned int index;

      /* GETRND is a per-cog hardware Xoroshiro128** stream seeded from
       * thermal noise at reset.  Never expose that recoverable raw stream:
       * compress two output blocks through BLAKE2s for every block returned
       * to callers.
       */

      for (index = 0; index < nitems(raw); index++)
        {
          raw[index] = p2_getrnd();
        }

      if (blake2s(conditioned, sizeof(conditioned), raw, sizeof(raw),
                  NULL, 0) < 0)
        {
          return -EIO;
        }

      memcpy(buffer, conditioned, chunk);
#else
      uint32_t value = p2_getrnd();
      size_t chunk = remaining < sizeof(value) ? remaining : sizeof(value);

      memcpy(buffer, &value, chunk);
#endif
      buffer += chunk;
      remaining -= chunk;
    }

  return buflen;
}

/****************************************************************************
 * Private Data
 ****************************************************************************/

static const struct file_operations g_p2_rng_operations =
{
  .read = p2_rng_read,
};

/****************************************************************************
 * Public Functions
 ****************************************************************************/

#ifdef CONFIG_DEV_RANDOM
void devrandom_register(void)
{
  register_driver("/dev/random", &g_p2_rng_operations, 0444, NULL);
}
#endif

#ifdef CONFIG_DEV_URANDOM_ARCH
void devurandom_register(void)
{
  register_driver("/dev/urandom", &g_p2_rng_operations, 0444, NULL);
}
#endif

#endif /* CONFIG_DEV_RANDOM || CONFIG_DEV_URANDOM_ARCH */
