; SPDX-License-Identifier: Apache-2.0

; Negative IR probe: the P2 pass must reject a dynamic atomicrmw rather than
; lower it to a non-atomic external-memory helper.

target triple = "p2"

define i32 @p2_probe_ir_dynamic_atomicrmw(i32* %pointer, i32 %value) {
entry:
  %old = atomicrmw add i32* %pointer, i32 %value seq_cst
  ret i32 %old
}
