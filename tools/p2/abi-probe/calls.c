/* SPDX-License-Identifier: Apache-2.0 */

#include "abi_probe.h"

volatile p2_u32 p2_probe_call_sink;

P2_NOINLINE p2_u32 p2_probe_leaf(p2_u32 value)
{
  return (value ^ 0x13579bdfu) + 3u;
}

P2_NOINLINE p2_u32 p2_probe_nonleaf(p2_u32 value)
{
  p2_u32 first = p2_probe_leaf(value);
  p2_probe_call_sink = first;
  return p2_probe_leaf(first + 1u);
}

P2_NOINLINE p2_u32 p2_probe_nested3(p2_u32 value)
{
  return p2_probe_nonleaf(p2_probe_leaf(value));
}

P2_NOINLINE p2_u32 p2_probe_recursive(p2_u32 value)
{
  if (value < 2u)
    {
      return value;
    }

  return p2_probe_recursive(value - 1u) + p2_probe_recursive(value - 2u);
}

P2_NOINLINE p2_u32 p2_probe_function_pointer(p2_u32 (*fn)(p2_u32),
                                              p2_u32 value)
{
  return fn(value);
}

P2_NOINLINE p2_u32 p2_probe_switch(p2_u32 value)
{
  switch (value)
    {
      case 0u:
        return 0x10u;
      case 1u:
        return 0x11u;
      case 2u:
        return 0x12u;
      case 3u:
        return 0x13u;
      case 17u:
        return 0x71u;
      case 255u:
        return 0xffu;
      default:
        return value + 0x100u;
    }
}
