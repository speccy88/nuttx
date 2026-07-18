; SPDX-License-Identifier: Apache-2.0

; A tagged pointer must never reach a native P2 atomic load.  The unified
; memory pass has no cross-memory atomic ABI and therefore must reject it.

define i32 @p2_probe_atomic_load(i32* %pointer) {
entry:
  %value = load atomic i32, i32* %pointer seq_cst, align 4
  ret i32 %value
}
