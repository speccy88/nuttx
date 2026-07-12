#!/usr/bin/env bash
set -euo pipefail
: "${P2LLVM_ROOT:=$HOME/.cache/p2llvm/install}"
clang="$P2LLVM_ROOT/bin/clang"
if [[ ! -x "$clang" ]]; then echo "BLOCKED: $clang not found; build pinned p2llvm first"; exit 2; fi
echo "DRAFTED: compile abi-probe sources with $clang --target=propeller2-unknown-none"
