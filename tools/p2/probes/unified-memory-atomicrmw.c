/* SPDX-License-Identifier: Apache-2.0 */

/* Positive probe for NuttX's serialized fixed-width atomic helper ABI. */

typedef unsigned int p2_probe_u32_t;

__attribute__((noinline, used)) p2_probe_u32_t
p2_probe_dynamic_atomicrmw(volatile p2_probe_u32_t *pointer,
                           p2_probe_u32_t value)
{
  return __atomic_fetch_add(pointer, value, __ATOMIC_SEQ_CST);
}
