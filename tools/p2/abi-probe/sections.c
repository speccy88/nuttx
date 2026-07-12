/* SPDX-License-Identifier: Apache-2.0 */

#include "abi_probe.h"

P2_USED __attribute__((weak)) void p2_probe_weak_hook(void)
{
}

P2_USED __attribute__((section(".p2probe.text")))
P2_NOINLINE p2_u32 p2_probe_custom_text(p2_u32 value)
{
  return value ^ 0x55aa55aau;
}

P2_USED __attribute__((section(".p2probe.data")))
p2_u32 p2_probe_custom_data = 0x12345678u;

P2_USED __attribute__((section(".p2probe.rodata")))
const p2_u32 p2_probe_custom_rodata = 0x87654321u;
