; SPDX-License-Identifier: Apache-2.0

; Negative IR probe: the P2 pass must reject a dynamic cmpxchg rather than
; lower it to non-atomic loads and stores.

target triple = "p2"

define i1 @p2_probe_ir_dynamic_cmpxchg(i32* %pointer, i32 %expected,
                                        i32 %desired) {
entry:
  %result = cmpxchg i32* %pointer, i32 %expected, i32 %desired seq_cst seq_cst
  %success = extractvalue { i32, i1 } %result, 1
  ret i1 %success
}
