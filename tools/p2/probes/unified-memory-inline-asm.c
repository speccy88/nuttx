/* SPDX-License-Identifier: Apache-2.0 */

/*
 * Negative compiler probe: inline assembly is opaque to helper lowering, so
 * unified memory must reject an unproven pointer used by inline assembly.
 */

typedef unsigned int p2_probe_u32_t;

__attribute__((noinline, used)) p2_probe_u32_t
p2_probe_dynamic_inline_asm(const volatile p2_probe_u32_t *pointer)
{
  p2_probe_u32_t value;

  __asm__ __volatile__("rdlong %0, %1"
                       : "=r" (value)
                       : "r" (pointer)
                       : "memory");
  return value;
}
