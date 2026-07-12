/* SPDX-License-Identifier: Apache-2.0 */

        .text
        .globl  p2_probe_block_store_r0_r31
p2_probe_block_store_r0_r31:
        setq    #31
        wrlong  r0, r0
        reta

        .globl  p2_probe_block_load_r0_r31
p2_probe_block_load_r0_r31:
        setq    #31
        rdlong  r0, r0
        reta
