/* SPDX-License-Identifier: Apache-2.0 */

#include "abi_probe.h"

P2_NOINLINE p2_u32 p2_probe_atomic_load(const p2_u32 *value)
{
  return __atomic_load_n(value, __ATOMIC_SEQ_CST);
}

P2_NOINLINE void p2_probe_atomic_store(p2_u32 *target, p2_u32 value)
{
  __atomic_store_n(target, value, __ATOMIC_SEQ_CST);
}
