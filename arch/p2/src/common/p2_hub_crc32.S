/****************************************************************************
 * arch/p2/src/common/p2_hub_crc32.S
 *
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

        .text

        /* uint32_t p2_hub_crc32_update(uint32_t crc,
         *                              const uint8_t *data, size_t size)
         *
         * R0-R2 are callee-saved by the P2 ABI and R31 is the return value.
         * PA/PB and R30-R31 are caller-scratch.  Preserve R3 on the caller's
         * stack so this leaf routine has no mutable global state and remains
         * safe across nested calls.  CRCNIB consumes Q[31:28] and shifts Q
         * left four bits.  Each SETQ #0 both supplies four zero bits and
         * shields its companion instruction from interrupt separation.
         * XORing the byte into D before two such pairs therefore implements
         * the standard eight reflected zero-bit CRC-32 updates exactly.
         */

        .globl p2_hub_crc32_update
        .type p2_hub_crc32_update,@function
p2_hub_crc32_update:
        wrlong  r3, ptra++
        mov     r31, r0
        mov     pa, r1
        mov     pb, r2
        mov     r30, ##0xedb88320
        tjz     pb, #.Lcrc_done

.Lcrc_byte:
        rdbyte  r3, pa
        add     pa, #1
        xor     r31, r3
        setq    #0
        crcnib  r31, r30
        setq    #0
        crcnib  r31, r30
        djnz    pb, #.Lcrc_byte

.Lcrc_done:
        rdlong  r3, --ptra
        reta
        .size p2_hub_crc32_update, .-p2_hub_crc32_update

        .section .note.GNU-stack, "", @progbits
