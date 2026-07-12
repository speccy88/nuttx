/* SPDX-License-Identifier: Apache-2.0 */

#include "abi_probe.h"

P2_NOINLINE p2_u32 p2_probe_atomic_lock_free(void)
{
  return __atomic_always_lock_free(sizeof(p2_u32), 0);
}
