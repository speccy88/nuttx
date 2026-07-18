/* SPDX-License-Identifier: Apache-2.0 */

/* Negative compiler probe: NuttX does not provide this fixed-width ABI. */

typedef unsigned int p2_probe_u32_t;

__attribute__((noinline, used)) p2_probe_u32_t
p2_probe_dynamic_atomic_nand(volatile p2_probe_u32_t *pointer,
                             p2_probe_u32_t value)
{
  return __atomic_fetch_nand(pointer, value, __ATOMIC_SEQ_CST);
}
