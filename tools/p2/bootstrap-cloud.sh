#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
CACHE=${P2_CACHE:-$HOME/.cache/p2-nuttx}
APPS_DIR=${NUTTX_APPS_DIR:-$ROOT/../apps}
P2LLVM_SRC=${P2LLVM_SRC:-$CACHE/p2llvm-src}
P2LLVM_ROOT=${P2LLVM_ROOT:-$CACHE/p2llvm/install}
if [[ ! -x "$P2LLVM_ROOT/bin/clang" && -x "$CACHE/p2llvm/install/bin/clang" ]]; then P2LLVM_ROOT="$CACHE/p2llvm/install"; fi
PYTHON=${PYTHON:-python3}
P2LLVM_REF=${P2LLVM_REF:-bdcefcce7860b2232c06f35726fea679a3a7309c}
LLVM_PROJECT_REF=${LLVM_PROJECT_REF:-72a9bb1ef2656d9953d1f41a8196d425ff2ab0b1}
P2LLVM_PREEMPT_PATCH=$ROOT/tools/p2/patches/p2llvm-preempt-safe-integer.patch
P2LLVM_UNIFIED_PATCH=$ROOT/tools/p2/patches/p2llvm-unified-memory.patch
P2LLVM_RUNTIME_PATCH=$ROOT/tools/p2/patches/p2llvm-python-runtime.patch
P2LLVM_RUNTIME_TOOL=$ROOT/tools/p2/p2llvm-runtime.py
P2LLVM_PATCHES=("$P2LLVM_PREEMPT_PATCH" "$P2LLVM_UNIFIED_PATCH")
LOADP2_SRC=${LOADP2_SRC:-$CACHE/loadp2-src}
LOADP2_ROOT=${LOADP2_ROOT:-$CACHE/loadp2}
RUNTIME_LOCK=${P2_TOOLCHAIN_LOCK:-$CACHE/toolchain.lock}
JOBS=${JOBS:-$(nproc 2>/dev/null || echo 2)}

mkdir -p "$CACHE" "$P2LLVM_ROOT" "$LOADP2_ROOT" "$ROOT/artifacts/cloud-p2"

die() { echo "ERROR: $*" >&2; exit 1; }
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

ensure_p2llvm_checkout() {
  local expected_url=https://github.com/ne75/p2llvm
  local actual_url outer_head gitlink_head llvm_head

  if [[ ! -d "$P2LLVM_SRC/.git" ]]; then
    clone_if_missing "$expected_url" "$P2LLVM_SRC" "$P2LLVM_REF"
  fi

  actual_url=$(git -C "$P2LLVM_SRC" remote get-url origin) ||
    die "p2llvm checkout has no origin remote: $P2LLVM_SRC"
  [[ "${actual_url%.git}" == "${expected_url%.git}" ]] ||
    die "p2llvm origin is $actual_url; expected $expected_url"

  outer_head=$(git -C "$P2LLVM_SRC" rev-parse HEAD) ||
    die "could not identify p2llvm HEAD"
  [[ "$outer_head" == "$P2LLVM_REF" ]] ||
    die "p2llvm is at $outer_head; expected exact commit $P2LLVM_REF"

  gitlink_head=$(git -C "$P2LLVM_SRC" ls-tree HEAD llvm-project |
    awk '$2 == "commit" { print $3 }')
  [[ "$gitlink_head" == "$LLVM_PROJECT_REF" ]] ||
    die "p2llvm llvm-project gitlink is $gitlink_head; expected $LLVM_PROJECT_REF"

  if ! git -C "$P2LLVM_SRC/llvm-project" rev-parse --git-dir \
       >/dev/null 2>&1; then
    git -C "$P2LLVM_SRC" submodule update --init --recursive llvm-project
  fi

  llvm_head=$(git -C "$P2LLVM_SRC/llvm-project" rev-parse HEAD) ||
    die "could not identify p2llvm llvm-project HEAD"
  [[ "$llvm_head" == "$LLVM_PROJECT_REF" ]] ||
    die "p2llvm llvm-project is at $llvm_head; expected $LLVM_PROJECT_REF; refusing to overwrite it"
}

apply_p2llvm_outer_patch() {
  "$PYTHON" "$P2LLVM_RUNTIME_TOOL" apply-outer \
    --source "$P2LLVM_SRC" --patch "$P2LLVM_RUNTIME_PATCH" \
    --ref "$P2LLVM_REF" ||
    die "p2llvm outer source does not match the exact runtime patch state"
}

verify_p2llvm_runtime_source() {
  "$PYTHON" "$P2LLVM_RUNTIME_TOOL" verify-source \
    --source "$P2LLVM_SRC" --patch "$P2LLVM_RUNTIME_PATCH" \
    --ref "$P2LLVM_REF"
}

verify_p2llvm_runtime_archive() {
  "$PYTHON" "$P2LLVM_RUNTIME_TOOL" verify-archive \
    --toolchain-root "$P2LLVM_ROOT"
}

prepare_p2llvm_expected_index() {
  local llvm_dir=$P2LLVM_SRC/llvm-project
  local expected_index=$1
  local expected_objects=$2
  local patch_count=${3:-${#P2LLVM_PATCHES[@]}}
  local expected_paths=$4
  local patch_index=0
  local patch_paths source_objects entry metadata mode type hash path path_error
  local patch

  rm -f "$expected_index" "$expected_index.lock"
  : > "$expected_paths"
  patch_paths=$(mktemp "$CACHE/p2llvm-patch-paths.XXXXXX") || return 1
  source_objects=$(git -C "$llvm_dir" rev-parse --git-path objects) ||
    { rm -f "$patch_paths"; return 1; }
  GIT_INDEX_FILE="$expected_index" \
    GIT_OBJECT_DIRECTORY="$expected_objects" \
    GIT_ALTERNATE_OBJECT_DIRECTORIES="$source_objects" \
    git -C "$llvm_dir" read-tree --empty ||
    { rm -f "$patch_paths"; return 1; }
  for patch in "${P2LLVM_PATCHES[@]}"; do
    (( patch_index < patch_count )) || break
    [[ -f "$patch" ]] || { rm -f "$patch_paths"; return 1; }
    git -C "$llvm_dir" apply --numstat --unidiff-zero "$patch" \
      > "$patch_paths" || { rm -f "$patch_paths"; return 1; }
    path_error=0
    while IFS=$'\t' read -r _ _ path; do
      if ! grep -Fqx -- "$path" "$expected_paths"; then
        printf '%s\n' "$path" >> "$expected_paths"
        entry=$(git -C "$llvm_dir" ls-tree HEAD -- "$path")
        if [[ -n "$entry" ]]; then
          metadata=${entry%%$'\t'*}
          read -r mode type hash <<< "$metadata"
          if [[ "$type" != blob ]] ||
             ! GIT_INDEX_FILE="$expected_index" \
               GIT_OBJECT_DIRECTORY="$expected_objects" \
               GIT_ALTERNATE_OBJECT_DIRECTORIES="$source_objects" \
               git -C "$llvm_dir" update-index --add \
                 --cacheinfo "$mode,$hash,$path";
          then
            path_error=1
            break
          fi
        fi
      fi
    done < "$patch_paths"
    if (( path_error != 0 )); then
      rm -f "$patch_paths"
      return 1
    fi
    GIT_INDEX_FILE="$expected_index" \
      GIT_OBJECT_DIRECTORY="$expected_objects" \
      GIT_ALTERNATE_OBJECT_DIRECTORIES="$source_objects" \
      git -C "$llvm_dir" apply \
      --cached --unidiff-zero --check "$patch" ||
      { rm -f "$patch_paths"; return 1; }
    GIT_INDEX_FILE="$expected_index" \
      GIT_OBJECT_DIRECTORY="$expected_objects" \
      GIT_ALTERNATE_OBJECT_DIRECTORIES="$source_objects" \
      git -C "$llvm_dir" apply \
      --cached --unidiff-zero "$patch" ||
      { rm -f "$patch_paths"; return 1; }
    (( patch_index += 1 ))
  done
  rm -f "$patch_paths"
}

p2llvm_patch_prefix_state_valid() {
  local llvm_dir=$P2LLVM_SRC/llvm-project
  local patch_count=$1
  local expected_index expected_objects allowed_additions source_objects path
  local actual_changes expected_changes
  local expected_paths=()
  local unexpected_change=0

  expected_index=$(mktemp "$CACHE/p2llvm-expected-index.XXXXXX") || return 1
  allowed_additions=$(mktemp "$CACHE/p2llvm-expected-additions.XXXXXX") ||
    { rm -f "$expected_index"; return 1; }
  expected_objects=$(mktemp -d "$CACHE/p2llvm-expected-objects.XXXXXX") ||
    { rm -f "$expected_index" "$allowed_additions"; return 1; }
  actual_changes=$(mktemp "$CACHE/p2llvm-actual-changes.XXXXXX") ||
    { rm -f "$expected_index" "$allowed_additions";
      rm -rf -- "$expected_objects"; return 1; }
  expected_changes=$(mktemp "$CACHE/p2llvm-expected-changes.XXXXXX") ||
    { rm -f "$expected_index" "$allowed_additions" "$actual_changes";
      rm -rf -- "$expected_objects"; return 1; }
  source_objects=$(git -C "$llvm_dir" rev-parse --git-path objects) ||
    { rm -f "$expected_index" "$allowed_additions" "$actual_changes" \
        "$expected_changes";
      rm -rf -- "$expected_objects"; return 1; }
  if ! prepare_p2llvm_expected_index "$expected_index" "$expected_objects" \
       "$patch_count" "$expected_changes"; then
    rm -f "$expected_index" "$expected_index.lock" "$allowed_additions" \
      "$actual_changes" "$expected_changes"
    rm -rf -- "$expected_objects"
    return 1
  fi

  git -C "$llvm_dir" diff --name-only --ignore-submodules=dirty -- \
    > "$actual_changes"
  while IFS= read -r path; do
    if ! grep -Fqx -- "$path" "$expected_changes"; then
      unexpected_change=1
      break
    fi
  done < "$actual_changes"
  if (( unexpected_change != 0 )); then
    rm -f "$expected_index" "$expected_index.lock" \
      "$allowed_additions" "$actual_changes" "$expected_changes"
    rm -rf -- "$expected_objects"
    return 1
  fi
  while IFS= read -r path; do
    expected_paths[${#expected_paths[@]}]=$path
  done < "$expected_changes"

  if (( ${#expected_paths[@]} != 0 )) &&
     ! GIT_INDEX_FILE="$expected_index" \
       GIT_OBJECT_DIRECTORY="$expected_objects" \
       GIT_ALTERNATE_OBJECT_DIRECTORIES="$source_objects" \
       git -C "$llvm_dir" diff \
       --quiet --ignore-submodules=dirty -- "${expected_paths[@]}" ||
     ! git -C "$llvm_dir" diff --cached --quiet \
       --ignore-submodules=dirty --;
  then
    rm -f "$expected_index" "$expected_index.lock" "$allowed_additions" \
      "$actual_changes" "$expected_changes"
    rm -rf -- "$expected_objects"
    return 1
  fi

  : > "$allowed_additions"
  if (( ${#expected_paths[@]} != 0 )); then
    GIT_INDEX_FILE="$expected_index" \
      GIT_OBJECT_DIRECTORY="$expected_objects" \
      GIT_ALTERNATE_OBJECT_DIRECTORIES="$source_objects" \
      git -C "$llvm_dir" diff --cached \
      --name-only --diff-filter=A HEAD -- "${expected_paths[@]}" \
      > "$allowed_additions"
  fi
  while IFS= read -r path; do
    if ! grep -Fqx -- "$path" "$allowed_additions"; then
      rm -f "$expected_index" "$expected_index.lock" \
        "$allowed_additions" "$actual_changes" "$expected_changes"
      rm -rf -- "$expected_objects"
      return 1
    fi
  done < <(git -C "$llvm_dir" ls-files --others --exclude-standard)

  rm -f "$expected_index" "$expected_index.lock" "$allowed_additions" \
    "$actual_changes" "$expected_changes"
  rm -rf -- "$expected_objects"
}

p2llvm_patch_state_valid() {
  p2llvm_patch_prefix_state_valid "${#P2LLVM_PATCHES[@]}"
}

apply_p2llvm_patches() {
  local llvm_dir=$P2LLVM_SRC/llvm-project
  local patch patch_index prefix

  for patch in "${P2LLVM_PATCHES[@]}"; do
    [[ -f "$patch" ]] || die "required p2llvm patch is missing: $patch"
  done
  p2llvm_patch_state_valid && return

  for ((prefix=${#P2LLVM_PATCHES[@]} - 1; prefix >= 0; prefix--)); do
    if p2llvm_patch_prefix_state_valid "$prefix"; then
      for ((patch_index=prefix;
           patch_index < ${#P2LLVM_PATCHES[@]};
           patch_index++)); do
        patch=${P2LLVM_PATCHES[$patch_index]}
        git -C "$llvm_dir" apply --unidiff-zero "$patch"
        echo "Applied $(basename "$patch")"
      done
      p2llvm_patch_state_valid ||
        die "p2llvm source does not exactly match the required patch series"
      return
    fi
  done

  die "p2llvm llvm-project is not an exact patch-series prefix; refusing to overwrite"
}

if [[ ${P2_BOOTSTRAP_FETCH:-1} == 1 ]]; then
  [[ -d "$APPS_DIR/.git" ]] || clone_if_missing https://github.com/apache/nuttx-apps "$APPS_DIR" "${NUTTX_APPS_REF:-master}"
  [[ -d "$LOADP2_SRC/.git" ]] || clone_if_missing https://github.com/totalspectrum/loadp2 "$LOADP2_SRC" "${LOADP2_REF:-master}"
fi

if [[ -d "$P2LLVM_SRC/.git" || ${P2_BOOTSTRAP_FETCH:-1} == 1 ]]; then
  ensure_p2llvm_checkout
  apply_p2llvm_outer_patch
  apply_p2llvm_patches
fi

if [[ ${P2_BOOTSTRAP_BUILD:-0} == 1 && -d "$P2LLVM_SRC/.git" ]]; then
  cmake -S "$P2LLVM_SRC/llvm-project/llvm" -B "$P2LLVM_SRC/build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$P2LLVM_ROOT" \
    -DLLVM_TARGETS_TO_BUILD= \
    -DLLVM_EXPERIMENTAL_TARGETS_TO_BUILD=P2 \
    -DLLVM_ENABLE_PROJECTS='clang;lld' \
    -DLLVM_ENABLE_ZLIB=OFF -DLLVM_ENABLE_LIBXML2=OFF
  cmake --build "$P2LLVM_SRC/build" --target install -j"$JOBS"
  (
    cd "$P2LLVM_SRC"
    "$PYTHON" build.py --configure --skip_llvm --skip_libc \
      --install "$P2LLVM_ROOT"
  )
fi

unified_codegen_status=BLOCKED_not_available
runtime_archive_status=BLOCKED_not_available
if [[ -x "$P2LLVM_ROOT/bin/clang" ]]; then
  [[ -x "$P2LLVM_ROOT/bin/llc" ]] ||
    die "p2llvm install is missing llc required by the unified-memory verifier"
  "$PYTHON" "$ROOT/tools/p2/check-unified-memory-codegen.py" \
    --clang "$P2LLVM_ROOT/bin/clang" \
    --llc "$P2LLVM_ROOT/bin/llc" ||
    die "p2llvm install does not satisfy the unified-memory codegen contract"
  unified_codegen_status=verified
  verify_p2llvm_runtime_archive ||
    die "p2llvm install lacks the verified standalone compiler builtins"
  runtime_archive_status=verified
fi

if [[ ${P2_BOOTSTRAP_BUILD:-0} == 1 && -d "$LOADP2_SRC/.git" ]]; then
  make -C "$LOADP2_SRC" -j"$JOBS"
  install -D "$LOADP2_SRC/loadp2" "$LOADP2_ROOT/bin/loadp2"
fi

write_unified_lock() {
  local lock=$RUNTIME_LOCK
  local tmp=$lock.tmp
  local apps_commit nuttx_commit p2llvm_commit llvm_commit
  local file digest
  local files=(
    "$P2LLVM_ROOT/bin/clang"
    "$P2LLVM_ROOT/bin/clang++"
    "$P2LLVM_ROOT/bin/ld.lld"
    "$P2LLVM_ROOT/bin/llc"
    "$P2LLVM_ROOT/bin/llvm-ar"
    "$P2LLVM_ROOT/bin/llvm-nm"
    "$P2LLVM_ROOT/bin/llvm-objcopy"
    "$P2LLVM_ROOT/bin/llvm-objdump"
    "$P2LLVM_ROOT/bin/llvm-readelf"
    "$P2LLVM_ROOT/bin/llvm-readobj"
    "$P2LLVM_ROOT/bin/llvm-size"
    "$P2LLVM_ROOT/bin/llvm-strip"
    "$P2LLVM_ROOT/libp2/lib/libcompiler_builtins.a"
    "$P2LLVM_RUNTIME_PATCH"
    "$P2LLVM_PREEMPT_PATCH"
    "$P2LLVM_UNIFIED_PATCH"
  )

  nuttx_commit=$(git -C "$ROOT" rev-parse HEAD) ||
    die "cannot identify the NuttX commit for the unified lock"
  apps_commit=$(git -C "$APPS_DIR" rev-parse HEAD) ||
    die "cannot identify the NuttX apps commit for the unified lock"
  p2llvm_commit=$(git -C "$P2LLVM_SRC" rev-parse HEAD) ||
    die "cannot identify the p2llvm commit for the unified lock"
  llvm_commit=$(git -C "$P2LLVM_SRC/llvm-project" rev-parse HEAD) ||
    die "cannot identify the llvm-project commit for the unified lock"

  [[ "$p2llvm_commit" == "$P2LLVM_REF" ]] ||
    die "refusing to lock unexpected p2llvm commit $p2llvm_commit"
  [[ "$llvm_commit" == "$LLVM_PROJECT_REF" ]] ||
    die "refusing to lock unexpected llvm-project commit $llvm_commit"
  verify_p2llvm_runtime_source ||
    die "refusing to lock p2llvm outer source outside the exact runtime patch state"
  p2llvm_patch_state_valid ||
    die "refusing to lock a compiler source that differs from the exact patch series"
  verify_p2llvm_runtime_archive ||
    die "refusing to lock an invalid standalone compiler builtins archive"

  for file in "${files[@]}"; do
    [[ -f "$file" ]] || die "unified lock input is missing: $file"
  done

  if [[ -x "$LOADP2_ROOT/bin/loadp2" ]]; then
    files+=("$LOADP2_ROOT/bin/loadp2")
  fi

  mkdir -p "$(dirname "$lock")"
  {
    echo "nuttx_commit=$nuttx_commit"
    echo "nuttx_apps_commit=$apps_commit"
    echo "p2llvm_commit=$p2llvm_commit"
    echo "p2llvm_llvm_project_commit=$llvm_commit"
    echo "compiler=$("$P2LLVM_ROOT/bin/clang" --version | head -1)"
    echo "linker=$("$P2LLVM_ROOT/bin/ld.lld" --version | head -1)"
    echo "llc=$("$P2LLVM_ROOT/bin/llc" --version | head -1)"
    echo "p2llvm_runtime_patch=$(basename "$P2LLVM_RUNTIME_PATCH")"
    echo "p2llvm_compiler_builtins=verified standalone archive with Python conversion helpers"
    echo "p2llvm_preempt_patch=$(basename "$P2LLVM_PREEMPT_PATCH")"
    echo "p2llvm_unified_patch=$(basename "$P2LLVM_UNIFIED_PATCH")"
    echo "p2llvm_unified_memory=verified opt-in lowering contract"
    echo "p2_flags=--target=p2 -fno-jump-tables -ffunction-sections -fdata-sections -fno-common -fno-builtin -nostdlib"
    echo "p2_unified_flags=-mllvm -p2-unified-memory"
    for file in "${files[@]}"; do
      digest=$("$PYTHON" -c \
        'import hashlib, sys; h = hashlib.sha256(); f = open(sys.argv[1], "rb"); [h.update(chunk) for chunk in iter(lambda: f.read(1048576), b"")]; print(h.hexdigest())' \
        "$file")
      printf 'sha256=%s  %s\n' "$digest" "$file"
    done
  } > "$tmp"
  mv "$tmp" "$lock"
  echo "Wrote exact unified toolchain lock $lock"
}

unified_lock_status=BLOCKED_not_available
if [[ "$unified_codegen_status" == verified ]]; then
  [[ -x "$P2LLVM_ROOT/bin/ld.lld" ]] ||
    die "p2llvm install is missing ld.lld required by unified builds"
  write_unified_lock
  unified_lock_status=$RUNTIME_LOCK
fi

{
  printf 'export NUTTX_APPS_DIR=%q\n' "$APPS_DIR"
  printf 'export P2LLVM_ROOT=%q\n' "$P2LLVM_ROOT"
  printf 'export P2_TOOLCHAIN_LOCK=%q\n' "$RUNTIME_LOCK"
  printf 'export LOADP2=%q\n' "$LOADP2_ROOT/bin/loadp2"
  printf 'export PATH=%q:%q:$%s\n' \
    "$P2LLVM_ROOT/bin" "$LOADP2_ROOT/bin" PATH
} > "$HOME/.p2-nuttx-env"

{
  echo "nuttx_commit=$(git -C "$ROOT" rev-parse HEAD)"
  echo "nuttx_upstream_base=$(git -C "$ROOT" merge-base HEAD origin/master 2>/dev/null || true)"
  echo "nuttx_apps_commit=$(git -C "$APPS_DIR" rev-parse HEAD 2>/dev/null || echo BLOCKED_not_available)"
  echo "p2llvm_commit=$(git -C "$P2LLVM_SRC" rev-parse HEAD 2>/dev/null || echo BLOCKED_not_available)"
  echo "p2llvm_llvm_project_commit=$(git -C "$P2LLVM_SRC/llvm-project" rev-parse HEAD 2>/dev/null || echo BLOCKED_not_available)"
  echo "p2llvm_runtime_patch=$(basename "$P2LLVM_RUNTIME_PATCH")"
  echo "p2llvm_compiler_builtins=$runtime_archive_status"
  echo "p2llvm_preempt_patch=$(basename "$P2LLVM_PREEMPT_PATCH")"
  echo "p2llvm_unified_patch=$(basename "$P2LLVM_UNIFIED_PATCH")"
  echo "p2llvm_unified_memory=$unified_codegen_status"
  echo "p2llvm_unified_toolchain_lock=$unified_lock_status"
  echo "loadp2_commit=$(git -C "$LOADP2_SRC" rev-parse HEAD 2>/dev/null || echo BLOCKED_not_available)"
  echo "host_os=$(uname -a)"
  echo "host_cc=$(${CC:-cc} --version 2>/dev/null | head -1 || true)"
  echo "cmake=$(cmake --version 2>/dev/null | head -1 || true)"
  echo "ninja=$(ninja --version 2>/dev/null || true)"
  echo "python=$(python3 --version 2>/dev/null || true)"
  echo "kconfig_conf=$(command -v kconfig-conf || echo BLOCKED_missing)"
  echo "clang=$([[ -x "$P2LLVM_ROOT/bin/clang" ]] && "$P2LLVM_ROOT/bin/clang" --version | head -1 || echo BLOCKED_missing)"
  echo "llc=$([[ -x "$P2LLVM_ROOT/bin/llc" ]] && "$P2LLVM_ROOT/bin/llc" --version | head -1 || echo BLOCKED_missing)"
  echo "loadp2=$([[ -x "$LOADP2_ROOT/bin/loadp2" ]] && "$LOADP2_ROOT/bin/loadp2" -h 2>&1 | head -1 || echo BLOCKED_missing)"
  echo "p2_flags=--target=p2 -fno-jump-tables -ffunction-sections -fdata-sections -fno-common -fno-builtin -nostdlib"
  echo "p2_unified_flags=-mllvm -p2-unified-memory"
} > "$ROOT/tools/p2/dependencies.lock"

cat "$ROOT/tools/p2/dependencies.lock"
echo "Wrote $HOME/.p2-nuttx-env"
