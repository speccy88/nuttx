/* SPDX-License-Identifier: Apache-2.0 */

#include <stdarg.h>

#include "abi_probe.h"

struct p2_probe_pair
{
  p2_u32 a;
  p2_u32 b;
};

struct p2_probe_large
{
  p2_u32 words[6];
};

P2_NOINLINE struct p2_probe_pair
p2_probe_pair_by_value(struct p2_probe_pair value, p2_u32 addend)
{
  value.a += addend;
  value.b ^= addend;
  return value;
}

P2_NOINLINE struct p2_probe_large
p2_probe_large_return(p2_u32 seed)
{
  struct p2_probe_large value;
  unsigned int i;

  for (i = 0; i < 6u; i++)
    {
      value.words[i] = seed;
      seed += 0x101u;
    }

  return value;
}

P2_NOINLINE p2_u32 p2_probe_large_by_value(struct p2_probe_large value)
{
  return value.words[0] ^ value.words[1] ^ value.words[2] ^
         value.words[3] ^ value.words[4] ^ value.words[5];
}

P2_NOINLINE p2_u32 p2_probe_varargs(unsigned int count, ...)
{
  va_list ap;
  p2_u32 total = 0;
  unsigned int i;

  va_start(ap, count);
  for (i = 0; i < count; i++)
    {
      total += va_arg(ap, p2_u32);
    }

  va_end(ap);
  return total;
}

P2_NOINLINE p2_u32 p2_probe_varargs_caller(void)
{
  return p2_probe_varargs(6u, 1u, 2u, 3u, 4u, 5u, 6u);
}
