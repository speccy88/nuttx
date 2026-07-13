#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)

if [[ -f "$HOME/.p2-nuttx-env" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/.p2-nuttx-env"
fi

if [[ -f "$ROOT/.p2-hil.env" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.p2-hil.env"
fi

cfg=${1:-bringup}
apps=${NUTTX_APPS_DIR:-$ROOT/../apps}
python=${P2_PYTHON:-python3}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
art=${P2_ARTIFACTS:-$ROOT/artifacts/hil/$timestamp-build-$cfg}
log=$art/build.log

if [[ "$apps" != /* ]]; then
  apps=$ROOT/$apps
fi

[[ -d "$apps" ]] || { echo "ERROR: nuttx-apps not found at $apps" >&2; exit 1; }
apps=$(cd "$apps" && pwd)
apps_arg=$("$python" -c 'import os, sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' \
  "$apps" "$ROOT")

[[ -n "${P2LLVM_ROOT:-}" ]] || { echo "ERROR: P2LLVM_ROOT is unset" >&2; exit 1; }
[[ -x "$P2LLVM_ROOT/bin/clang" ]] ||
  { echo "ERROR: P2 clang not found at $P2LLVM_ROOT/bin/clang" >&2; exit 1; }

if command -v sysctl >/dev/null 2>&1; then
  jobs=$(sysctl -n hw.ncpu 2>/dev/null || echo 4)
elif command -v getconf >/dev/null 2>&1; then
  jobs=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)
else
  jobs=4
fi
jobs=${NPROC:-$jobs}

mkdir -p "$art"

finish()
{
  local rc=$?

  trap - EXIT
  if [[ -f "$ROOT/.config" ]]; then
    cp "$ROOT/.config" "$art/config"
  fi

  if [[ -f "$ROOT/tools/p2/toolchain.lock" ]]; then
    cp "$ROOT/tools/p2/toolchain.lock" "$art/toolchain.lock"
  fi

  printf 'status=%s\nexit_code=%d\n' \
    "$([[ $rc -eq 0 ]] && echo PASS || echo FAIL)" "$rc" > "$art/status.txt"
  exit "$rc"
}

trap finish EXIT
cd "$ROOT"

{
  echo "# p2-ec32mb:$cfg build $timestamp"
  echo "nuttx_branch=$(git branch --show-current)"
  echo "nuttx_commit=$(git rev-parse HEAD)"
  echo "apps=$apps"
  echo "apps_commit=$(git -C "$apps" rev-parse HEAD)"
  echo "P2LLVM_ROOT=$P2LLVM_ROOT"
  echo "compiler=$("$P2LLVM_ROOT/bin/clang" --version | head -1)"
  echo "jobs=$jobs"
  ./tools/configure.sh -E -a "$apps_arg" "p2-ec32mb:$cfg"
  make olddefconfig
  make -j"$jobs" V=1
} 2>&1 | tee "$log"

input_relocs=$art/input-relocations.txt
unsafe_relocs=$art/unsafe-relocations.txt
"$P2LLVM_ROOT/bin/llvm-objdump" -r staging/*.a arch/p2/src/p2_head.o \
  arch/p2/src/board/libboard.a \
  > "$input_relocs"
if awk '
  /file format/ { source = $0 }
  /^RELOCATION RECORDS FOR/ { section = $0 }
  $1 == "00000000" && $2 == "R_P2_AUG20" {
    print source
    print section
    print $0
    found = 1
  }
  END { exit found ? 0 : 1 }
' "$input_relocs" > "$unsafe_relocs"; then
  echo "ERROR: offset-zero R_P2_AUG20 would make pinned ld.lld write outside its input section" >&2
  cat "$unsafe_relocs" >&2
  exit 1
fi
rm -f "$unsafe_relocs"

[[ -s nuttx ]] || { echo "ERROR: NuttX ELF is missing or empty" >&2; exit 1; }
[[ -s nuttx.map ]] || { echo "ERROR: nuttx.map is missing or empty" >&2; exit 1; }
[[ -s System.map ]] || { echo "ERROR: System.map is missing or empty" >&2; exit 1; }
[[ -x ./tools/p2/verify-elf.py ]] ||
  { echo "ERROR: tools/p2/verify-elf.py is missing or not executable" >&2; exit 1; }

cp nuttx nuttx.map System.map "$art/"
"$P2LLVM_ROOT/bin/llvm-objcopy" -O binary nuttx nuttx.bin
cp nuttx.bin "$art/"
"$P2LLVM_ROOT/bin/llvm-readelf" -h -l -S nuttx > "$art/elf.txt"
"$P2LLVM_ROOT/bin/llvm-nm" -n nuttx > "$art/symbols.txt"
"$P2LLVM_ROOT/bin/llvm-size" nuttx > "$art/size.txt"
"$P2LLVM_ROOT/bin/llvm-objdump" -dr nuttx > "$art/disassembly.txt"
./tools/p2/verify-elf.py nuttx | tee "$art/verify-elf.txt"

echo "P2 build artifact: $art"
