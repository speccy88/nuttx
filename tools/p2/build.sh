#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
caller_apps=${NUTTX_APPS_DIR:-}
caller_p2llvm_root=${P2LLVM_ROOT:-}
caller_toolchain_lock=${P2_TOOLCHAIN_LOCK:-}

if [[ -f "$HOME/.p2-nuttx-env" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/.p2-nuttx-env"
fi

if [[ -f "$ROOT/.p2-hil.env" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.p2-hil.env"
fi

# Explicit per-build apps, compiler, and lock selections must win over
# convenience defaults from the persistent environment files.  This keeps an
# isolated source worktree paired with its exact companion and toolchain.

if [[ -n "$caller_apps" ]]; then
  NUTTX_APPS_DIR=$caller_apps
fi
if [[ -n "$caller_p2llvm_root" ]]; then
  P2LLVM_ROOT=$caller_p2llvm_root
fi
if [[ -n "$caller_toolchain_lock" ]]; then
  P2_TOOLCHAIN_LOCK=$caller_toolchain_lock
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
unified_profile=0
case "$cfg" in
  unified|unified-hil|python)
    unified_profile=1
    ;;
esac

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

if [[ $unified_profile -eq 1 ]]; then
  [[ -x "$P2LLVM_ROOT/bin/llc" ]] ||
    { echo "ERROR: P2 llc not found at $P2LLVM_ROOT/bin/llc" >&2; exit 1; }
  [[ -x "$ROOT/tools/p2/check-unified-memory-codegen.py" ]] ||
    { echo "ERROR: unified-memory compiler verifier is missing" >&2; exit 1; }
fi

if [[ "$cfg" == python ]]; then
  [[ -x "$P2LLVM_ROOT/bin/p2-overlay-link.py" ]] ||
    { echo "ERROR: P2 overlay linker helper is missing" >&2; exit 1; }
  [[ -x "$ROOT/tools/p2/check-hub-overlay-codegen.py" ]] ||
    { echo "ERROR: Hub-overlay compiler verifier is missing" >&2; exit 1; }
  [[ -f "$ROOT/tools/p2/p2_python_package.py" ]] ||
    { echo "ERROR: P2 Python packager is missing" >&2; exit 1; }
  "$python" "$ROOT/tools/p2/p2llvm-runtime.py" verify-archive \
    --toolchain-root "$P2LLVM_ROOT"
fi

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
toolchain_lock=${P2_TOOLCHAIN_LOCK:-$ROOT/tools/p2/toolchain.lock}
build_command="$ROOT/tools/p2/build.sh $requested"
git -C "$ROOT" status --porcelain=v1 --untracked-files=all \
  > "$art/nuttx-source-status-before.txt"
git -C "$apps" status --porcelain=v1 --untracked-files=all \
  > "$art/apps-source-status-before.txt"
[[ ! -s "$art/nuttx-source-status-before.txt" ]] && nuttx_clean=1 || nuttx_clean=0
[[ ! -s "$art/apps-source-status-before.txt" ]] && apps_clean=1 || apps_clean=0
printf '%q ' "$ROOT/tools/p2/build.sh" "$requested" > "$art/build-command.txt"
printf '\n' >> "$art/build-command.txt"

verify_unified_toolchain_lock()
{
  local file
  local digest

  [[ -f "$toolchain_lock" ]] ||
    { echo "ERROR: unified build toolchain lock is missing: $toolchain_lock" >&2;
      return 1; }

  for file in \
    "$P2LLVM_ROOT/bin/clang" \
    "$P2LLVM_ROOT/bin/clang++" \
    "$P2LLVM_ROOT/bin/ld.lld" \
    "$P2LLVM_ROOT/bin/llc" \
    "$P2LLVM_ROOT/bin/llvm-ar" \
    "$P2LLVM_ROOT/bin/llvm-nm" \
    "$P2LLVM_ROOT/bin/llvm-objcopy" \
    "$P2LLVM_ROOT/bin/llvm-objdump" \
    "$P2LLVM_ROOT/bin/llvm-readelf" \
    "$P2LLVM_ROOT/bin/llvm-readobj" \
    "$P2LLVM_ROOT/bin/llvm-size" \
    "$P2LLVM_ROOT/bin/llvm-strip" \
    "$P2LLVM_ROOT/bin/p2-overlay-link.py" \
    "$P2LLVM_ROOT/libp2/lib/libcompiler_builtins.a" \
    "$ROOT/tools/p2/patches/p2llvm-preempt-safe-integer.patch" \
    "$ROOT/tools/p2/patches/p2llvm-unified-memory.patch" \
    "$ROOT/tools/p2/patches/p2llvm-python-overlays.patch"
  do
    [[ -f "$file" ]] ||
      { echo "ERROR: unified toolchain input is missing: $file" >&2;
        return 1; }
    digest=$("$python" -c \
      'import hashlib, sys; h = hashlib.sha256(); f = open(sys.argv[1], "rb"); [h.update(chunk) for chunk in iter(lambda: f.read(1048576), b"")]; print(h.hexdigest())' \
      "$file")
    awk -v key="sha256=$digest" -v path="$file" \
      '$1 == key && $2 == path { found = 1 } END { exit !found }' \
      "$toolchain_lock" ||
      { echo "ERROR: unified toolchain lock does not pin $file at $digest" >&2;
        return 1; }
  done

  echo "unified_toolchain_lock=verified"
}

finish()
{
  local rc=$?

  trap - EXIT
  if [[ -f "$ROOT/.config" ]]; then
    cp "$ROOT/.config" "$art/config"
  fi

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
  if [[ $unified_profile -eq 1 ]]; then
    "$python" ./tools/p2/check-unified-memory-codegen.py \
      --clang "$P2LLVM_ROOT/bin/clang" \
      --llc "$P2LLVM_ROOT/bin/llc" |
      tee "$art/unified-codegen.txt"
    verify_unified_toolchain_lock
  fi
  if [[ "$cfg" == python ]]; then
    "$python" ./tools/p2/check-hub-overlay-codegen.py \
      --toolchain-root "$P2LLVM_ROOT" |
      tee "$art/hub-overlay-codegen.txt"
  fi
  "$python" ./tools/p2/build_artifact.py \
    --verify-toolchain-lock "$toolchain_lock" \
    --nuttx-commit "$nuttx_commit" \
    --apps-commit "$apps_commit"
  ./tools/configure.sh -E -a "$apps_arg" "$target"
  make olddefconfig
  if [[ "$cfg" == python ]]; then
    # P2 intentionally uses null dependency files.  Rebuild application
    # objects after regenerating the builtin registry so a command added or
    # removed by Kconfig cannot leave a stale builtin table in libapps.a.

    make apps_clean
  fi
  if [[ $unified_profile -eq 1 ]]; then
    LC_ALL=C grep -Fqx 'CONFIG_P2_EC32MB_PSRAM_UNIFIED=y' .config ||
      { echo "ERROR: $target does not enable the unified PSRAM ABI" >&2;
        exit 1; }
    if LC_ALL=C grep -Fqx 'CONFIG_P2_EC32MB_PSRAM=y' .config; then
      echo "ERROR: $target enables the legacy /dev/psram0 driver" >&2
      exit 1
    fi
    if LC_ALL=C grep -aFq '/dev/psram0' .config; then
      echo "ERROR: $target configuration contains the legacy PSRAM device path" >&2
      exit 1
    fi
    if [[ "$cfg" == python ]]; then
      # The eight always-resident PyMem/PyObject frontends are funded by the
      # 2304-byte kernel-heap reduction.  The measured CPython
      # startup-hot resident cluster is funded from the 8208-byte group-table
      # BSS allocation reclaimed by staging that init-only table in the
      # unpublished overlay slot.  The 63232-byte kernel heap also preserves
      # at least one KiB for the in-Hub user-heap bootstrap metadata; source,
      # link-time, and runtime audits lock both budgets and real placement.
      # Python rebalances the existing 2304 console-RX BSS bytes from a
      # 256/2048 lower/upper split to 1024/1280.  A complete 1012-byte upload
      # frame then fits in both layers at 2 Mbaud without reducing either
      # heap or relying on host-side quiet gaps.

      for required in \
        CONFIG_FS_TMPFS=y \
        CONFIG_ARCH_HAVE_RNG=y \
        CONFIG_DEV_RANDOM=y \
        CONFIG_DEV_URANDOM_ARCH=y \
		CONFIG_P2_RNG_BLAKE2S=y \
		CONFIG_P2_HUB_OVERLAY_ZLIB=y \
		CONFIG_CRYPTO=y \
        CONFIG_FS_HEAPSIZE=1048576 \
        CONFIG_FS_HEAP_USER_BUFFER=y \
        'CONFIG_LIBC_TMPDIR="/tmp"' \
        CONFIG_INTERPRETERS_CPYTHON_ROMFS_SECTORSIZE=512 \
        CONFIG_INTERPRETERS_CPYTHON_P2_DEFAULT_NO_SITE=y \
        CONFIG_INTERPRETERS_CPYTHON_P2_FIXED_PATH_CONFIG=y \
        CONFIG_INTERPRETERS_CPYTHON_P2_OVERLAY_TELEMETRY=y \
        CONFIG_INTERPRETERS_CPYTHON_P2_OVERLAY_TELEMETRY_INTERVAL_MS=60000 \
		'CONFIG_INTERPRETERS_CPYTHON_PYTHONPATH="/tmp"' \
        CONFIG_STACK_COLORATION=y \
        CONFIG_MM_KERNEL_HEAP=y \
        CONFIG_MM_KERNEL_HEAPSIZE=63232 \
        CONFIG_INTERPRETERS_CPYTHON_STACKSIZE=24576 \
        CONFIG_P2_UART_RX_RING_SIZE=1024 \
        CONFIG_UART0_BAUD=2000000 \
        CONFIG_UART0_RXBUFSIZE=1280 \
        CONFIG_NFILE_DESCRIPTORS_PER_BLOCK=8 \
        CONFIG_TLS_NELEM=16 \
		CONFIG_TLS_TASK_NELEM=8 \
		'# CONFIG_RAW_BINARY is not set' \
		'# CONFIG_LIB_ZLIB_TEST is not set' \
		'# CONFIG_UTILS_GZIP is not set' \
		'# CONFIG_UTILS_ZIP is not set' \
		'# CONFIG_UTILS_UNZIP is not set' \
        '# CONFIG_NSH_DISABLE_ECHO is not set' \
        'CONFIG_NSH_DISABLE_HELP=y' \
        '# CONFIG_NSH_DISABLE_MKDIR is not set' \
        '# CONFIG_NSH_DISABLE_MOUNT is not set'
      do
        LC_ALL=C grep -Fqx "$required" .config ||
          { echo "ERROR: Python runtime telemetry/heap contract is missing $required" >&2;
            exit 1; }
      done
    fi
  fi
  make -j"$jobs" V=1
} 2>&1 | tee "$log"

if [[ "$cfg" == python ]]; then
  zlib_archive=$ROOT/staging/libapps.a
  zlib_audit_archive=$art/zlib-link-input-libapps.a
  zlib_overlay_audit=$art/zlib-overlay-audit.txt
  [[ -s "$zlib_archive" &&
     -f "$ROOT/tools/p2/check-zlib-overlay.py" ]] ||
    { echo "ERROR: P2 Python zlib overlay audit inputs are missing" >&2;
      exit 1; }
  cp "$zlib_archive" "$zlib_audit_archive"
  p2_overlay_slot_start=$(
    "$P2LLVM_ROOT/bin/llvm-nm" --defined-only "$ROOT/nuttx" |
      LC_ALL=C awk -v symbol=__p2_overlay_slot_start '
        $NF == symbol { value = $1; count++ }
        END {
          if (count != 1 || value !~ /^[0-9A-Fa-f]+$/)
            exit 1
          print "0x" value
        }
      '
  ) ||
    { echo "ERROR: linked P2 overlay slot start is missing or ambiguous" >&2;
      exit 1; }
  p2_overlay_slot_end=$(
    "$P2LLVM_ROOT/bin/llvm-nm" --defined-only "$ROOT/nuttx" |
      LC_ALL=C awk -v symbol=__p2_overlay_slot_end '
        $NF == symbol { value = $1; count++ }
        END {
          if (count != 1 || value !~ /^[0-9A-Fa-f]+$/)
            exit 1
          print "0x" value
        }
      '
  ) ||
    { echo "ERROR: linked P2 overlay slot end is missing or ambiguous" >&2;
      exit 1; }
  if ! "$python" "$ROOT/tools/p2/check-zlib-overlay.py" \
       --map "$ROOT/nuttx.map" \
       --archive "$zlib_audit_archive" \
       --map-archive "$zlib_archive" \
       --slot-start "$p2_overlay_slot_start" \
       --slot-end "$p2_overlay_slot_end" \
       --xmem-start 0x10000000 \
       --xmem-end 0x12000000 2>&1 | tee "$zlib_overlay_audit"; then
    echo "ERROR: zlib code or data escaped the P2 Python overlay container" >&2
    exit 1
  fi

  builtin_list_objects=("$apps"/builtin/builtin_list.c*.o)
  [[ -e "${builtin_list_objects[0]}" ]] ||
    { echo "ERROR: generated builtin command table object is missing" >&2;
      exit 1; }
  for object in "${builtin_list_objects[@]}"; do
    if "$P2LLVM_ROOT/bin/llvm-readelf" --sections --wide "$object" |
         LC_ALL=C awk '
           /\.p2\.(xdata|xbss)/ { found = 1 }
           END { exit !found }
         '; then
      echo "ERROR: builtin command table was externalized before the Python runtime can initialize PSRAM: $object" >&2
      exit 1
    fi
    "$P2LLVM_ROOT/bin/llvm-nm" "$object" |
      LC_ALL=C awk '
        $1 == "U" && $NF == "python_main" { found = 1 }
        END { exit !found }
      ' ||
      { echo "ERROR: builtin command table does not register python_main: $object" >&2;
        exit 1; }
  done

  python_version=$(sed -n \
    's/^CONFIG_INTERPRETERS_CPYTHON_VERSION="\(.*\)"$/\1/p' "$ROOT/.config")
  python_minor=${python_version%.*}
  python_setup=$apps/interpreters/python/Setup.local
  python_target_makefile=$apps/interpreters/python/build/target/Makefile
  python_config=$apps/interpreters/python/build/target/Modules/config.c
  python_archive=$apps/interpreters/python/install/target/libpython${python_minor}.a
  [[ "$python_minor" =~ ^[0-9]+\.[0-9]+$ && -s "$python_setup" &&
     -s "$python_target_makefile" && -s "$python_config" &&
     -s "$python_archive" ]] ||
    { echo "ERROR: CPython module contract inputs are missing" >&2; exit 1; }
  python_setup_active=$(LC_ALL=C awk '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    $1 == "*disabled*" { found = 1; exit }
    { print }
    END { if (!found) exit 1 }
  ' "$python_setup") ||
    { echo "ERROR: CPython Setup.local lacks the disabled-module marker" >&2;
      exit 1; }
  [[ "$python_setup_active" == 'zlib zlibmodule.c' ]] ||
    { echo "ERROR: P2 CPython active builtin set must bootstrap only zlib" >&2;
      exit 1; }
  LC_ALL=C grep -Fqx '_thread' "$python_setup" ||
    { echo "ERROR: P2 CPython must explicitly disable _thread" >&2; exit 1; }
  LC_ALL=C grep -Fqx '_interpreters' "$python_setup" ||
    { echo "ERROR: P2 CPython must explicitly disable subinterpreters" >&2;
      exit 1; }
  if LC_ALL=C grep -Fq 'PyInit__thread' "$python_config" ||
     "$P2LLVM_ROOT/bin/llvm-nm" --defined-only "$python_archive" |
       LC_ALL=C awk '
         $NF == "PyInit__thread" { found = 1 }
         END { exit !found }
       '; then
    echo "ERROR: P2 CPython unexpectedly exposes task-spawning _thread" >&2
    exit 1
  fi
  if "$P2LLVM_ROOT/bin/llvm-ar" t "$python_archive" |
       LC_ALL=C awk '
         /(^|\/)_threadmodule\.o$/ { found = 1 }
         END { exit !found }
       '; then
    echo "ERROR: P2 CPython unexpectedly archives native _threadmodule.o" >&2
    exit 1
  fi
  if LC_ALL=C grep -Fq 'PyInit__interpreters' "$python_config" ||
     "$P2LLVM_ROOT/bin/llvm-nm" --defined-only "$python_archive" |
       LC_ALL=C awk '
         $NF == "PyInit__interpreters" { found = 1 }
         END { exit !found }
       '; then
    echo "ERROR: P2 CPython unexpectedly exposes subinterpreters" >&2
    exit 1
  fi
  # The cross-configure probe cannot link against NuttX's apps archive, so
  # MODULE_ZLIB_STATE remains "missing" even when Setup.local deliberately
  # promotes zlib into the static builtin set.  Trust the generated makesetup
  # outputs and the archive/final-link symbols below instead of that probe
  # result: MODBUILT_NAMES is the authoritative build-plan declaration.
  LC_ALL=C awk '
    /^MODBUILT_NAMES=/ {
      for (field = 2; field <= NF; field++)
        if ($field == "zlib")
          found = 1
    }
    END { exit !found }
  ' "$python_target_makefile" ||
    { echo "ERROR: compressed P2 stdlib requires builtin CPython zlib" >&2;
      exit 1; }
  LC_ALL=C grep -Fq 'PyInit_zlib' "$python_config" ||
    { echo "ERROR: CPython builtin table does not register zlib" >&2;
      exit 1; }
  "$P2LLVM_ROOT/bin/llvm-nm" --defined-only "$python_archive" |
    LC_ALL=C awk '
      $NF == "PyInit_zlib" { found = 1 }
      END { exit !found }
    ' ||
    { echo "ERROR: CPython archive does not define PyInit_zlib" >&2;
      exit 1; }
  LC_ALL=C grep -Eq '^prefix=[[:space:]]*/usr/local[[:space:]]*$' \
    "$python_target_makefile" ||
    { echo "ERROR: CPython target prefix is not /usr/local" >&2; exit 1; }
  if LC_ALL=C grep -Fq "$apps/interpreters/python/install/target" \
       "$python_target_makefile"; then
    echo "ERROR: CPython target configuration embeds its host staging prefix" >&2
    exit 1
  fi
fi

input_relocs=$art/input-relocations.txt
unsafe_relocs=$art/unsafe-relocations.txt
relocation_inputs=(staging/*.a arch/p2/src/p2_head.o
                   arch/p2/src/board/libboard.a)
if [[ "$cfg" == python ]]; then
  python_version=$(sed -n \
    's/^CONFIG_INTERPRETERS_CPYTHON_VERSION="\(.*\)"$/\1/p' "$ROOT/.config")
  python_minor=${python_version%.*}
  [[ "$python_minor" =~ ^[0-9]+\.[0-9]+$ ]] ||
    { echo "ERROR: invalid configured CPython version '$python_version'" >&2;
      exit 1; }
  relocation_inputs+=(
    "$apps/interpreters/python/install/target/libpython${python_minor}.a"
    "$apps/interpreters/python/build/target/Modules/_hacl/libHacl_Hash_SHA2.a"
    "$apps/interpreters/python/build/target/Modules/expat/libexpat.a"
  )
fi
"$P2LLVM_ROOT/bin/llvm-objdump" -r "${relocation_inputs[@]}" \
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

if [[ "$cfg" == python ]]; then
  python_romfs=$apps/interpreters/python/romfs_cpython_modules.img
  python_stdlib_zip=$apps/interpreters/python/build/target/lib/python$(echo "$python_minor" | tr -d .).zip
  python_package_dir=$ROOT/p2-python-package
  python_manifest=$python_package_dir/manifest.json
  python_payloads=$python_package_dir/payloads
  python_container=$ROOT/nuttx.p2py
  python_residency_audit=$art/python-residency-audit.txt
  python_slot_size=$(sed -n \
    's/^CONFIG_P2_HUB_OVERLAY_SLOT_SIZE=//p' "$ROOT/.config")
  python_reserve_size=$(sed -n \
    's/^CONFIG_P2_EC32MB_PSRAM_UNIFIED_RESERVE_SIZE=//p' "$ROOT/.config")

  [[ -s "$python_romfs" && -s "$python_stdlib_zip" ]] ||
    { echo "ERROR: external CPython stdlib package is missing" >&2; exit 1; }
  "$python" - "$python_stdlib_zip" \
    "$apps/interpreters/python/install/target" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive:
    for member in ("encodings/__init__.pyc", "_thread.pyc", "_pyio.pyc"):
        info = archive.getinfo(member)
        if info.compress_type != zipfile.ZIP_DEFLATED:
            raise SystemExit(
                "ERROR: P2 stdlib member {} is not DEFLATE-compressed".format(
                    member
                )
            )
    if "_thread.py" in archive.namelist():
        raise SystemExit("ERROR: P2 stdlib must package only compiled _thread.pyc")
    sysconfig_members = [
        member for member in archive.namelist()
        if "_sysconfigdata_" in member and member.endswith(".pyc")
    ]
    if len(sysconfig_members) != 1:
        raise SystemExit("ERROR: P2 stdlib has an invalid sysconfig payload")
    sysconfig = archive.read(sysconfig_members[0])
    if sys.argv[2].encode() in sysconfig:
        raise SystemExit("ERROR: packaged sysconfig embeds host staging paths")
    if b"/usr/local" not in sysconfig:
        raise SystemExit("ERROR: packaged sysconfig lacks target /usr/local prefix")
PY
  [[ "$python_slot_size" =~ ^[0-9]+$ &&
     "$python_reserve_size" =~ ^[0-9]+$ ]] ||
    { echo "ERROR: invalid P2 Python slot/reserve configuration" >&2;
      exit 1; }
  [[ -s "$ROOT/p2-overlay.ld" ]] ||
    { echo "ERROR: generated P2 overlay linker fragment is missing" >&2;
      exit 1; }
  "$python" "$ROOT/tools/p2/p2_python_package.py" \
    --elf "$ROOT/nuttx" \
    --full-elf "$ROOT/nuttx.full" \
    --resident-elf "$ROOT/nuttx.resident" \
    --romfs "$python_romfs" \
    --manifest "$python_manifest" \
    --payload-dir "$python_payloads" \
    --container "$python_container" \
    --objcopy "$P2LLVM_ROOT/bin/llvm-objcopy" \
    --slot-size "$python_slot_size" \
    --reserve-size "$python_reserve_size" \
    --backing-address 0x10300000
  mv -f "$ROOT/nuttx.resident" "$ROOT/nuttx"
  if ! "$python" "$ROOT/tools/p2/verify-python-residency.py" \
       "$ROOT/nuttx.full" 2>&1 | tee "$python_residency_audit"; then
    echo "ERROR: packaged P2 Python control/telemetry path is not resident" >&2
    exit 1
  fi
  "$P2LLVM_ROOT/bin/llvm-nm" --defined-only "$ROOT/nuttx.full" |
    LC_ALL=C awk '
      $NF == "PyInit_zlib" { found = 1 }
      END { exit !found }
    ' ||
    { echo "ERROR: packaged P2 CPython does not link builtin zlib" >&2;
      exit 1; }
  if "$P2LLVM_ROOT/bin/llvm-nm" --defined-only "$ROOT/nuttx.full" |
       LC_ALL=C awk '
         $NF == "PyInit__thread" { found = 1 }
         END { exit !found }
       '; then
    echo "ERROR: packaged P2 CPython unexpectedly links _thread" >&2
    exit 1
  fi
  if "$P2LLVM_ROOT/bin/llvm-nm" --defined-only "$ROOT/nuttx.full" |
       LC_ALL=C awk '
         $NF == "PyInit__interpreters" { found = 1 }
         END { exit !found }
       '; then
    echo "ERROR: packaged P2 CPython unexpectedly links subinterpreters" >&2
    exit 1
  fi
  "$P2LLVM_ROOT/bin/llvm-objdump" --disassemble-symbols=p2_rng_read \
    "$ROOT/nuttx.full" |
    LC_ALL=C awk '
      /[[:space:]]getrnd[[:space:]]/ { hardware = 1 }
      /blake2s/ { conditioner = 1 }
      END { exit !(hardware && conditioner) }
    ' ||
    { echo "ERROR: packaged P2 Python image lacks conditioned hardware GETRND" >&2;
      exit 1; }
fi

[[ -s nuttx ]] || { echo "ERROR: NuttX ELF is missing or empty" >&2; exit 1; }
[[ -s nuttx.map ]] || { echo "ERROR: nuttx.map is missing or empty" >&2; exit 1; }
[[ -s System.map ]] || { echo "ERROR: System.map is missing or empty" >&2; exit 1; }
[[ -x ./tools/p2/verify-elf.py ]] ||
  { echo "ERROR: tools/p2/verify-elf.py is missing or not executable" >&2; exit 1; }

cp nuttx nuttx.map System.map "$art/"
if [[ "$cfg" == python ]]; then
  cp nuttx.full nuttx.p2py p2-overlay.ld "$python_manifest" "$art/"
  "$P2LLVM_ROOT/bin/llvm-readelf" -h -l -S nuttx.full > "$art/full-elf.txt"
  "$python" ./tools/p2/p2_python_container.py list nuttx.p2py \
    > "$art/python-container.json"
fi
"$P2LLVM_ROOT/bin/llvm-objcopy" -O binary nuttx nuttx.bin
./tools/p2/report-memory.sh nuttx.map nuttx.bin System.map |
  tee "$art/memory.txt"

if [[ $unified_profile -eq 1 ]]; then
  unified_symbols=$art/unified-symbols.txt
  "$P2LLVM_ROOT/bin/llvm-nm" --defined-only nuttx > "$unified_symbols"
  for helper in \
    __p2_xmem_load8 __p2_xmem_load16 __p2_xmem_load32 __p2_xmem_load64 \
    __p2_xmem_store8 __p2_xmem_store16 __p2_xmem_store32 __p2_xmem_store64 \
    __p2_xmem_memcpy __p2_xmem_memmove __p2_xmem_memset
  do
    LC_ALL=C grep -Eq "[[:space:]]${helper}$" "$unified_symbols" ||
      { echo "ERROR: $target is missing required runtime helper $helper" >&2;
        exit 1; }
  done

  for legacy_symbol in \
    p2_psram_read p2_psram_write p2_psram_seek g_p2_psram_fops
  do
    if LC_ALL=C grep -Eq "[[:space:]]${legacy_symbol}$" \
         "$unified_symbols"; then
      echo "ERROR: $target contains legacy PSRAM driver symbol $legacy_symbol" >&2
      exit 1
    fi
  done

  # The HIL-only image deliberately contains this path as a negative stat()
  # probe.  Its NODEV result is runtime evidence; the symbol exclusions above
  # prove at link time that the character-driver implementation is absent.

  if [[ "$cfg" == unified ]] &&
     LC_ALL=C grep -aFq '/dev/psram0' nuttx.bin; then
    echo "ERROR: $target image contains the legacy /dev/psram0 interface" >&2
    exit 1
  fi
fi

if [[ "$cfg" == "flashboot" || "$cfg" == "showcase" || "$cfg" == "base" ]] &&
   ! LC_ALL=C grep -aFq \
     'P2FLASHBOOT:SMARTFS=/dev/smart0@/mnt/flash:MOUNTED:AUTOFORMAT=NO:DESTRUCTIVE_HANDLERS=ABSENT' \
     nuttx.bin; then
  echo "ERROR: $target flashboot image does not contain the startup mount marker" >&2
  exit 1
fi

if [[ "$cfg" == "base" ]]; then
  sd_boot_max=491516
  minimum_heap=81920
  image_bytes=$(wc -c < nuttx.bin | tr -d '[:space:]')
  heap_bytes=$(sed -n \
    's/^P2MEM:HEAP=.*:BYTES=\([0-9][0-9]*\):.*/\1/p' \
    "$art/memory.txt")

  if (( image_bytes > sd_boot_max )); then
    echo "ERROR: $target image is $image_bytes bytes; serial SD writer limit is $sd_boot_max" >&2
    exit 1
  fi
  if [[ ! "$heap_bytes" =~ ^[0-9]+$ ]] || (( heap_bytes < minimum_heap )); then
    echo "ERROR: $target leaves ${heap_bytes:-unknown} Hub heap bytes; base minimum is $minimum_heap" >&2
    exit 1
  fi
  if ! LC_ALL=C grep -aFq \
       "P2BASE:READY:BOARD=$board:APPS=berry,vi" nuttx.bin; then
    echo "ERROR: $target image does not contain its board-specific base marker" >&2
    exit 1
  fi

  for required in \
    CONFIG_INTERPRETERS_BERRY=y \
    CONFIG_NSH_CLE=y \
    CONFIG_SYSTEM_CLE=y \
    CONFIG_SYSTEM_VI=y \
    CONFIG_P2_EC32MB_SDCARD_AUTOMOUNT=y
  do
    LC_ALL=C grep -Fqx "$required" .config ||
      { echo "ERROR: $target is missing $required" >&2; exit 1; }
  done

  for forbidden in \
    CONFIG_ELF=y \
    CONFIG_MODULES=y \
    CONFIG_NSH_FILE_APPS=y \
    CONFIG_NSH_READLINE=y \
    CONFIG_GRAPHICS_LVGL=y \
    CONFIG_LCD=y \
    CONFIG_VIDEO=y \
    CONFIG_INPUT=y \
    CONFIG_INTERPRETERS_BERRY_LVGL=y
  do
    if LC_ALL=C grep -Fqx "$forbidden" .config; then
      echo "ERROR: $target unexpectedly enables $forbidden" >&2
      exit 1
    fi
  done
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
"$python" ./tools/p2/verify-elf.py nuttx | tee "$art/verify-elf.txt"

echo "P2 build artifact: $art"
