#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
source "$HOME/.p2-nuttx-env" 2>/dev/null || true
cfg=${1:-nsh}
apps=${NUTTX_APPS_DIR:-../apps}
art=${P2_ARTIFACTS:-$ROOT/artifacts/cloud-p2/$cfg}
mkdir -p "$art"
log="$art/build.log"

{
  echo "# p2-ec32mb:$cfg build $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "apps=$apps"
  echo "P2LLVM_ROOT=${P2LLVM_ROOT:-}"
  ./tools/configure.sh -a "$apps" "p2-ec32mb:$cfg"
  make olddefconfig
  make -j"${NPROC:-$(nproc)}" V=1
} 2>&1 | tee "$log"

cp .config "$art/config" 2>/dev/null || true
for f in nuttx nuttx.bin nuttx.hex nuttx.map System.map; do
  [[ -f $f ]] && cp "$f" "$art/"
done
if [[ -f nuttx && -n ${P2LLVM_ROOT:-} && -x ${P2LLVM_ROOT}/bin/llvm-readelf ]]; then
  "${P2LLVM_ROOT}/bin/llvm-readelf" -S nuttx > "$art/sections.txt" || true
  "${P2LLVM_ROOT}/bin/llvm-nm" -n nuttx > "$art/symbols.txt" || true
  "${P2LLVM_ROOT}/bin/llvm-size" nuttx > "$art/size.txt" || true
  "${P2LLVM_ROOT}/bin/llvm-objdump" -dr nuttx > "$art/disassembly.txt" || true
  ./tools/p2/verify-elf.py nuttx > "$art/verify-elf.txt" 2>&1 || true
fi
