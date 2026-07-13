/* SPDX-License-Identifier: Apache-2.0 */

#include "abi_probe.h"

P2_NOINLINE p2_u32 p2_probe_s64_lt(p2_s64 left, p2_s64 right)
{
  return left < right;
}

P2_NOINLINE p2_u32 p2_probe_s64_ge(p2_s64 left, p2_s64 right)
{
  return left >= right;
}

P2_NOINLINE p2_u32 p2_probe_s64_le(p2_s64 left, p2_s64 right)
{
  return left <= right;
}

P2_NOINLINE p2_u32 p2_probe_s64_gt(p2_s64 left, p2_s64 right)
{
  return left > right;
}

P2_NOINLINE p2_u32 p2_probe_u64_lt(p2_u64 left, p2_u64 right)
{
  return left < right;
}

P2_NOINLINE p2_u32 p2_probe_u64_ge(p2_u64 left, p2_u64 right)
{
  return left >= right;
}

P2_NOINLINE p2_u32 p2_probe_u64_le(p2_u64 left, p2_u64 right)
{
  return left <= right;
}

P2_NOINLINE p2_u32 p2_probe_u64_gt(p2_u64 left, p2_u64 right)
{
  return left > right;
}

P2_NOINLINE p2_u32 p2_probe_s64_lt_large_low(p2_s64 value)
{
  return value < (p2_s64)0x1234567889abcdefULL;
}

P2_NOINLINE p2_u32 p2_probe_u64_lt_large_low(p2_u64 value)
{
  return value < 0x9234567889abcdefULL;
}
