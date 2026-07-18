#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

readonly HUB_LIMIT=$((0x0007c000))

usage()
{
  echo "usage: $0 MAP [RAW_BIN [SYSTEM_MAP]]" >&2
}

blocked()
{
  echo "P2MEM:BLOCKED:$1" >&2
  exit 2
}

failed()
{
  echo "P2MEM:FAIL:$1" >&2
  exit 1
}

map_symbol()
{
  local symbol=$1
  local value

  if ! value=$(LC_ALL=C awk -v wanted="$symbol" '
    $5 == wanted {
      # LLD prints the current location in the VMA column even when the
      # symbol is assigned from another symbol.  The column is therefore
      # authoritative only for definitions tied to the location counter.

      if ($7 !~ /^ABSOLUTE($|[[:space:]]|\()/) {
        untrusted = 1
        next
      }

      candidate = $1
      if (candidate !~ /^(0[xX])?[[:xdigit:]]+$/) {
        invalid = 1
      } else {
        sub(/^0[xX]/, "", candidate)
        value = candidate
        count++
      }
    }
    END {
      if (invalid || untrusted || count != 1) {
        exit 1
      }

      print value
    }
  ' "$map_path"); then
    return 1
  fi

  printf '%u\n' "$((16#$value))"
}

system_map_symbol()
{
  local symbol=$1
  local value

  if ! value=$(LC_ALL=C awk -v wanted="$symbol" '
    $3 == wanted {
      candidate = $1
      if (candidate !~ /^(0[xX])?[[:xdigit:]]+$/) {
        invalid = 1
      } else {
        sub(/^0[xX]/, "", candidate)
        value = candidate
        count++
      }
    }
    END {
      if (invalid || count != 1) {
        exit 1
      }

      print value
    }
  ' "$system_map_path"); then
    return 1
  fi

  printf '%u\n' "$((16#$value))"
}

link_symbol()
{
  local symbol=$1

  if [[ -n "$system_map_path" ]]; then
    system_map_symbol "$symbol"
  else
    map_symbol "$symbol"
  fi
}

if (( $# < 1 || $# > 3 )); then
  usage
  blocked "ARGUMENTS:EXPECTED=MAP_[RAW_BIN_[SYSTEM_MAP]]"
fi

map_path=$1
bin_path=${2:-}
system_map_path=${3:-}

if [[ ! -f "$map_path" || ! -r "$map_path" || ! -s "$map_path" ]]; then
  blocked "MAP=MISSING_OR_EMPTY:PATH=$map_path"
fi

if [[ -n "$system_map_path" &&
      ( ! -f "$system_map_path" || ! -r "$system_map_path" ||
        ! -s "$system_map_path" ) ]]; then
  blocked "SYSTEM_MAP=MISSING_OR_EMPTY:PATH=$system_map_path"
fi

if ! data_end=$(link_symbol _edata); then
  blocked "LINK_SYMBOL=_edata:MISSING_OR_INVALID"
fi

if ! bss_start=$(link_symbol _sbss); then
  blocked "LINK_SYMBOL=_sbss:MISSING_OR_INVALID"
fi

if ! image_end=$(link_symbol _ebss); then
  blocked "MAP_SYMBOL=_ebss:MISSING_OR_INVALID"
fi

if ! stack_start=$(link_symbol _sinitialstack); then
  blocked "MAP_SYMBOL=_sinitialstack:MISSING_OR_INVALID"
fi

if ! stack_end=$(link_symbol _einitialstack); then
  blocked "MAP_SYMBOL=_einitialstack:MISSING_OR_INVALID"
fi

if ! heap_start=$(link_symbol _sheap); then
  blocked "MAP_SYMBOL=_sheap:MISSING_OR_INVALID"
fi

if ! heap_end=$(link_symbol _eheap); then
  blocked "MAP_SYMBOL=_eheap:MISSING_OR_INVALID"
fi

if ! overlay_start=$(link_symbol __p2_overlay_slot_start); then
  blocked "MAP_SYMBOL=__p2_overlay_slot_start:MISSING_OR_INVALID"
fi

if ! overlay_end=$(link_symbol __p2_overlay_slot_end); then
  blocked "MAP_SYMBOL=__p2_overlay_slot_end:MISSING_OR_INVALID"
fi

if (( image_end > HUB_LIMIT )); then
  failed "LINKED_IMAGE_END_OVERFLOW:END=$(printf '0x%08x' "$image_end"):LIMIT=$(printf '0x%08x' "$HUB_LIMIT")"
fi

if (( stack_end > HUB_LIMIT )); then
  failed "INITIAL_STACK_OVERFLOW:END=$(printf '0x%08x' "$stack_end"):LIMIT=$(printf '0x%08x' "$HUB_LIMIT")"
fi

if (( heap_end > HUB_LIMIT )); then
  failed "HEAP_OVERFLOW:END=$(printf '0x%08x' "$heap_end"):LIMIT=$(printf '0x%08x' "$HUB_LIMIT")"
fi

if (( overlay_end > HUB_LIMIT )); then
  failed "OVERLAY_OVERFLOW:END=$(printf '0x%08x' "$overlay_end"):LIMIT=$(printf '0x%08x' "$HUB_LIMIT")"
fi

if (( data_end == 0 || data_end > bss_start || bss_start > image_end ||
      stack_start < image_end || stack_start >= stack_end ||
      heap_start < stack_end || heap_start >= heap_end )); then
  blocked "MAP_LAYOUT=INVALID"
fi

if (( heap_end != overlay_start || overlay_end != HUB_LIMIT ||
      overlay_start > overlay_end )); then
  blocked "OVERLAY_LAYOUT_MISMATCH:HEAP_END=$(printf '0x%08x' "$heap_end"):OVERLAY=$(printf '0x%08x-0x%08x' "$overlay_start" "$overlay_end"):EXPECTED_END=$(printf '0x%08x' "$HUB_LIMIT")"
fi

stack_bytes=$((stack_end - stack_start))
heap_bytes=$((heap_end - heap_start))
heap_headroom=$((HUB_LIMIT - heap_start))
overlay_bytes=$((overlay_end - overlay_start))

raw_bytes=
staging_remaining=
if [[ -n "$bin_path" ]]; then
  if [[ ! -f "$bin_path" || ! -r "$bin_path" || ! -s "$bin_path" ]]; then
    blocked "RAW_BIN=MISSING_OR_EMPTY:PATH=$bin_path"
  fi

  raw_bytes=$(LC_ALL=C wc -c < "$bin_path")
  if [[ ! "$raw_bytes" =~ ^[[:space:]]*[0-9]+[[:space:]]*$ ]]; then
    blocked "RAW_BIN=INVALID_SIZE:PATH=$bin_path"
  fi

  raw_bytes=$((raw_bytes + 0))
  if (( raw_bytes > HUB_LIMIT )); then
    failed "RAW_IMAGE_OVERFLOW:BYTES=$raw_bytes:LIMIT=$HUB_LIMIT"
  fi

  # _sbss is the first non-file-backed runtime region.  The validated layout
  # places BSS before the initial stack, heap, and overlay slot, so this one
  # exclusive upper bound protects every one of those regions.

  if (( raw_bytes > bss_start )); then
    failed "RAW_IMAGE_OVERLAPS_RUNTIME:BYTES=$raw_bytes:BSS_START=$bss_start:STACK_START=$stack_start:OVERLAY_START=$overlay_start"
  fi

  staging_remaining=$((bss_start - raw_bytes))
fi

printf 'P2MEM:MAP=%s\n' "$map_path"
if [[ -n "$system_map_path" ]]; then
  printf 'P2MEM:SYSTEM_MAP=%s\n' "$system_map_path"
fi
printf 'P2MEM:HUB_LIMIT=0x%08x:BYTES=%u\n' "$HUB_LIMIT" "$HUB_LIMIT"
printf 'P2MEM:INITIALIZED_IMAGE_END=0x%08x:BYTES=%u\n' \
  "$data_end" "$data_end"
printf 'P2MEM:BSS=0x%08x-0x%08x:BYTES=%u\n' \
  "$bss_start" "$image_end" "$((image_end - bss_start))"
printf 'P2MEM:LINKED_IMAGE_END=0x%08x:BYTES=%u\n' \
  "$image_end" "$image_end"
printf 'P2MEM:INITIAL_STACK=0x%08x-0x%08x:BYTES=%u\n' \
  "$stack_start" "$stack_end" "$stack_bytes"
printf 'P2MEM:HEAP=0x%08x-0x%08x:BYTES=%u:HEADROOM_TO_0X0007C000=%u\n' \
  "$heap_start" "$heap_end" "$heap_bytes" "$heap_headroom"
printf 'P2MEM:HUB_OVERLAY_SLOT=0x%08x-0x%08x:BYTES=%u\n' \
  "$overlay_start" "$overlay_end" "$overlay_bytes"

if [[ -n "$bin_path" ]]; then
  printf 'P2MEM:RAW_IMAGE=%s:BYTES=%u:STAGING_CAPACITY=%u:STAGING_REMAINING=%u\n' \
    "$bin_path" "$raw_bytes" "$bss_start" "$staging_remaining"
fi

echo "P2MEM:PASS:STATICALLY-VERIFIED"
