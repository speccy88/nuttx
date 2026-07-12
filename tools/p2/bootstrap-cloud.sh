#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
mkdir -p ../apps "$HOME/.cache/p2llvm" "$HOME/.cache/loadp2" artifacts/p2
{
  echo "nuttx_commit=$(git rev-parse HEAD)"
  if git -C ../apps rev-parse HEAD >/dev/null 2>&1; then echo "nuttx_apps_commit=$(git -C ../apps rev-parse HEAD)"; else echo "nuttx_apps_commit=BLOCKED_not_cloned"; fi
  if git -C "$HOME/.cache/p2llvm" rev-parse HEAD >/dev/null 2>&1; then echo "p2llvm_commit=$(git -C "$HOME/.cache/p2llvm" rev-parse HEAD)"; else echo "p2llvm_commit=BLOCKED_not_cloned"; fi
  if git -C "$HOME/.cache/p2llvm/llvm-project" rev-parse HEAD >/dev/null 2>&1; then echo "p2llvm_llvm_project_commit=$(git -C "$HOME/.cache/p2llvm/llvm-project" rev-parse HEAD)"; else echo "p2llvm_llvm_project_commit=BLOCKED_not_cloned"; fi
  if git -C "$HOME/.cache/loadp2" rev-parse HEAD >/dev/null 2>&1; then echo "loadp2_commit=$(git -C "$HOME/.cache/loadp2" rev-parse HEAD)"; else echo "loadp2_commit=BLOCKED_not_cloned"; fi
  echo "host_cc=$(${CC:-cc} --version | head -1 2>/dev/null || true)"
  echo "cmake=$(cmake --version | head -1 2>/dev/null || true)"
  echo "ninja=$(ninja --version 2>/dev/null || true)"
  echo "build_flags=--target=propeller2-unknown-none -fno-builtin -nostdlib"
} > tools/p2/dependencies.lock
cat tools/p2/dependencies.lock
printf '%s\n' 'DRAFTED: install host packages, clone/build p2llvm/loadp2 locally if network/cache permits; no generated toolchain binaries are committed.'
