/****************************************************************************
 * arch/p2/include/setjmp.h
 *
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

#ifndef __ARCH_P2_INCLUDE_SETJMP_H
#define __ARCH_P2_INCLUDE_SETJMP_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>
#include <nuttx/compiler.h>

#include <stdint.h>

/****************************************************************************
 * Public Types
 ****************************************************************************/

/* P2LLVM uses an upward-growing Hub stack.  R0-R29 are described as
 * callee-saved by the target calling convention; R30/R31 are reserved for
 * scalar returns.  CALLA pushes a packed C/Z/return-PC long immediately
 * below PTRA.  Preserve that complete C-call state so longjmp() can rebuild
 * the return slot even after later calls have reused it.
 */

struct setjmp_buf_s
{
  uintptr_t regs[30];
  uintptr_t resume;
  uintptr_t sp;
};

typedef struct setjmp_buf_s jmp_buf[1];

/****************************************************************************
 * Public Function Prototypes
 ****************************************************************************/

int setjmp(jmp_buf env) __attribute__((returns_twice));
void longjmp(jmp_buf env, int val) noreturn_function;

#endif /* __ARCH_P2_INCLUDE_SETJMP_H */
