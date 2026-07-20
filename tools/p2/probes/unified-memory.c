/* SPDX-License-Identifier: Apache-2.0 */

/*
 * Compiler-only probe for the P2 tagged external-memory lowering pass.
 *
 * This file is never linked or run on a target.  The checker compiles it to
 * P2 assembly and verifies that pointer provenance selects the expected
 * access path.  Keep it independent of target headers so it remains usable
 * while bringing up a patched compiler.
 */

typedef unsigned char p2_probe_u8_t;
typedef unsigned short p2_probe_u16_t;
typedef unsigned int p2_probe_u32_t;
typedef unsigned long long p2_probe_u64_t;
typedef __SIZE_TYPE__ p2_probe_size_t;

void *memcpy(void *destination, const void *source, p2_probe_size_t length);
void *memmove(void *destination, const void *source, p2_probe_size_t length);
void *memset(void *destination, int value, p2_probe_size_t length);

#define P2_PROBE_NOINLINE __attribute__((noinline, used))

volatile p2_probe_u32_t g_p2_probe_hub_word;

P2_PROBE_NOINLINE p2_probe_u8_t
p2_probe_dynamic_load8(const volatile void *pointer)
{
  return *(const volatile p2_probe_u8_t *)pointer;
}

P2_PROBE_NOINLINE p2_probe_u16_t
p2_probe_dynamic_load16(const volatile void *pointer)
{
  return *(const volatile p2_probe_u16_t *)pointer;
}

P2_PROBE_NOINLINE p2_probe_u32_t
p2_probe_dynamic_load32(const volatile void *pointer)
{
  return *(const volatile p2_probe_u32_t *)pointer;
}

P2_PROBE_NOINLINE p2_probe_u64_t
p2_probe_dynamic_load64(const volatile void *pointer)
{
  return *(const volatile p2_probe_u64_t *)pointer;
}

P2_PROBE_NOINLINE void
p2_probe_dynamic_store8(volatile void *pointer, p2_probe_u8_t value)
{
  *(volatile p2_probe_u8_t *)pointer = value;
}

P2_PROBE_NOINLINE void
p2_probe_dynamic_store16(volatile void *pointer, p2_probe_u16_t value)
{
  *(volatile p2_probe_u16_t *)pointer = value;
}

P2_PROBE_NOINLINE void
p2_probe_dynamic_store32(volatile void *pointer, p2_probe_u32_t value)
{
  *(volatile p2_probe_u32_t *)pointer = value;
}

P2_PROBE_NOINLINE void
p2_probe_dynamic_store64(volatile void *pointer, p2_probe_u64_t value)
{
  *(volatile p2_probe_u64_t *)pointer = value;
}

P2_PROBE_NOINLINE void *
p2_probe_dynamic_memcpy(void *destination, const void *source,
                        p2_probe_size_t length)
{
  return __builtin_memcpy(destination, source, length);
}

P2_PROBE_NOINLINE void *
p2_probe_dynamic_memmove(void *destination, const void *source,
                         p2_probe_size_t length)
{
  return __builtin_memmove(destination, source, length);
}

P2_PROBE_NOINLINE void *
p2_probe_dynamic_memset(void *destination, int value,
                        p2_probe_size_t length)
{
  return __builtin_memset(destination, value, length);
}

/*
 * NuttX globally uses -fno-builtin.  These deliberately ordinary libc calls
 * prove that the pass recognizes the retained calls as well as LLVM memory
 * intrinsics emitted for the explicit builtins above.
 */

P2_PROBE_NOINLINE void *
p2_probe_dynamic_libc_memcpy(void *destination, const void *source,
                             p2_probe_size_t length)
{
  return memcpy(destination, source, length);
}

P2_PROBE_NOINLINE void *
p2_probe_dynamic_libc_memmove(void *destination, const void *source,
                              p2_probe_size_t length)
{
  return memmove(destination, source, length);
}

P2_PROBE_NOINLINE void *
p2_probe_dynamic_libc_memset(void *destination, int value,
                             p2_probe_size_t length)
{
  return memset(destination, value, length);
}

P2_PROBE_NOINLINE p2_probe_u32_t p2_probe_hub_global_load(void)
{
  return g_p2_probe_hub_word;
}

P2_PROBE_NOINLINE void p2_probe_hub_global_store(p2_probe_u32_t value)
{
  g_p2_probe_hub_word = value;
}

P2_PROBE_NOINLINE p2_probe_u32_t
p2_probe_hub_stack_roundtrip(p2_probe_u32_t value)
{
  volatile p2_probe_u32_t slot = value;
  return slot;
}

/* Runtime functions carrying the __p2_xmem_ prefix are trusted recursion
 * boundaries.  Their pointer contracts are checked by the runtime itself,
 * so the compiler must leave these accesses native at every optimization
 * level even though this probe's pointer has unknown provenance.
 */

P2_PROBE_NOINLINE p2_probe_u32_t
__p2_xmem_probe_hub_roundtrip(volatile p2_probe_u32_t *pointer,
                              p2_probe_u32_t value)
{
  *pointer = value;
  return *pointer;
}
