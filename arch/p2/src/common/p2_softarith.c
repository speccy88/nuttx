/****************************************************************************
 * arch/p2/src/common/p2_softarith.c
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.  The
 * ASF licenses this file to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance with the
 * License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
 * WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the
 * License for the specific language governing permissions and limitations
 * under the License.
 *
 ****************************************************************************/

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <stddef.h>
#include <stdint.h>

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define P2_U32_SIGN 0x80000000u

#if !defined(__BYTE_ORDER__) || !defined(__ORDER_LITTLE_ENDIAN__) || \
    __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#  error "p2_softarith requires little-endian 32-bit limb layout"
#endif

/* P2LLVM currently combines pairs of 32-bit limbs back into i64 operations
 * during optimization.  That transformation makes the shift helpers call
 * themselves.  Keep this translation unit literal until that backend pass
 * is made safe for compiler-runtime implementations.
 */

#ifdef __clang__
#  pragma clang optimize off
#endif

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct p2_u32_div_s
{
  uint32_t quotient;
  uint32_t remainder;
};

struct p2_u64_s
{
  uint32_t low;
  uint32_t high;
};

struct p2_u64_div_s
{
  struct p2_u64_s quotient;
  struct p2_u64_s remainder;
};

union p2_i32_bits_u
{
  int32_t signed_value;
  uint32_t unsigned_value;
};

union p2_i64_bits_u
{
  int64_t signed_value;
  struct p2_u64_s limb;
};

union p2_u64_bits_u
{
  uint64_t unsigned_value;
  struct p2_u64_s limb;
};

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static uint32_t p2_i32_bits(int32_t value)
{
  union p2_i32_bits_u bits;

  bits.signed_value = value;
  return bits.unsigned_value;
}

static int32_t p2_pack_i32(uint32_t value)
{
  union p2_i32_bits_u bits;

  bits.unsigned_value = value;
  return bits.signed_value;
}

static struct p2_u64_s p2_i64_bits(int64_t value)
{
  union p2_i64_bits_u bits;

  bits.signed_value = value;
  return bits.limb;
}

static struct p2_u64_s p2_u64_bits(uint64_t value)
{
  union p2_u64_bits_u bits;

  bits.unsigned_value = value;
  return bits.limb;
}

static int64_t p2_pack_i64(struct p2_u64_s value)
{
  union p2_i64_bits_u bits;

  bits.limb = value;
  return bits.signed_value;
}

static uint64_t p2_pack_u64(struct p2_u64_s value)
{
  union p2_u64_bits_u bits;

  bits.limb = value;
  return bits.unsigned_value;
}

static uint32_t p2_negate32(uint32_t value)
{
  return ~value + 1u;
}

static uint32_t p2_abs32(uint32_t value)
{
  if ((value & P2_U32_SIGN) != 0)
    {
      return p2_negate32(value);
    }

  return value;
}

static uint32_t p2_mul32(uint32_t left, uint32_t right)
{
  uint32_t result = 0;

  while (right != 0)
    {
      if ((right & 1u) != 0)
        {
          result += left;
        }

      left <<= 1;
      right >>= 1;
    }

  return result;
}

static struct p2_u32_div_s p2_udivmod32(uint32_t numerator,
                                        uint32_t denominator)
{
  struct p2_u32_div_s result;
  uint32_t divisor;
  uint32_t quotient_bit;

  result.quotient = 0;
  result.remainder = numerator;

  if (denominator == 0)
    {
      return result;
    }

  divisor = denominator;
  quotient_bit = 1;

  while (divisor <= result.remainder &&
         (divisor & P2_U32_SIGN) == 0)
    {
      divisor <<= 1;
      quotient_bit <<= 1;
    }

  while (quotient_bit != 0)
    {
      if (result.remainder >= divisor)
        {
          result.remainder -= divisor;
          result.quotient |= quotient_bit;
        }

      divisor >>= 1;
      quotient_bit >>= 1;
    }

  return result;
}

static struct p2_u32_div_s p2_sdivmod32(int32_t numerator,
                                        int32_t denominator)
{
  struct p2_u32_div_s result;
  uint32_t numerator_bits = p2_i32_bits(numerator);
  uint32_t denominator_bits = p2_i32_bits(denominator);

  result = p2_udivmod32(p2_abs32(numerator_bits),
                        p2_abs32(denominator_bits));

  if (((numerator_bits ^ denominator_bits) & P2_U32_SIGN) != 0)
    {
      result.quotient = p2_negate32(result.quotient);
    }

  if ((numerator_bits & P2_U32_SIGN) != 0)
    {
      result.remainder = p2_negate32(result.remainder);
    }

  return result;
}

static int p2_u64_is_zero(struct p2_u64_s value)
{
  return value.low == 0 && value.high == 0;
}

static int p2_u64_compare(struct p2_u64_s left, struct p2_u64_s right)
{
  if (left.high < right.high)
    {
      return -1;
    }

  if (left.high > right.high)
    {
      return 1;
    }

  if (left.low < right.low)
    {
      return -1;
    }

  if (left.low > right.low)
    {
      return 1;
    }

  return 0;
}

static struct p2_u64_s p2_u64_add(struct p2_u64_s left,
                                  struct p2_u64_s right)
{
  struct p2_u64_s result;
  uint32_t carry;

  result.low = left.low + right.low;
  carry = result.low < left.low ? 1u : 0u;
  result.high = left.high + right.high + carry;
  return result;
}

static struct p2_u64_s p2_u64_subtract(struct p2_u64_s left,
                                       struct p2_u64_s right)
{
  struct p2_u64_s result;
  uint32_t borrow = left.low < right.low ? 1u : 0u;

  result.low = left.low - right.low;
  result.high = left.high - right.high - borrow;
  return result;
}

static struct p2_u64_s p2_u64_negate(struct p2_u64_s value)
{
  struct p2_u64_s result;

  result.low = ~value.low + 1u;
  result.high = ~value.high;
  if (result.low == 0)
    {
      result.high += 1u;
    }

  return result;
}

static struct p2_u64_s p2_u64_abs(struct p2_u64_s value)
{
  if ((value.high & P2_U32_SIGN) != 0)
    {
      return p2_u64_negate(value);
    }

  return value;
}

static struct p2_u64_s p2_u64_shift_left_one(struct p2_u64_s value)
{
  struct p2_u64_s result;

  result.high = (value.high << 1) | (value.low >> 31);
  result.low = value.low << 1;
  return result;
}

static struct p2_u64_s p2_u64_shift_right_one(struct p2_u64_s value)
{
  struct p2_u64_s result;

  result.low = (value.low >> 1) | (value.high << 31);
  result.high = value.high >> 1;
  return result;
}

static struct p2_u64_s p2_mul64(struct p2_u64_s left,
                                struct p2_u64_s right)
{
  struct p2_u64_s result;

  result.low = 0;
  result.high = 0;

  while (!p2_u64_is_zero(right))
    {
      if ((right.low & 1u) != 0)
        {
          result = p2_u64_add(result, left);
        }

      left = p2_u64_shift_left_one(left);
      right = p2_u64_shift_right_one(right);
    }

  return result;
}

static struct p2_u64_div_s p2_udivmod64(struct p2_u64_s numerator,
                                        struct p2_u64_s denominator)
{
  struct p2_u64_div_s result;
  struct p2_u64_s divisor;
  struct p2_u64_s quotient_bit;

  result.quotient.low = 0;
  result.quotient.high = 0;
  result.remainder = numerator;
  quotient_bit.low = 1;
  quotient_bit.high = 0;

  if (p2_u64_is_zero(denominator))
    {
      return result;
    }

  divisor = denominator;
  while (p2_u64_compare(divisor, result.remainder) <= 0 &&
         (divisor.high & P2_U32_SIGN) == 0)
    {
      divisor = p2_u64_shift_left_one(divisor);
      quotient_bit = p2_u64_shift_left_one(quotient_bit);
    }

  while (!p2_u64_is_zero(quotient_bit))
    {
      if (p2_u64_compare(result.remainder, divisor) >= 0)
        {
          result.remainder = p2_u64_subtract(result.remainder, divisor);
          result.quotient.low |= quotient_bit.low;
          result.quotient.high |= quotient_bit.high;
        }

      divisor = p2_u64_shift_right_one(divisor);
      quotient_bit = p2_u64_shift_right_one(quotient_bit);
    }

  return result;
}

static struct p2_u64_div_s p2_sdivmod64(int64_t numerator,
                                        int64_t denominator)
{
  struct p2_u64_div_s result;
  struct p2_u64_s numerator_bits = p2_i64_bits(numerator);
  struct p2_u64_s denominator_bits = p2_i64_bits(denominator);

  result = p2_udivmod64(p2_u64_abs(numerator_bits),
                        p2_u64_abs(denominator_bits));

  if (((numerator_bits.high ^ denominator_bits.high) & P2_U32_SIGN) != 0)
    {
      result.quotient = p2_u64_negate(result.quotient);
    }

  if ((numerator_bits.high & P2_U32_SIGN) != 0)
    {
      result.remainder = p2_u64_negate(result.remainder);
    }

  return result;
}

static struct p2_u64_s p2_shift_left64(struct p2_u64_s value,
                                       unsigned int amount)
{
  struct p2_u64_s result;

  result.low = 0;
  result.high = 0;

  if (amount == 0)
    {
      return value;
    }

  if (amount < 32)
    {
      result.low = value.low << amount;
      result.high = (value.high << amount) |
                    (value.low >> (32 - amount));
    }
  else if (amount < 64)
    {
      result.high = value.low << (amount - 32);
    }

  return result;
}

static struct p2_u64_s p2_shift_right64(struct p2_u64_s value,
                                        unsigned int amount)
{
  struct p2_u64_s result;

  result.low = 0;
  result.high = 0;

  if (amount == 0)
    {
      return value;
    }

  if (amount < 32)
    {
      result.low = (value.low >> amount) |
                   (value.high << (32 - amount));
      result.high = value.high >> amount;
    }
  else if (amount < 64)
    {
      result.low = value.high >> (amount - 32);
    }

  return result;
}

static struct p2_u64_s p2_shift_aright64(struct p2_u64_s value,
                                         unsigned int amount)
{
  struct p2_u64_s result;
  uint32_t sign_fill;
  unsigned int high_amount;

  sign_fill = (value.high & P2_U32_SIGN) != 0 ? UINT32_MAX : 0;
  result.low = sign_fill;
  result.high = sign_fill;

  if (amount == 0)
    {
      return value;
    }

  if (amount < 32)
    {
      result.low = (value.low >> amount) |
                   (value.high << (32 - amount));
      result.high = value.high >> amount;
      if (sign_fill != 0)
        {
          result.high |= UINT32_MAX << (32 - amount);
        }
    }
  else if (amount < 64)
    {
      high_amount = amount - 32;
      result.low = value.high;
      if (high_amount != 0)
        {
          result.low >>= high_amount;
          if (sign_fill != 0)
            {
              result.low |= UINT32_MAX << (32 - high_amount);
            }
        }
    }

  return result;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

/* These helpers intentionally define cases that ISO C leaves undefined.
 * Division by zero returns a zero quotient and the original numerator as the
 * remainder.  Signed minimum divided by -1 wraps to signed minimum.  Shift
 * counts outside 0..63 produce zero, or sign fill for arithmetic right
 * shift.
 */

int32_t __mulsi3(int32_t left, int32_t right)
{
  return p2_pack_i32(p2_mul32(p2_i32_bits(left), p2_i32_bits(right)));
}

uint32_t __udivmodsi4(uint32_t numerator, uint32_t denominator,
                      uint32_t *remainder)
{
  struct p2_u32_div_s result = p2_udivmod32(numerator, denominator);

  if (remainder != NULL)
    {
      *remainder = result.remainder;
    }

  return result.quotient;
}

uint32_t __udivsi3(uint32_t numerator, uint32_t denominator)
{
  return p2_udivmod32(numerator, denominator).quotient;
}

uint32_t __umodsi3(uint32_t numerator, uint32_t denominator)
{
  return p2_udivmod32(numerator, denominator).remainder;
}

int32_t __divmodsi4(int32_t numerator, int32_t denominator,
                    int32_t *remainder)
{
  struct p2_u32_div_s result = p2_sdivmod32(numerator, denominator);

  if (remainder != NULL)
    {
      *remainder = p2_pack_i32(result.remainder);
    }

  return p2_pack_i32(result.quotient);
}

int32_t __divsi3(int32_t numerator, int32_t denominator)
{
  return p2_pack_i32(p2_sdivmod32(numerator, denominator).quotient);
}

int32_t __modsi3(int32_t numerator, int32_t denominator)
{
  return p2_pack_i32(p2_sdivmod32(numerator, denominator).remainder);
}

int64_t __muldi3(int64_t left, int64_t right)
{
  return p2_pack_i64(p2_mul64(p2_i64_bits(left), p2_i64_bits(right)));
}

uint64_t __udivmoddi4(uint64_t numerator, uint64_t denominator,
                      uint64_t *remainder)
{
  struct p2_u64_div_s result;

  result = p2_udivmod64(p2_u64_bits(numerator),
                        p2_u64_bits(denominator));
  if (remainder != NULL)
    {
      *remainder = p2_pack_u64(result.remainder);
    }

  return p2_pack_u64(result.quotient);
}

uint64_t __udivdi3(uint64_t numerator, uint64_t denominator)
{
  struct p2_u64_div_s result;

  result = p2_udivmod64(p2_u64_bits(numerator),
                        p2_u64_bits(denominator));
  return p2_pack_u64(result.quotient);
}

uint64_t __umoddi3(uint64_t numerator, uint64_t denominator)
{
  struct p2_u64_div_s result;

  result = p2_udivmod64(p2_u64_bits(numerator),
                        p2_u64_bits(denominator));
  return p2_pack_u64(result.remainder);
}

int64_t __divmoddi4(int64_t numerator, int64_t denominator,
                    int64_t *remainder)
{
  struct p2_u64_div_s result = p2_sdivmod64(numerator, denominator);

  if (remainder != NULL)
    {
      *remainder = p2_pack_i64(result.remainder);
    }

  return p2_pack_i64(result.quotient);
}

int64_t __divdi3(int64_t numerator, int64_t denominator)
{
  return p2_pack_i64(p2_sdivmod64(numerator, denominator).quotient);
}

int64_t __moddi3(int64_t numerator, int64_t denominator)
{
  return p2_pack_i64(p2_sdivmod64(numerator, denominator).remainder);
}

int64_t __ashldi3(int64_t value, int amount)
{
  return p2_pack_i64(p2_shift_left64(p2_i64_bits(value),
                                     (unsigned int)amount));
}

int64_t __ashrdi3(int64_t value, int amount)
{
  return p2_pack_i64(p2_shift_aright64(p2_i64_bits(value),
                                       (unsigned int)amount));
}

int64_t __lshrdi3(int64_t value, int amount)
{
  return p2_pack_i64(p2_shift_right64(p2_i64_bits(value),
                                      (unsigned int)amount));
}
