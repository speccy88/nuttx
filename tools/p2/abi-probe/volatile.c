/* SPDX-License-Identifier: Apache-2.0 */

#include "abi_probe.h"

volatile p2_u32 p2_probe_volatile_word;
volatile p2_u8 p2_probe_volatile_byte;

P2_NOINLINE p2_u32 p2_probe_volatile_access(p2_u32 value)
{
  p2_probe_volatile_word = value;
  p2_probe_volatile_byte = (p2_u8)value;
  return p2_probe_volatile_word + p2_probe_volatile_byte;
}
