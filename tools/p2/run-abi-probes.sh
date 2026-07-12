#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
: "${P2LLVM_ROOT:=$HOME/.cache/p2-nuttx/p2llvm/install}"
if [[ ! -x "$P2LLVM_ROOT/bin/clang" && -x "$HOME/.cache/p2-nuttx/p2llvm/install/bin/clang" ]]; then
  P2LLVM_ROOT="$HOME/.cache/p2-nuttx/p2llvm/install"
fi
CLANG=${P2_CLANG:-$P2LLVM_ROOT/bin/clang}
LLVM_OBJDUMP=${P2_OBJDUMP:-$P2LLVM_ROOT/bin/llvm-objdump}
LLVM_READELF=${P2_READELF:-$P2LLVM_ROOT/bin/llvm-readelf}
LLVM_NM=${P2_NM:-$P2LLVM_ROOT/bin/llvm-nm}
OUT=${P2_ABI_ARTIFACTS:-$ROOT/artifacts/cloud-p2/abi}
SRC=$ROOT/tools/p2/abi-probe
TARGET=${P2_TARGET:-p2}
FLAGS=(-fno-jump-tables -ffunction-sections -fdata-sections -fno-common -fno-builtin -nostdlib)

if [[ ! -x "$CLANG" ]]; then
  echo "BLOCKED: $CLANG not found; run tools/p2/bootstrap-cloud.sh or set P2LLVM_ROOT/P2_CLANG" >&2
  exit 2
fi
mkdir -p "$SRC" "$OUT"
cat > "$SRC/core.c" <<'EOS'
#include <stdarg.h>
struct pair { unsigned a; unsigned b; };
volatile unsigned sink;
void leaf(void) { sink++; }
unsigned nonleaf(unsigned a) { leaf(); return a + sink; }
unsigned recurse(unsigned n) { return n < 2 ? n : recurse(n - 1) + recurse(n - 2); }
unsigned pressure(unsigned x) { unsigned r = x; for (unsigned i = 0; i < 32; i++) r = r * 33u + i; return r; }
unsigned fp(unsigned (*fn)(unsigned), unsigned v) { return fn(v); }
unsigned sw(unsigned v) { switch (v) { case 0: return 11; case 7: return 77; default: return v + 3; } }
unsigned long long wide(unsigned long long a, unsigned long long b) { return (a / 3u) + (b % 17u); }
struct pair byval(struct pair p) { p.a += p.b; return p; }
unsigned varg(int n, ...) { va_list ap; va_start(ap, n); unsigned s = 0; for (int i = 0; i < n; i++) s += va_arg(ap, unsigned); va_end(ap); return s; }
__attribute__((weak)) void weak_hook(void) {}
__attribute__((section(".p2probe"))) unsigned custom_section = 0x12345678u;
void memops(void *d, const void *s) { __builtin_memcpy(d, s, 16); __builtin_memset(d, 0xa5, 8); }
unsigned atom(unsigned *p) { return __atomic_fetch_add(p, 1, __ATOMIC_SEQ_CST); }
EOS

for opt in O0 Os O2; do
  odir="$OUT/$opt"
  mkdir -p "$odir"
  printf '%q ' "$CLANG" --target="$TARGET" -"$opt" "${FLAGS[@]}" -S "$SRC/core.c" -o "$odir/core.s" > "$odir/compile.cmd"
  echo >> "$odir/compile.cmd"
  "$CLANG" --target="$TARGET" -"$opt" "${FLAGS[@]}" -S "$SRC/core.c" -o "$odir/core.s"
  "$CLANG" --target="$TARGET" -"$opt" "${FLAGS[@]}" -c "$SRC/core.c" -o "$odir/core.o"
  "$LLVM_OBJDUMP" -dr "$odir/core.o" > "$odir/core.dis" 2>&1 || true
  "$LLVM_READELF" -S "$odir/core.o" > "$odir/core.sections" 2>&1 || true
  "$LLVM_NM" -n "$odir/core.o" > "$odir/core.syms" 2>&1 || true
done

echo "COMPILED: ABI probes for --target=$TARGET preserved under $OUT"
