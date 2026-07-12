/* SPDX-License-Identifier: Apache-2.0 */

#ifndef TOOLS_P2_ABI_PROBE_ABI_PROBE_H
#define TOOLS_P2_ABI_PROBE_ABI_PROBE_H

typedef unsigned char p2_u8;
typedef unsigned int p2_u32;
typedef signed int p2_s32;
typedef unsigned long long p2_u64;
typedef signed long long p2_s64;
typedef __SIZE_TYPE__ p2_size_t;

#define P2_NOINLINE __attribute__((noinline))
#define P2_USED __attribute__((used))

#endif
