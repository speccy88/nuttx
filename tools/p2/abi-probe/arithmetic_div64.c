/* SPDX-License-Identifier: Apache-2.0 */

#include "abi_probe.h"

P2_NOINLINE p2_u64 p2_probe_u64_divmod(p2_u64 a, p2_u64 b)
{
  p2_u64 divisor = b | 1ull;
  return (a / divisor) ^ (a % divisor);
}

P2_NOINLINE p2_s64 p2_probe_s64_divmod(p2_s64 a, p2_s64 b)
{
  p2_s64 divisor = b == 0 ? 1 : b;
  return (a / divisor) ^ (a % divisor);
}
