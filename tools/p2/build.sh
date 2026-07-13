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

requested=${1:-bringup}
if [[ "$requested" == *:* ]]; then
  board=${requested%%:*}
  cfg=${requested#*:}
  artifact_suffix=$board-$cfg
else
  board=p2-ec32mb
  cfg=$requested
  artifact_suffix=$cfg
fi

case "$board" in
  p2-ec32mb|p2-ec)
    ;;
  *)
    echo "ERROR: unsupported P2 board '$board'" >&2
    exit 2
    ;;
esac

[[ "$cfg" =~ ^[A-Za-z0-9._-]+$ ]] ||
  { echo "ERROR: invalid P2 profile '$cfg'" >&2; exit 2; }

target=$board:$cfg
apps=${NUTTX_APPS_DIR:-$ROOT/../apps}
python=${P2_PYTHON:-python3}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
art=${P2_ARTIFACTS:-$ROOT/artifacts/hil/$timestamp-build-$artifact_suffix}
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

nuttx_branch=$(git -C "$ROOT" branch --show-current)
nuttx_commit=$(git -C "$ROOT" rev-parse HEAD)
apps_branch=$(git -C "$apps" branch --show-current)
apps_commit=$(git -C "$apps" rev-parse HEAD)
compiler=$("$P2LLVM_ROOT/bin/clang" --version | head -1)
build_command="$ROOT/tools/p2/build.sh $requested"
git -C "$ROOT" status --porcelain=v1 --untracked-files=all \
  > "$art/nuttx-source-status-before.txt"
git -C "$apps" status --porcelain=v1 --untracked-files=all \
  > "$art/apps-source-status-before.txt"
[[ ! -s "$art/nuttx-source-status-before.txt" ]] && nuttx_clean=1 || nuttx_clean=0
[[ ! -s "$art/apps-source-status-before.txt" ]] && apps_clean=1 || apps_clean=0
printf '%q ' "$ROOT/tools/p2/build.sh" "$requested" > "$art/build-command.txt"
printf '\n' >> "$art/build-command.txt"

finish()
{
  local rc=$?

  trap - EXIT
  if [[ -f "$ROOT/.config" ]]; then
    cp "$ROOT/.config" "$art/config"
  fi

  local toolchain_lock=${P2_TOOLCHAIN_LOCK:-$ROOT/tools/p2/toolchain.lock}
  if [[ -f "$toolchain_lock" ]]; then
    cp "$toolchain_lock" "$art/toolchain.lock"
  fi

  local nuttx_commit_after apps_commit_after nuttx_final_clean apps_final_clean
  local nuttx_stable apps_stable
  nuttx_commit_after=$(git -C "$ROOT" rev-parse HEAD)
  apps_commit_after=$(git -C "$apps" rev-parse HEAD)
  git -C "$ROOT" status --porcelain=v1 --untracked-files=all \
    > "$art/nuttx-source-status.txt"
  git -C "$apps" status --porcelain=v1 --untracked-files=all \
    > "$art/apps-source-status.txt"
  [[ ! -s "$art/nuttx-source-status.txt" ]] && nuttx_final_clean=1 || nuttx_final_clean=0
  [[ ! -s "$art/apps-source-status.txt" ]] && apps_final_clean=1 || apps_final_clean=0
  [[ $nuttx_clean -eq 1 && $nuttx_final_clean -eq 1 && \
     "$nuttx_commit" == "$nuttx_commit_after" ]] && nuttx_stable=1 || nuttx_stable=0
  [[ $apps_clean -eq 1 && $apps_final_clean -eq 1 && \
     "$apps_commit" == "$apps_commit_after" ]] && apps_stable=1 || apps_stable=0

  printf 'status=%s\nexit_code=%d\n' \
    "$([[ $rc -eq 0 ]] && echo PASS || echo FAIL)" "$rc" > "$art/status.txt"
  local ended_utc
  ended_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  P2_BUILD_ARTIFACT="$art" \
  P2_BUILD_STATUS="$([[ $rc -eq 0 ]] && echo PASS || echo FAIL)" \
  P2_BUILD_EXIT_CODE="$rc" P2_BUILD_BOARD="$board" \
  P2_BUILD_PROFILE="$cfg" \
  P2_BUILD_STARTED_UTC="$started_utc" P2_BUILD_ENDED_UTC="$ended_utc" \
  P2_BUILD_COMMAND="$build_command" \
  P2_BUILD_NUTTX_BRANCH="$nuttx_branch" \
  P2_BUILD_NUTTX_COMMIT="$nuttx_commit" \
  P2_BUILD_NUTTX_COMMIT_AFTER="$nuttx_commit_after" \
  P2_BUILD_APPS_PATH="$apps" P2_BUILD_APPS_BRANCH="$apps_branch" \
  P2_BUILD_APPS_COMMIT="$apps_commit" \
  P2_BUILD_APPS_COMMIT_AFTER="$apps_commit_after" \
  P2_BUILD_NUTTX_CLEAN="$nuttx_stable" P2_BUILD_APPS_CLEAN="$apps_stable" \
  P2_BUILD_P2LLVM_ROOT="$P2LLVM_ROOT" P2_BUILD_COMPILER="$compiler" \
  P2_BUILD_JOBS="$jobs" \
    "$python" "$ROOT/tools/p2/build_artifact.py" --finalize-environment || \
    { [[ $rc -ne 0 ]] || rc=1; }
  exit "$rc"
}

trap finish EXIT
cd "$ROOT"

{
  echo "# $target build $timestamp"
  echo "nuttx_branch=$nuttx_branch"
  echo "nuttx_commit=$nuttx_commit"
  echo "apps=$apps"
  echo "apps_branch=$apps_branch"
  echo "apps_commit=$apps_commit"
  echo "nuttx_source_clean=$nuttx_clean"
  echo "apps_source_clean=$apps_clean"
  echo "P2LLVM_ROOT=$P2LLVM_ROOT"
  echo "compiler=$compiler"
  echo "jobs=$jobs"
  ./tools/configure.sh -E -a "$apps_arg" "$target"
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

if [[ "$cfg" == "flashboot" || "$cfg" == "showcase" ]] &&
   ! LC_ALL=C grep -aFq \
     'P2FLASHBOOT:SMARTFS=/dev/smart0@/mnt/flash:MOUNTED:AUTOFORMAT=NO:DESTRUCTIVE_HANDLERS=ABSENT' \
     nuttx.bin; then
  echo "ERROR: $target flashboot image does not contain the startup mount marker" >&2
  exit 1
fi

if [[ "$cfg" == "showcase" ]]; then
  sd_boot_max=491516
  image_bytes=$(wc -c < nuttx.bin | tr -d '[:space:]')
  if (( image_bytes > sd_boot_max )); then
    echo "ERROR: $target image is $image_bytes bytes; serial SD writer limit is $sd_boot_max" >&2
    exit 1
  fi

  if ! LC_ALL=C grep -aFq \
       "P2SHOWCASE:READY:BOARD=$board:RUN=p2help" nuttx.bin; then
    echo "ERROR: $target image does not contain its board-specific showcase marker" >&2
    exit 1
  fi
fi

cp nuttx.bin "$art/"
"$P2LLVM_ROOT/bin/llvm-readelf" -h -l -S nuttx > "$art/elf.txt"
"$P2LLVM_ROOT/bin/llvm-nm" -n nuttx > "$art/symbols.txt"
"$P2LLVM_ROOT/bin/llvm-size" nuttx > "$art/size.txt"
"$P2LLVM_ROOT/bin/llvm-objdump" -dr nuttx > "$art/disassembly.txt"
./tools/p2/verify-elf.py nuttx | tee "$art/verify-elf.txt"

echo "P2 build artifact: $art"
