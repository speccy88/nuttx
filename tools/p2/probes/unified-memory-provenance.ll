; Compile-only postcondition probes for conservative P2 Hub provenance.
;
; Each address below is numerically in the tagged external-memory window but
; is deliberately derived from a real Hub global.  A compiler that follows
; only the underlying object will misclassify these accesses as native Hub
; loads.  Unified-memory lowering must route all three through load8.

target triple = "p2"

@p2_probe_hub_bytes = global [16 x i8] zeroinitializer, align 4
@p2_probe_out_of_range_alias = alias i8, i8* getelementptr (
  [16 x i8], [16 x i8]* @p2_probe_hub_bytes, i32 0, i32 268435456)

define i8 @p2_probe_integer_derived_tag() {
  %hub = ptrtoint [16 x i8]* @p2_probe_hub_bytes to i32
  %tag = add i32 %hub, 268435456
  %pointer = inttoptr i32 %tag to i8*
  %value = load volatile i8, i8* %pointer, align 1
  ret i8 %value
}

define i8 @p2_probe_non_inbounds_gep_escape() {
  %hub = bitcast [16 x i8]* @p2_probe_hub_bytes to i8*
  %pointer = getelementptr i8, i8* %hub, i32 268435456
  %value = load volatile i8, i8* %pointer, align 1
  ret i8 %value
}

define i8 @p2_probe_out_of_range_global_alias() {
  %value = load volatile i8, i8* @p2_probe_out_of_range_alias, align 1
  ret i8 %value
}
