/****************************************************************************
 * arch/p2/src/p2x8c4m64p/p2_head.S
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

/* The P2 ROM starts cog 0 with the first 2 KiB of the Hub image loaded into
 * cog memory.  Address zero therefore begins in COGEXEC mode.  Jump to cog
 * address 0x10, which corresponds to Hub byte address 0x40 in the image.
 */

  .section .p2.entry, "ax", @progbits
  .p2align 2
  .global __entry
  .type __entry, @function
__entry:
  jmp     #\0x10
  .size __entry, . - __entry

/* The loadp2 RAM loader patches these words before transfer.  They are image
 * metadata, not executable instructions; __entry branches over them.
 */

  .section .p2.params, "a", @progbits
  .p2align 2
  .global __p2_loader_clkfreq
__p2_loader_clkfreq:
  .long   0
  .global __p2_loader_clkmode
__p2_loader_clkmode:
  .long   0
  .global __p2_loader_baud
__p2_loader_baud:
  .long   0

/* Restart cog 0 at the reusable Hub entry.  The AUGS/MOV relocation pair is
 * the p2llvm form for materializing a 20-bit Hub address.  COGINIT mode 0x20
 * explicitly selects HUBEXEC on physical cog 0.
 */

  .section .p2.cog, "ax", @progbits
  .p2align 2
  .global __p2_cog_start
  .type __p2_cog_start, @function
__p2_cog_start:
  augs    #0
  mov     r0, #__start
  coginit #0x20, r0
.Lp2_cog_halt:
  jmp     #.Lp2_cog_halt
  .size __p2_cog_start, . - __p2_cog_start

/* The restarted cog arrives here in HUBEXEC mode.  PTRA is an upward-growing
 * stack pointer and must point at the first free long before CALLA pushes its
 * packed C/Z/return-PC word.
 */

  .section .text.__start, "ax", @progbits
  .p2align 2
  .global __start
  .type __start, @function
__start:
  augs    #0
  mov     ptra, #__initial_ptra
  calla   #\p2_start
.Lp2_start_halt:
  jmp     #.Lp2_start_halt
  .size __start, . - __start

  .section .note.GNU-stack, "", @progbits
