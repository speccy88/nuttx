/****************************************************************************
 * arch/p2/src/common/p2_overlay_veneer.S
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

#define __ASSEMBLY__ 1
#include <arch/context.h>

        .section .text.p2_overlay_veneer,"ax",@progbits

        /* Every generated public stub is exactly:
         *
         *   calla #\__p2_overlay_enter
         *
         * The stub CALLA resume at [PTRA-4] identifies its index, while the
         * original caller resume is at [PTRA-8].  Save R0-R29 before passing
         * those two words to C.  P2LLVM defines R0-R29 as callee-saved, but
         * replacing R0/R1 with dispatcher arguments would otherwise destroy
         * the overlay function's original arguments.
         */

        .globl __p2_overlay_enter
        .type __p2_overlay_enter,@function
__p2_overlay_enter:
        setq    #29
        wrlong  r0, ptra
        add     ptra, #120

        mov     pa, ptra
        sub     pa, #124
        rdlong  r0, pa
        sub     pa, #4
        rdlong  r1, pa
        calla   #\p2_overlay_dispatch_enter
        mov     pb, r31

        sub     ptra, #120
        setq    #29
        rdlong  r0, ptra

        /* Discard the stub-to-veneer CALLA word.  Replace only the low 20 PC
         * bits of the original packed resume, retaining its exact C/Z bits.
         * The C shadow stack holds the complete original resume.
         */

        sub     ptra, #4
        mov     pa, ptra
        sub     pa, #4
        rdlong  r30, pa
        and     r30, ##(~P2_RESUME_PC_MASK)
        augs    #0
        mov     r31, ##__p2_overlay_exit
        or      r30, r31
        wrlong  r30, pa
        jmp     pb
        .size __p2_overlay_enter, .-__p2_overlay_enter

        /* An overlay body returns with RETA through the rewritten word above,
         * so PTRA once again names the original caller's free return slot.
         * R30/R31 hold scalar return values and are caller-clobbered by the C
         * loader path.  Save both before reloading a caller group, recreate
         * the exact original CALLA word, then consume it with one final RETA.
         */

        .globl __p2_overlay_exit
        .type __p2_overlay_exit,@function
__p2_overlay_exit:
        wrlong  r30, ptra++
        wrlong  r31, ptra++
        calla   #\p2_overlay_dispatch_exit
        mov     pb, r31
        rdlong  r31, --ptra
        rdlong  r30, --ptra
        wrlong  pb, ptra++
        reta
        .size __p2_overlay_exit, .-__p2_overlay_exit

        .section .note.GNU-stack, "", @progbits
