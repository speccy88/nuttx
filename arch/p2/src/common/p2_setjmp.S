/****************************************************************************
 * arch/p2/src/common/p2_setjmp.S
 *
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

        .text

        /* int setjmp(jmp_buf env)
         *
         * R0 contains env.  A SETQ block stores R0-R29 without disturbing
         * them.  PA/PB are reserved compiler scratch registers and therefore
         * may be used by this leaf routine.  On entry, PTRA points one long
         * beyond CALLA's packed C/Z/return-PC word.
         */

        .globl setjmp
        .type setjmp,@function
setjmp:
        mov     pa, r0
        setq    #29
        wrlong  r0, pa
        add     pa, #120
        mov     pb, ptra
        sub     pb, #4
        rdlong  pb, pb
        wrlong  pb, pa
        add     pa, #4
        wrlong  ptra, pa
        mov     r31, #0
        reta
        .size setjmp, .-setjmp

        /* void longjmp(jmp_buf env, int val)
         *
         * Recreate CALLA's overwritten return slot, restore R0-R29, and use
         * RETA so both the saved flags and return PC are honored.  POSIX and
         * ISO C require a zero val to make setjmp() return one.
         */

        .globl longjmp
        .type longjmp,@function
longjmp:
        mov     pa, r0
        mov     pb, r1
        cmp     pb, #0 wz
  if_z  mov     pb, #1
        add     pa, #120
        rdlong  ptrb, pa
        add     pa, #4
        rdlong  ptra, pa
        sub     pa, #124
        setq    #29
        rdlong  r0, pa
        sub     ptra, #4
        wrlong  ptrb, ptra
        add     ptra, #4
        mov     r31, pb
        reta
        .size longjmp, .-longjmp

        .section .note.GNU-stack, "", @progbits
