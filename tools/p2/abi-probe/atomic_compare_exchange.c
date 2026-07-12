/* SPDX-License-Identifier: Apache-2.0 */

#include "abi_probe.h"

P2_NOINLINE p2_u32 p2_probe_atomic_compare_exchange(p2_u32 *target,
                                                     p2_u32 expected,
                                                     p2_u32 desired)
{
  return __atomic_compare_exchange_n(target, &expected, desired, 0,
                                     __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
}
