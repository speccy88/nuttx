/* SPDX-License-Identifier: Apache-2.0 */

#include "abi_probe.h"

P2_NOINLINE void p2_probe_memcpy_fixed(void *destination, const void *source)
{
  __builtin_memcpy(destination, source, 32u);
}

P2_NOINLINE void p2_probe_memset_fixed(void *destination)
{
  __builtin_memset(destination, 0xa5, 32u);
}

P2_NOINLINE void p2_probe_memcpy_dynamic(void *destination,
                                         const void *source,
                                         p2_size_t length)
{
  __builtin_memcpy(destination, source, length);
}

P2_NOINLINE void p2_probe_memset_dynamic(void *destination,
                                         p2_u8 value,
                                         p2_size_t length)
{
  __builtin_memset(destination, value, length);
}
