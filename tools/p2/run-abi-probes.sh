#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Offline ABI/code-generation evidence for the pinned p2llvm compiler.  This
# script never opens a serial device and never uses libp2 or a target libc.

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SRC=$ROOT/tools/p2/abi-probe
OUT_ROOT=${P2_ABI_ARTIFACTS:-$ROOT/artifacts/hil/abi}
TARGET=${P2_TARGET:-p2}

if [[ -f "$HOME/.p2-nuttx-env" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/.p2-nuttx-env"
fi

: "${P2LLVM_ROOT:=/Volumes/SSD2TB/Code/.p2-nuttx-cache/p2llvm/install}"
CLANG=${P2_CLANG:-$P2LLVM_ROOT/bin/clang}
LD_LLD=${P2_LD_LLD:-$P2LLVM_ROOT/bin/ld.lld}
LLVM_NM=${P2_NM:-$P2LLVM_ROOT/bin/llvm-nm}
LLVM_OBJDUMP=${P2_OBJDUMP:-$P2LLVM_ROOT/bin/llvm-objdump}
LLVM_READELF=${P2_READELF:-$P2LLVM_ROOT/bin/llvm-readelf}
LLVM_SIZE=${P2_SIZE:-$P2LLVM_ROOT/bin/llvm-size}
LOCK=${P2_TOOLCHAIN_LOCK:-$ROOT/tools/p2/toolchain.lock}

TOOLS=("$CLANG" "$LD_LLD" "$LLVM_NM" "$LLVM_OBJDUMP" "$LLVM_READELF" "$LLVM_SIZE")
for tool in "${TOOLS[@]}"; do
  if [[ ! -x "$tool" ]]; then
    echo "BLOCKED: required P2 tool is not executable: $tool" >&2
    exit 2
  fi
done

if [[ ! -f "$LOCK" ]]; then
  echo "BLOCKED: missing pinned toolchain record: $LOCK" >&2
  exit 2
fi

expected_clang_sha=$(sed -n "s|^sha256=\([0-9a-f][0-9a-f]*\)  $CLANG$|\1|p" "$LOCK")
if [[ -z "$expected_clang_sha" ]]; then
  echo "BLOCKED: $LOCK does not pin $CLANG" >&2
  exit 2
fi

actual_clang_sha=$(shasum -a 256 "$CLANG" | awk '{print $1}')
if [[ "$actual_clang_sha" != "$expected_clang_sha" ]]; then
  echo "BLOCKED: clang hash differs from $LOCK" >&2
  echo "expected=$expected_clang_sha" >&2
  echo "actual=$actual_clang_sha" >&2
  exit 2
fi

expected_llvm_commit=$(sed -n 's/^p2llvm_llvm_project_commit=//p' "$LOCK")
compiler_version=$($CLANG --version)
if [[ -z "$expected_llvm_commit" || "$compiler_version" != *"$expected_llvm_commit"* ]]; then
  echo "BLOCKED: compiler version does not identify the LLVM commit pinned in $LOCK" >&2
  echo "expected_llvm_commit=$expected_llvm_commit" >&2
  echo "$compiler_version" >&2
  exit 2
fi

if ! "$CLANG" --target="$TARGET" -dM -E -x c /dev/null | grep -q '^#define __propeller2__ 1$'; then
  echo "BLOCKED: $CLANG does not advertise the Propeller 2 target for --target=$TARGET" >&2
  exit 2
fi

RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
RUN=$OUT_ROOT/$RUN_ID
mkdir -p "$RUN/source"
cp "$SRC"/*.c "$SRC"/*.h "$SRC"/*.S "$SRC"/*.ld "$SRC"/README.md "$RUN/source/"
cp "$ROOT/tools/p2/run-abi-probes.sh" "$LOCK" "$RUN/"

COMMON_FLAGS=(
  --target="$TARGET"
  -std=c11
  -ffreestanding
  -fno-builtin
  -fno-common
  -fno-jump-tables
  -ffunction-sections
  -fdata-sections
  -fno-omit-frame-pointer
  -Wall
  -Wextra
  -Werror
  -nostdlib
)

REQUIRED_C=(
  calls
  pressure
  arguments
  arithmetic
  comparison64
  memory
  sections
  volatile
  pasm2
  fixed_registers
  atomics
  link_entry
)
LINKABLE=(calls arguments sections volatile pasm2 fixed_registers link_entry block_context)

write_command()
{
  local file=$1
  shift
  printf '%q ' "$@" > "$file"
  printf '\n' >> "$file"
}

run_required()
{
  local stem=$1
  shift
  write_command "$stem.cmd" "$@"
  if ! "$@" >"$stem.stdout" 2>"$stem.stderr"; then
    echo "FAILED: required command; see $stem.stderr" >&2
    sed -n '1,160p' "$stem.stderr" >&2
    exit 1
  fi
}

run_expected_compile()
{
  local stem=$1
  local source=$2
  shift 2
  write_command "$stem.cmd" "$@"
  if "$@" >"$stem.stdout" 2>"$stem.stderr"; then
    printf 'SUPPORTED\n' > "$stem.status"
  else
    if grep -Eq 'Cannot select|fatal error: error in backend|unsupported|not supported|invalid output constraint|unknown register name|couldn.t allocate input reg' "$stem.stderr"; then
      printf 'UNSUPPORTED\n' > "$stem.status"
    else
      echo "FAILED: unclassified compiler failure for $source; see $stem.stderr" >&2
      sed -n '1,160p' "$stem.stderr" >&2
      exit 1
    fi
  fi
}

{
  echo "run_id=$RUN_ID"
  echo "source_commit=$(git -C "$ROOT" rev-parse HEAD)"
  echo "target=$TARGET"
  echo "clang=$CLANG"
  echo "clang_sha256=$actual_clang_sha"
  echo "$compiler_version"
  "$LD_LLD" --version
  uname -a
} > "$RUN/toolchain.txt"

for opt in O0 Os O2; do
  ODIR=$RUN/$opt
  mkdir -p "$ODIR"

  for name in "${REQUIRED_C[@]}"; do
    source=$SRC/$name.c
    base=$ODIR/$name
    run_required "$base.assembly" "$CLANG" "${COMMON_FLAGS[@]}" -"$opt" -S "$source" -o "$base.s"
    run_required "$base.object" "$CLANG" "${COMMON_FLAGS[@]}" -"$opt" -c "$source" -o "$base.o"
    run_required "$base.disassembly" "$LLVM_OBJDUMP" -dr "$base.o"
    run_required "$base.sections" "$LLVM_READELF" -SW "$base.o"
    run_required "$base.relocations" "$LLVM_READELF" -rW "$base.o"
    run_required "$base.symbols" "$LLVM_NM" -an "$base.o"
    run_required "$base.size" "$LLVM_SIZE" -A "$base.o"
  done

  asm_base=$ODIR/block_context
  run_required "$asm_base.object" "$CLANG" --target="$TARGET" -c "$SRC/block_context.S" -o "$asm_base.o"
  run_required "$asm_base.disassembly" "$LLVM_OBJDUMP" -dr "$asm_base.o"
  run_required "$asm_base.sections" "$LLVM_READELF" -SW "$asm_base.o"
  run_required "$asm_base.relocations" "$LLVM_READELF" -rW "$asm_base.o"
  run_required "$asm_base.symbols" "$LLVM_NM" -an "$asm_base.o"

  objects=()
  for name in "${REQUIRED_C[@]}" block_context; do
    objects+=("$ODIR/$name.o")
  done
  run_required "$ODIR/combined.object" "$LD_LLD" -r "${objects[@]}" -o "$ODIR/combined.o"
  run_required "$ODIR/combined.disassembly" "$LLVM_OBJDUMP" -dr "$ODIR/combined.o"
  run_required "$ODIR/combined.sections" "$LLVM_READELF" -SW "$ODIR/combined.o"
  run_required "$ODIR/combined.relocations" "$LLVM_READELF" -rW "$ODIR/combined.o"
  run_required "$ODIR/combined.symbols" "$LLVM_NM" -an "$ODIR/combined.o"

  link_objects=()
  for name in "${LINKABLE[@]}"; do
    link_objects+=("$ODIR/$name.o")
  done
  run_required "$ODIR/linked.link" "$LD_LLD" -T "$SRC/link.ld" -Map="$ODIR/linked.map" "${link_objects[@]}" -o "$ODIR/linked.elf"
  run_required "$ODIR/linked.disassembly" "$LLVM_OBJDUMP" -dr "$ODIR/linked.elf"
  run_required "$ODIR/linked.header" "$LLVM_READELF" -hW "$ODIR/linked.elf"
  run_required "$ODIR/linked.sections" "$LLVM_READELF" -SW "$ODIR/linked.elf"
  run_required "$ODIR/linked.relocations" "$LLVM_READELF" -rW "$ODIR/linked.elf"
  run_required "$ODIR/linked.symbols" "$LLVM_NM" -an "$ODIR/linked.elf"
  run_required "$ODIR/linked.size" "$LLVM_SIZE" -A "$ODIR/linked.elf"

  atomic_diag=$ODIR/atomic_compare_exchange
  run_expected_compile "$atomic_diag" "$SRC/atomic_compare_exchange.c" \
    "$CLANG" "${COMMON_FLAGS[@]}" -Wno-error=atomic-alignment -"$opt" -S \
    "$SRC/atomic_compare_exchange.c" -o "$atomic_diag.s"
  if [[ $(<"$atomic_diag.status") == SUPPORTED ]]; then
    run_required "$atomic_diag.object" "$CLANG" "${COMMON_FLAGS[@]}" \
      -Wno-error=atomic-alignment -"$opt" -c \
      "$SRC/atomic_compare_exchange.c" -o "$atomic_diag.o"
    run_required "$atomic_diag.disassembly" "$LLVM_OBJDUMP" -dr "$atomic_diag.o"
    run_required "$atomic_diag.relocations" "$LLVM_READELF" -rW "$atomic_diag.o"
    run_required "$atomic_diag.symbols" "$LLVM_NM" -an "$atomic_diag.o"
  fi

  atomic_ls=$ODIR/atomic_load_store
  run_expected_compile "$atomic_ls" "$SRC/atomic_load_store.c" \
    "$CLANG" "${COMMON_FLAGS[@]}" -Wno-error=atomic-alignment -"$opt" -S \
    "$SRC/atomic_load_store.c" -o "$atomic_ls.s"
  if [[ $(<"$atomic_ls.status") == SUPPORTED ]]; then
    run_required "$atomic_ls.object" "$CLANG" "${COMMON_FLAGS[@]}" \
      -Wno-error=atomic-alignment -"$opt" -c "$SRC/atomic_load_store.c" \
      -o "$atomic_ls.o"
    run_required "$atomic_ls.disassembly" "$LLVM_OBJDUMP" -dr "$atomic_ls.o"
    run_required "$atomic_ls.relocations" "$LLVM_READELF" -rW "$atomic_ls.o"
    run_required "$atomic_ls.symbols" "$LLVM_NM" -an "$atomic_ls.o"
  fi

  div64=$ODIR/arithmetic_div64
  run_expected_compile "$div64" "$SRC/arithmetic_div64.c" \
    "$CLANG" "${COMMON_FLAGS[@]}" -"$opt" -S "$SRC/arithmetic_div64.c" \
    -o "$div64.s"
  if [[ $(<"$div64.status") == SUPPORTED ]]; then
    run_required "$div64.object" "$CLANG" "${COMMON_FLAGS[@]}" -"$opt" \
      -c "$SRC/arithmetic_div64.c" -o "$div64.o"
    run_required "$div64.disassembly" "$LLVM_OBJDUMP" -dr "$div64.o"
    run_required "$div64.relocations" "$LLVM_READELF" -rW "$div64.o"
    run_required "$div64.symbols" "$LLVM_NM" -an "$div64.o"
  fi
done

{
  echo "STATICALLY-VERIFIED: pinned P2 compiler ABI probes completed offline"
  echo "run=$RUN"
  echo "target=$TARGET"
  echo "clang_sha256=$actual_clang_sha"
  echo
  echo "Generated call/return instructions:"
  grep -Ehi '\b(calla|reta)\b' "$RUN"/{O0,Os,O2}/*.s | sed -E 's/^[[:space:]]+//' | sort -u
  echo
  echo "Compiler-lowered C arithmetic q-pipeline instructions:"
  if ! grep -Ehi '\b(qmul|qdiv|getqx|getqy)\b' "$RUN"/{O0,Os,O2}/arithmetic.s |
    sed -E 's/^[[:space:]]+//' | sort -u; then
    echo "none"
  fi
  echo
  echo "Explicit inline PASM2 q-pipeline instructions:"
  grep -Ehi '\b(qmul|qdiv|getqx|getqy)\b' "$RUN"/{O0,Os,O2}/pasm2.s |
    sed -E 's/^[[:space:]]+//' | sort -u
  echo
  echo "Undefined/runtime helper symbols by optimization level:"
  for opt in O0 Os O2; do
    echo "[$opt]"
    "$LLVM_NM" -u "$RUN/$opt/combined.o"
  done
  echo
  echo "Atomic lowering diagnostics and undefined symbols:"
  for opt in O0 Os O2; do
    for name in atomic_load_store atomic_compare_exchange; do
      printf '[%s/%s] ' "$opt" "$name"
      cat "$RUN/$opt/$name.status"
      if [[ -f "$RUN/$opt/$name.o" ]]; then
        "$LLVM_NM" -u "$RUN/$opt/$name.o"
      fi
      sed -n '1p' "$RUN/$opt/$name.stderr"
    done
  done
  echo
  echo "Expected-capability probes:"
  for opt in O0 Os O2; do
    printf '%s atomic_load_store=' "$opt"
    cat "$RUN/$opt/atomic_load_store.status"
    printf '%s atomic_compare_exchange=' "$opt"
    cat "$RUN/$opt/atomic_compare_exchange.status"
    printf '%s arithmetic_div64=' "$opt"
    cat "$RUN/$opt/arithmetic_div64.status"
    sed -n '1p' "$RUN/$opt/arithmetic_div64.stderr"
  done
  echo
  echo "Fixed general-register names accepted by inline constraints:"
  tr -s '[:space:],' '\n' < "$RUN/O0/fixed_registers.s" |
    grep -E '^r([0-9]|[12][0-9]|3[01])$' | sort -u
  echo
  echo "Special-register and C/Z access instructions:"
  grep -Ehi '\b(mov.*(pa|pb|ptra)|wrc|wrz)\b' "$RUN"/{O0,Os,O2}/pasm2.s |
    sed -E 's/^[[:space:]]+//' | sort -u
  echo
  echo "Block transfer instructions:"
  grep -Ehi '\b(setq|rdlong|wrlong)\b' "$RUN"/{O0,Os,O2}/block_context.disassembly.stdout | sed -E 's/^[[:space:]]+//' | sort -u
  echo
  echo "Relocation types:"
  grep -Eh 'R_P2_' "$RUN"/{O0,Os,O2}/*.relocations.stdout | sed -E 's/.*(R_P2_[A-Za-z0-9_]+).*/\1/' | sort -u
} > "$RUN/summary.txt"

ln -sfn "$RUN_ID" "$OUT_ROOT/latest"
cat "$RUN/summary.txt"
