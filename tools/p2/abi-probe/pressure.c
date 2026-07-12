/* SPDX-License-Identifier: Apache-2.0 */

#include "abi_probe.h"

extern p2_u32 p2_probe_pressure_barrier(
  p2_u32, p2_u32, p2_u32, p2_u32, p2_u32, p2_u32, p2_u32, p2_u32,
  p2_u32, p2_u32, p2_u32, p2_u32, p2_u32, p2_u32, p2_u32, p2_u32,
  p2_u32, p2_u32, p2_u32, p2_u32, p2_u32, p2_u32, p2_u32, p2_u32,
  p2_u32, p2_u32, p2_u32, p2_u32, p2_u32, p2_u32, p2_u32, p2_u32);

P2_NOINLINE p2_u32 p2_probe_pressure_r0_r31(const volatile p2_u32 *source)
{
  p2_u32 v0 = source[0] + 0u;
  p2_u32 v1 = source[1] + 1u;
  p2_u32 v2 = source[2] + 2u;
  p2_u32 v3 = source[3] + 3u;
  p2_u32 v4 = source[4] + 4u;
  p2_u32 v5 = source[5] + 5u;
  p2_u32 v6 = source[6] + 6u;
  p2_u32 v7 = source[7] + 7u;
  p2_u32 v8 = source[8] + 8u;
  p2_u32 v9 = source[9] + 9u;
  p2_u32 v10 = source[10] + 10u;
  p2_u32 v11 = source[11] + 11u;
  p2_u32 v12 = source[12] + 12u;
  p2_u32 v13 = source[13] + 13u;
  p2_u32 v14 = source[14] + 14u;
  p2_u32 v15 = source[15] + 15u;
  p2_u32 v16 = source[16] + 16u;
  p2_u32 v17 = source[17] + 17u;
  p2_u32 v18 = source[18] + 18u;
  p2_u32 v19 = source[19] + 19u;
  p2_u32 v20 = source[20] + 20u;
  p2_u32 v21 = source[21] + 21u;
  p2_u32 v22 = source[22] + 22u;
  p2_u32 v23 = source[23] + 23u;
  p2_u32 v24 = source[24] + 24u;
  p2_u32 v25 = source[25] + 25u;
  p2_u32 v26 = source[26] + 26u;
  p2_u32 v27 = source[27] + 27u;
  p2_u32 v28 = source[28] + 28u;
  p2_u32 v29 = source[29] + 29u;
  p2_u32 v30 = source[30] + 30u;
  p2_u32 v31 = source[31] + 31u;

  return p2_probe_pressure_barrier(
    v0, v1, v2, v3, v4, v5, v6, v7,
    v8, v9, v10, v11, v12, v13, v14, v15,
    v16, v17, v18, v19, v20, v21, v22, v23,
    v24, v25, v26, v27, v28, v29, v30, v31);
}
