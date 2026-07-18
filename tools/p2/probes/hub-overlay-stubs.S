/****************************************************************************
 * tools/p2/probes/hub-overlay-stubs.S
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Compile-only probe for the resident P2 Hub overlay ABI.  These two public
 * symbols must each remain exactly one four-byte unconditional CALLA.  The
 * records deliberately use packed source offsets; they are not a runnable
 * image and must never be registered with the dispatcher.
 ****************************************************************************/

        .section .p2.overlay.stubs,"ax",@progbits
        .p2align 2
        .globl p2_overlay_probe_first
        .type p2_overlay_probe_first,@function
p2_overlay_probe_first:
        calla   #\__p2_overlay_enter
        .size p2_overlay_probe_first, .-p2_overlay_probe_first

        .globl p2_overlay_probe_second
        .type p2_overlay_probe_second,@function
p2_overlay_probe_second:
        calla   #\__p2_overlay_enter
        .size p2_overlay_probe_second, .-p2_overlay_probe_second

        .section .p2.xdata.ro.overlay.entries,"a",@progbits
        .p2align 3
        .long   1, 0
        .long   2, 4

        .section .p2.overlay.groups,"aw",@progbits
        .p2align 2
        .long   0, 0, 0, 0
        .long   0, 16, 0, 0x0000000f
        .long   16, 16, 0, 0x0000000f

        .section .note.GNU-stack,"",@progbits
