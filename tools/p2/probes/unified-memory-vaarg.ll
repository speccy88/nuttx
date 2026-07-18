; An arbitrary va_list cursor may be a tagged PSRAM pointer.  The unified
; lowering stages its cursor through a Hub alloca, uses the native descending
; P2 va_arg operation there, and writes the updated cursor back through xmem.

target triple = "p2"

define i32 @p2_probe_dynamic_vaarg(i8** %args) {
  %value = va_arg i8** %args, i32
  ret i32 %value
}
