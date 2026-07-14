; Reduced from LVGL 9.2.2 lv_style_prop_get_default().  Conditional TJZ/TJNZ
; instructions must preserve the not-taken machine-block successor.

target triple = "p2"

%style_value = type { i32 }

@p2_probe_font = external global i8

define void @p2_tj_fallthrough_probe(
    %style_value* noalias nocapture sret(%style_value) align 4 %out,
    i8 zeroext %prop) minsize optsize {
entry:
  %slot = getelementptr inbounds %style_value, %style_value* %out, i32 0, i32 0
  switch i8 %prop, label %default [
    i8 108, label %scale
    i8 109, label %scale
    i8 28, label %white
    i8 35, label %black
    i8 49, label %black
    i8 61, label %black
    i8 57, label %black
    i8 82, label %black
    i8 76, label %black
    i8 88, label %black
    i8 69, label %black
    i8 95, label %opaque
    i8 96, label %opaque
    i8 50, label %opaque
    i8 89, label %opaque
    i8 68, label %opaque
    i8 37, label %opaque
    i8 36, label %opaque
    i8 41, label %opaque
    i8 58, label %opaque
    i8 62, label %opaque
    i8 77, label %opaque
    i8 83, label %opaque
    i8 34, label %gradient_stop
    i8 52, label %border_side
    i8 90, label %font
    i8 5, label %coord_max
    i8 7, label %coord_max
    i8 116, label %rotary
  ]

scale:
  store i32 256, i32* %slot, align 4
  br label %done

white:
  store i32 16777215, i32* %slot, align 4
  br label %done

black:
  store i32 0, i32* %slot, align 4
  br label %done

opaque:
  store i32 255, i32* %slot, align 4
  br label %done

gradient_stop:
  store i32 255, i32* %slot, align 4
  br label %done

border_side:
  store i32 15, i32* %slot, align 4
  br label %done

font:
  %font.ptr = ptrtoint i8* @p2_probe_font to i32
  store i32 %font.ptr, i32* %slot, align 4
  br label %done

coord_max:
  store i32 536870911, i32* %slot, align 4
  br label %done

rotary:
  store i32 256, i32* %slot, align 4
  br label %done

default:
  store i32 0, i32* %slot, align 4
  br label %done

done:
  ret void
}
