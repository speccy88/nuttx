; An arbitrary va_list cursor may be a tagged PSRAM pointer.  The native P2
; va_arg expansion cannot access it through the unified-memory helper ABI and
; must therefore fail explicitly instead of silently issuing Hub loads.

target triple = "p2"

define i32 @p2_probe_dynamic_vaarg(i8** %args) {
  %value = va_arg i8** %args, i32
  ret i32 %value
}
