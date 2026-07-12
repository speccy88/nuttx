#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
CACHE=${P2_CACHE:-$HOME/.cache/p2-nuttx}
APPS_DIR=${NUTTX_APPS_DIR:-$ROOT/../apps}
P2LLVM_SRC=${P2LLVM_SRC:-$CACHE/p2llvm-src}
P2LLVM_ROOT=${P2LLVM_ROOT:-$CACHE/p2llvm/install}
if [[ ! -x "$P2LLVM_ROOT/bin/clang" && -x "$CACHE/p2llvm/install/bin/clang" ]]; then P2LLVM_ROOT="$CACHE/p2llvm/install"; fi
LOADP2_SRC=${LOADP2_SRC:-$CACHE/loadp2-src}
LOADP2_ROOT=${LOADP2_ROOT:-$CACHE/loadp2}
JOBS=${JOBS:-$(nproc 2>/dev/null || echo 2)}

mkdir -p "$CACHE" "$P2LLVM_ROOT" "$LOADP2_ROOT" "$ROOT/artifacts/cloud-p2"

need_cmd() { command -v "$1" >/dev/null 2>&1 || echo "BLOCKED_missing_$1"; }
clone_if_missing() {
  local url=$1 dir=$2 ref=${3:-}
  if [[ ! -d "$dir/.git" ]]; then
    git clone --recursive "$url" "$dir"
  fi
  if [[ -n "$ref" ]]; then
    git -C "$dir" fetch --tags origin "$ref" || true
    git -C "$dir" checkout "$ref"
    git -C "$dir" submodule update --init --recursive
  fi
}

if [[ ${P2_BOOTSTRAP_FETCH:-1} == 1 ]]; then
  [[ -d "$APPS_DIR/.git" ]] || clone_if_missing https://github.com/apache/nuttx-apps "$APPS_DIR" "${NUTTX_APPS_REF:-master}"
  [[ -d "$P2LLVM_SRC/.git" ]] || clone_if_missing https://github.com/ne75/p2llvm "$P2LLVM_SRC" "${P2LLVM_REF:-master}"
  [[ -d "$LOADP2_SRC/.git" ]] || clone_if_missing https://github.com/totalspectrum/loadp2 "$LOADP2_SRC" "${LOADP2_REF:-master}"
fi

if [[ ${P2_BOOTSTRAP_BUILD:-0} == 1 && -d "$P2LLVM_SRC/.git" ]]; then
  cmake -S "$P2LLVM_SRC/llvm-project/llvm" -B "$P2LLVM_SRC/build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$P2LLVM_ROOT" \
    -DLLVM_TARGETS_TO_BUILD=P2 -DLLVM_ENABLE_PROJECTS=clang
  cmake --build "$P2LLVM_SRC/build" --target install -j"$JOBS"
fi

if [[ ${P2_BOOTSTRAP_BUILD:-0} == 1 && -d "$LOADP2_SRC/.git" ]]; then
  make -C "$LOADP2_SRC" -j"$JOBS"
  install -D "$LOADP2_SRC/loadp2" "$LOADP2_ROOT/bin/loadp2"
fi

{
  echo "export NUTTX_APPS_DIR=$APPS_DIR"
  echo "export P2LLVM_ROOT=$P2LLVM_ROOT"
  echo "export PATH=$P2LLVM_ROOT/bin:$LOADP2_ROOT/bin:\$PATH"
} > "$HOME/.p2-nuttx-env"

{
  echo "nuttx_commit=$(git -C "$ROOT" rev-parse HEAD)"
  echo "nuttx_upstream_base=$(git -C "$ROOT" merge-base HEAD origin/master 2>/dev/null || true)"
  echo "nuttx_apps_commit=$(git -C "$APPS_DIR" rev-parse HEAD 2>/dev/null || echo BLOCKED_not_available)"
  echo "p2llvm_commit=$(git -C "$P2LLVM_SRC" rev-parse HEAD 2>/dev/null || echo BLOCKED_not_available)"
  echo "p2llvm_llvm_project_commit=$(git -C "$P2LLVM_SRC/llvm-project" rev-parse HEAD 2>/dev/null || echo BLOCKED_not_available)"
  echo "loadp2_commit=$(git -C "$LOADP2_SRC" rev-parse HEAD 2>/dev/null || echo BLOCKED_not_available)"
  echo "host_os=$(uname -a)"
  echo "host_cc=$(${CC:-cc} --version 2>/dev/null | head -1 || true)"
  echo "cmake=$(cmake --version 2>/dev/null | head -1 || true)"
  echo "ninja=$(ninja --version 2>/dev/null || true)"
  echo "python=$(python3 --version 2>/dev/null || true)"
  echo "kconfig_conf=$(command -v kconfig-conf || echo BLOCKED_missing)"
  echo "clang=$([[ -x "$P2LLVM_ROOT/bin/clang" ]] && "$P2LLVM_ROOT/bin/clang" --version | head -1 || echo BLOCKED_missing)"
  echo "loadp2=$([[ -x "$LOADP2_ROOT/bin/loadp2" ]] && "$LOADP2_ROOT/bin/loadp2" -h 2>&1 | head -1 || echo BLOCKED_missing)"
  echo "p2_flags=--target=p2 -fno-jump-tables -ffunction-sections -fdata-sections -fno-common -fno-builtin -nostdlib"
} > "$ROOT/tools/p2/dependencies.lock"

cat "$ROOT/tools/p2/dependencies.lock"
echo "Wrote $HOME/.p2-nuttx-env"
