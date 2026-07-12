/* SPDX-License-Identifier: Apache-2.0 */

#include "abi_probe.h"

P2_NOINLINE p2_u32 p2_probe_read_ptra(void)
{
  p2_u32 value;
  __asm__ volatile ("mov %0, ptra" : "=r" (value));
  return value;
}

P2_NOINLINE p2_u32 p2_probe_read_pa_pb(void)
{
  p2_u32 pa_value;
  p2_u32 pb_value;

  __asm__ volatile ("mov %0, pa\n\tmov %1, pb"
                    : "=r" (pa_value), "=r" (pb_value));
  return pa_value ^ pb_value;
}

P2_NOINLINE p2_u32 p2_probe_read_c_z(p2_u32 a, p2_u32 b)
{
  p2_u32 carry;
  p2_u32 zero;

  __asm__ volatile ("cmp %2, %3 wcz\n\twrc %0\n\twrz %1"
                    : "=r" (carry), "=r" (zero)
                    : "r" (a), "r" (b));
  return (carry << 1) | zero;
}

P2_NOINLINE void p2_probe_inline_qmul(p2_u32 a, p2_u32 b,
                                      p2_u32 *low_out,
                                      p2_u32 *high_out)
{
  p2_u32 low;
  p2_u32 high;

  __asm__ volatile ("qmul %2, %3\n\tgetqx %0\n\tgetqy %1"
                    : "=r" (low), "=r" (high)
                    : "r" (a), "r" (b));
  *low_out = low;
  *high_out = high;
}

P2_NOINLINE void p2_probe_inline_qdiv(p2_u32 dividend, p2_u32 divisor,
                                      p2_u32 *quotient_out,
                                      p2_u32 *remainder_out)
{
  p2_u32 quotient;
  p2_u32 remainder;

  __asm__ volatile ("qdiv %2, %3\n\tgetqx %0\n\tgetqy %1"
                    : "=r" (quotient), "=r" (remainder)
                    : "r" (dividend), "r" (divisor));
  *quotient_out = quotient;
  *remainder_out = remainder;
}
