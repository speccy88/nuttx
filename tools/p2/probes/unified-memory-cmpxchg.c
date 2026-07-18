/* SPDX-License-Identifier: Apache-2.0 */

/* Positive probe for NuttX's serialized fixed-width compare-exchange ABI. */

typedef unsigned int p2_probe_u32_t;

__attribute__((noinline, used)) int
p2_probe_dynamic_cmpxchg(volatile p2_probe_u32_t *pointer,
                         p2_probe_u32_t *expected,
                         p2_probe_u32_t desired)
{
  return __atomic_compare_exchange_n(pointer, expected, desired, 0,
                                     __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
}
