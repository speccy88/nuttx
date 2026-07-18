; SPDX-License-Identifier: Apache-2.0

; A tagged pointer must never reach a native P2 atomic store.  The unified
; memory pass has no cross-memory atomic ABI and therefore must reject it.

define void @p2_probe_atomic_store(i32* %pointer, i32 %value) {
entry:
  store atomic i32 %value, i32* %pointer seq_cst, align 4
  ret void
}
