/* SPDX-License-Identifier: Apache-2.0 */

#include "abi_probe.h"

extern p2_u32 p2_probe_nonleaf(p2_u32 value);
extern p2_u32 p2_probe_switch(p2_u32 value);
extern p2_u32 p2_probe_custom_text(p2_u32 value);
extern p2_u32 p2_probe_volatile_access(p2_u32 value);
extern p2_u32 p2_probe_read_ptra(void);

volatile p2_u32 p2_probe_link_sink;

P2_USED __attribute__((section(".p2probe.entry")))
void _start(void)
{
  p2_u32 value = p2_probe_nonleaf(7u);
  value ^= p2_probe_switch(value);
  value ^= p2_probe_custom_text(value);
  value ^= p2_probe_volatile_access(value);
  value ^= p2_probe_read_ptra();
  p2_probe_link_sink = value;
}
