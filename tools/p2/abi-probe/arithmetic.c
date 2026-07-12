/* SPDX-License-Identifier: Apache-2.0 */

#include "abi_probe.h"

P2_NOINLINE p2_u32 p2_probe_u32_arithmetic(p2_u32 a, p2_u32 b)
{
  p2_u32 divisor = b | 1u;
  return (a * b) ^ (a / divisor) ^ (a % divisor);
}

P2_NOINLINE p2_s32 p2_probe_s32_arithmetic(p2_s32 a, p2_s32 b)
{
  p2_s32 divisor = b == 0 ? 1 : b;
  return (a * b) ^ (a / divisor) ^ (a % divisor);
}

P2_NOINLINE p2_u64 p2_probe_u64_arithmetic(p2_u64 a, p2_u64 b)
{
  return (a + b) ^ (a - b) ^ (a * b) ^ (a << (b & 31u));
}

P2_NOINLINE p2_s64 p2_probe_s64_arithmetic(p2_s64 a, p2_s64 b)
{
  return (a + b) ^ (a - b) ^ (a * b) ^ (a >> ((p2_u64)b & 31u));
}
