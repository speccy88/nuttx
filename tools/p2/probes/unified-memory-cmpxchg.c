/* SPDX-License-Identifier: Apache-2.0 */

/* Negative compiler probe: unified memory must reject a dynamic cmpxchg. */

typedef unsigned int p2_probe_u32_t;

__attribute__((noinline, used)) int
p2_probe_dynamic_cmpxchg(volatile p2_probe_u32_t *pointer,
                         p2_probe_u32_t *expected,
                         p2_probe_u32_t desired)
{
  return __atomic_compare_exchange_n(pointer, expected, desired, 0,
                                     __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
}
