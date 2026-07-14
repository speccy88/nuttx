#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)

if [[ -f "$ROOT/.p2-hil.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.p2-hil.env"
  set +a
fi

CACHE=${P2_CACHE:-$HOME/.cache/p2-nuttx}
APPS_DIR=${NUTTX_APPS_DIR:-$ROOT/../apps}
P2LLVM_SRC=${P2LLVM_SRC:-$CACHE/p2llvm-src}
P2LLVM_ROOT=${P2LLVM_ROOT:-$CACHE/p2llvm/install}
FLEXPROP_ROOT=${FLEXPROP_ROOT:-$CACHE/flexprop-src}
PYTHON=${PYTHON:-python3}
PYTHON_VENV=${P2_PYTHON_VENV:-$CACHE/venv}
RUNTIME_LOCK=${P2_TOOLCHAIN_LOCK:-$CACHE/toolchain.lock}

APPS_URL=${P2_APPS_URL:-https://github.com/speccy88/nuttx-apps.git}
APPS_BRANCH=${P2_APPS_BRANCH:-codex/p2-hil-finish-apps}
APPS_REF=a333035462f545056e7a2fb859a9fbdc6d4ef831
P2LLVM_REF=bdcefcce7860b2232c06f35726fea679a3a7309c
LLVM_PROJECT_REF=72a9bb1ef2656d9953d1f41a8196d425ff2ab0b1
P2LLVM_LOADP2_REF=21e074cc7ee6fbd4fb12ef5352544b3457a6729c
P2LLVM_PATCH=$ROOT/tools/p2/patches/p2llvm-preempt-safe-integer.patch
FLEXPROP_REF=858f51c4a24e7ae0f6cbc78f625c731083ad304f
SPIN2CPP_REF=28f1b80fc3a36422fb0a1f7c54465d808634abc8
LOADP2_REF=c20afedd4253d09da449fa740f8d4304481fc560
KCONFIG_TOOLS_REF=9484147c12d051014f854852d59c21d75a9616bd

die()
{
  echo "ERROR: $*" >&2
  exit 1
}

need_command()
{
  command -v "$1" >/dev/null 2>&1 || die "required host command '$1' is missing"
}

host_jobs()
{
  if command -v sysctl >/dev/null 2>&1; then
    sysctl -n hw.ncpu 2>/dev/null && return
  fi

  if command -v getconf >/dev/null 2>&1; then
    getconf _NPROCESSORS_ONLN 2>/dev/null && return
  fi

  echo 4
}

git_head()
{
  git -C "$1" rev-parse HEAD
}

require_gitlink()
{
  local path=$1
  local expected=$2
  local actual

  actual=$(git -C "$path" rev-parse HEAD)
  [[ "$actual" == "$expected" ]] ||
    die "$path is at $actual; expected $expected"
}

ensure_checkout()
{
  local name=$1
  local url=$2
  local dir=$3
  local ref=$4
  local actual_url

  if [[ ! -d "$dir/.git" ]]; then
    mkdir -p "$(dirname "$dir")"
    git clone --filter=blob:none --no-checkout "$url" "$dir"
  fi

  actual_url=$(git -C "$dir" remote get-url origin)
  [[ "${actual_url%.git}" == "${url%.git}" ]] ||
    die "$name origin is $actual_url; expected $url"

  git -C "$dir" diff --quiet --ignore-submodules=dirty -- ||
    die "$name has tracked modifications in $dir"
  git -C "$dir" diff --cached --quiet --ignore-submodules=dirty -- ||
    die "$name has staged modifications in $dir"

  if ! git -C "$dir" cat-file -e "$ref^{commit}" 2>/dev/null; then
    git -C "$dir" fetch --filter=blob:none origin
  fi

  git -C "$dir" cat-file -e "$ref^{commit}" 2>/dev/null ||
    die "$name commit $ref is not available"

  if [[ "$(git_head "$dir")" != "$ref" ]]; then
    git -C "$dir" switch --detach "$ref"
  fi

  git -C "$dir" submodule update --init --recursive
  require_gitlink "$dir" "$ref"
}

ensure_apps_checkout()
{
  if [[ ! -d "$APPS_DIR/.git" ]]; then
    ensure_checkout nuttx-apps "$APPS_URL" "$APPS_DIR" "$APPS_REF"
    return
  fi

  if ! git -C "$APPS_DIR" cat-file -e "$APPS_REF^{commit}" 2>/dev/null; then
    git -C "$APPS_DIR" fetch --filter=blob:none "$APPS_URL" \
      "+refs/heads/$APPS_BRANCH:refs/remotes/p2-release/$APPS_BRANCH"
  fi

  git -C "$APPS_DIR" cat-file -e "$APPS_REF^{commit}" 2>/dev/null ||
    die "nuttx-apps commit $APPS_REF is not available"
  git -C "$APPS_DIR" merge-base --is-ancestor "$APPS_REF" HEAD ||
    die "nuttx-apps HEAD is not based on pinned commit $APPS_REF"

  # The apps tree is an active companion checkout during P2 bring-up.

  git -C "$APPS_DIR" submodule update --init --recursive
}

find_kconfig_conf()
{
  local candidate

  for candidate in \
    "${KCONFIG_CONF:-}" \
    "$(command -v kconfig-conf 2>/dev/null || true)" \
    "$HOME/.local/nuttx-tools/kconfig-frontends/bin/kconfig-conf" \
    "$HOME/.cache/nuttx-tools/kconfig-frontends/bin/kconfig-conf" \
    "$CACHE/kconfig-frontends/bin/kconfig-conf"
  do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      echo "$candidate"
      return
    fi
  done

  return 1
}

build_kconfig_conf()
{
  local src=$CACHE/kconfig-tools-src
  local prefix=$CACHE/kconfig-frontends

  ensure_checkout kconfig-frontends \
    https://github.com/patacongo/tools.git "$src" "$KCONFIG_TOOLS_REF"

  (
    cd "$src/kconfig-frontends"
    ./configure --prefix="$prefix" \
      --disable-kconfig --disable-nconf --disable-qconf \
      --disable-gconf --disable-mconf --disable-static \
      --disable-shared --disable-L10n
    touch aclocal.m4 Makefile.in
    make -j"$JOBS" install
  )

  [[ -x "$prefix/bin/kconfig-conf" ]] ||
    die "kconfig-conf build did not produce $prefix/bin/kconfig-conf"
}

p2llvm_tools_valid()
{
  local tool

  for tool in clang ld.lld llvm-ar llvm-nm llvm-objcopy llvm-objdump \
              llvm-readelf llvm-readobj llvm-size llvm-strip
  do
    [[ -x "$P2LLVM_ROOT/bin/$tool" ]] || return 1
  done

  [[ ! -e "$P2LLVM_ROOT/libc/lib/libc.a" ]] || return 1
}

p2llvm_preemption_valid()
{
  local probe_dir
  local source
  local ir_source
  local object
  local ir_object
  local disassembly
  local relocations
  local optimization

  p2llvm_tools_valid || return 1
  probe_dir=$(mktemp -d "$CACHE/p2-preemption-probe.XXXXXX") || return 1
  source=$probe_dir/probe.c
  ir_source=$probe_dir/probe.ll
  disassembly=$probe_dir/probe.dis
  relocations=$probe_dir/probe.relocs

  printf '%s\n' \
    'typedef unsigned int u32;' \
    'typedef int i32;' \
    'typedef unsigned long long u64;' \
    'typedef long long i64;' \
    'unsigned p2_mul(unsigned a, unsigned b) { return a * b; }' \
    'unsigned p2_div(unsigned a, unsigned b) { return a / b; }' \
    'unsigned p2_mod(unsigned a, unsigned b) { return a % b; }' \
    'int p2_sdiv(int a, int b) { return a / b; }' \
    'int p2_smod(int a, int b) { return a % b; }' \
    '_Bool p2_overflow(unsigned a, unsigned b, unsigned *r)' \
    '{ return __builtin_mul_overflow(a, b, r); }' \
    '_Bool p2_soverflow64(i64 a, i64 b, i64 *r)' \
    '{ return __builtin_mul_overflow(a, b, r); }' \
    '_Bool p2_uoverflow64(u64 a, u64 b, u64 *r)' \
    '{ return __builtin_mul_overflow(a, b, r); }' \
    'u32 p2_mulhu32(u32 a, u32 b)' \
    '{ return (u32)(((u64)a * (u64)b) >> 32); }' \
    'i32 p2_mulhs32(i32 a, i32 b)' \
    '{ return (i32)(((i64)a * (i64)b) >> 32); }' \
    'i32 p2_sdivc32(i32 a) { return a / 365; }' \
    'u32 p2_udivc32(u32 a) { return a / 365; }' \
    'i64 p2_sdivc64(i64 a) { return a / 86400; }' \
    'u64 p2_udivc64(u64 a) { return a / 86400; }' \
    'i64 p2_sdivp2(i64 a) { return a / 256; }' \
    'u64 p2_udivp2(u64 a) { return a / 256; }' > "$source"

  printf '%s\n' \
    'target triple = "p2"' \
    'define i64 @p2_mulhu64(i64 %a, i64 %b) {' \
    '  %ax = zext i64 %a to i128' \
    '  %bx = zext i64 %b to i128' \
    '  %p = mul i128 %ax, %bx' \
    '  %h = lshr i128 %p, 64' \
    '  %r = trunc i128 %h to i64' \
    '  ret i64 %r' \
    '}' \
    'define i64 @p2_mulhs64(i64 %a, i64 %b) {' \
    '  %ax = sext i64 %a to i128' \
    '  %bx = sext i64 %b to i128' \
    '  %p = mul i128 %ax, %bx' \
    '  %h = ashr i128 %p, 64' \
    '  %r = trunc i128 %h to i64' \
    '  ret i64 %r' \
    '}' > "$ir_source"

  : > "$disassembly"
  : > "$relocations"
  for optimization in O0 Os O2
  do
    object=$probe_dir/probe-$optimization.o
    ir_object=$probe_dir/probe-high64-$optimization.o

    if ! "$P2LLVM_ROOT/bin/clang" --target=p2 -"$optimization" \
         -fno-jump-tables -fno-builtin -ffunction-sections \
         -fdata-sections -c "$source" -o "$object" ||
       ! "$P2LLVM_ROOT/bin/clang" --target=p2 -"$optimization" \
         -fno-jump-tables -x ir -c "$ir_source" -o "$ir_object" ||
       ! "$P2LLVM_ROOT/bin/llvm-objdump" -dr "$object" \
         >> "$disassembly" ||
       ! "$P2LLVM_ROOT/bin/llvm-objdump" -dr "$ir_object" \
         >> "$disassembly" ||
       ! "$P2LLVM_ROOT/bin/llvm-readobj" --relocations "$object" \
         >> "$relocations" ||
       ! "$P2LLVM_ROOT/bin/llvm-readobj" --relocations "$ir_object" \
         >> "$relocations";
    then
      rm -rf "$probe_dir"
      return 1
    fi
  done

  if grep -Eiq '(qmul|qdiv|qfrac|qsqrt|qrotate|qvector|qlog|qexp|getqx|getqy)' \
       "$disassembly" ||
     grep -q 'R_P2_COG9' "$relocations" ||
     ! grep -q 'R_P2_20 __mulsi3' "$relocations" ||
     ! grep -q 'R_P2_20 __divsi3' "$relocations" ||
     ! grep -q 'R_P2_20 __udivsi3' "$relocations" ||
     ! grep -q 'R_P2_20 __modsi3' "$relocations" ||
     ! grep -q 'R_P2_20 __umodsi3' "$relocations" ||
     ! grep -q 'R_P2_20 __divdi3' "$relocations" ||
     ! grep -q 'R_P2_20 __udivdi3' "$relocations";
  then
    rm -rf "$probe_dir"
    return 1
  fi

  rm -rf "$probe_dir"
}

p2llvm_linker_aug20_valid()
{
  local probe_dir
  local bad_source
  local good_source
  local bad_object
  local good_object
  local bad_relocations
  local good_relocations
  local bad_error

  p2llvm_tools_valid || return 1
  probe_dir=$(mktemp -d "$CACHE/p2-linker-aug20-probe.XXXXXX") || return 1
  bad_source=$probe_dir/bad.S
  good_source=$probe_dir/good.S
  bad_object=$probe_dir/bad.o
  good_object=$probe_dir/good.o
  bad_relocations=$probe_dir/bad.relocs
  good_relocations=$probe_dir/good.relocs
  bad_error=$probe_dir/bad-link.err

  printf '%s\n' \
    '.section .text.bad,"ax",@progbits' \
    '.globl p2_aug20_bad' \
    'p2_aug20_bad:' \
    '  wrlong r0, ##p2_aug20_bad_target' \
    '.section .bss.bad,"aw",@nobits' \
    '.globl p2_aug20_bad_target' \
    '.balign 512' \
    'p2_aug20_bad_target:' \
    '  .space 4' > "$bad_source"

  printf '%s\n' \
    '.section .text.good,"ax",@progbits' \
    '.globl p2_aug20_good' \
    'p2_aug20_good:' \
    '  augs #0' \
    '  wrlong r0, ##p2_aug20_good_target' \
    '.section .bss.good,"aw",@nobits' \
    '.globl p2_aug20_good_target' \
    '.balign 512' \
    'p2_aug20_good_target:' \
    '  .space 4' > "$good_source"

  if ! "$P2LLVM_ROOT/bin/clang" --target=p2 -x assembler -c \
       "$bad_source" -o "$bad_object" ||
     ! "$P2LLVM_ROOT/bin/clang" --target=p2 -x assembler -c \
       "$good_source" -o "$good_object" ||
     ! "$P2LLVM_ROOT/bin/llvm-readobj" --relocations "$bad_object" \
       > "$bad_relocations" ||
     ! "$P2LLVM_ROOT/bin/llvm-readobj" --relocations "$good_object" \
       > "$good_relocations";
  then
    rm -rf "$probe_dir"
    return 1
  fi

  if ! grep -Eq '0x0 R_P2_AUG20 p2_aug20_bad_target' \
       "$bad_relocations" ||
     ! grep -Eq '0x4 R_P2_AUG20 p2_aug20_good_target' \
       "$good_relocations";
  then
    rm -rf "$probe_dir"
    return 1
  fi

  if "$P2LLVM_ROOT/bin/ld.lld" -e p2_aug20_bad -Ttext=0x1000 \
       -o "$probe_dir/bad.elf" "$bad_object" 2> "$bad_error" ||
     ! grep -q \
       'R_P2_AUG20 relocation has no preceding AUGS/AUGD instruction in its input section' \
       "$bad_error" ||
     ! "$P2LLVM_ROOT/bin/ld.lld" -e p2_aug20_good -Ttext=0x1000 \
       -o "$probe_dir/good.elf" "$good_object";
  then
    rm -rf "$probe_dir"
    return 1
  fi

  rm -rf "$probe_dir"
}

p2llvm_bool_memory_valid()
{
  local probe_dir
  local source
  local object
  local disassembly
  local optimization

  p2llvm_tools_valid || return 1
  probe_dir=$(mktemp -d "$CACHE/p2-bool-memory-probe.XXXXXX") || return 1
  source=$probe_dir/probe.ll

  printf '%s\n' \
    'target triple = "p2"' \
    '@p2_static_bool = internal global i1 false, align 1' \
    '@p2_global_bool = global i1 false, align 1' \
    'define i32 @p2_static_bool_branch() {' \
    'entry:' \
    '  %v = load i1, i1* @p2_static_bool, align 1' \
    '  br i1 %v, label %yes, label %no' \
    'yes:' \
    '  ret i32 37' \
    'no:' \
    '  ret i32 11' \
    '}' \
    'define i32 @p2_global_bool_branch() {' \
    'entry:' \
    '  %v = load i1, i1* @p2_global_bool, align 1' \
    '  br i1 %v, label %yes, label %no' \
    'yes:' \
    '  ret i32 41' \
    'no:' \
    '  ret i32 13' \
    '}' \
    'define void @p2_static_bool_store(i1 %v) {' \
    '  store i1 %v, i1* @p2_static_bool, align 1' \
    '  ret void' \
    '}' \
    'define void @p2_global_bool_store(i1 %v) {' \
    '  store i1 %v, i1* @p2_global_bool, align 1' \
    '  ret void' \
    '}' > "$source"

  for optimization in O0 Os O2
  do
    object=$probe_dir/probe-$optimization.o
    disassembly=$probe_dir/probe-$optimization.dis

    if ! "$P2LLVM_ROOT/bin/clang" --target=p2 -"$optimization" \
         -fno-jump-tables -ffunction-sections -fdata-sections \
         -x ir -c "$source" -o "$object" ||
       ! "$P2LLVM_ROOT/bin/llvm-objdump" -dr "$object" \
         > "$disassembly" ||
       ! grep -Eq 'rdbyte[[:space:]]+r[0-9]+,[[:space:]]+r[0-9]+' \
         "$disassembly" ||
       ! grep -Eq 'wrbyte[[:space:]]+' "$disassembly" ||
       ! grep -Eq 'zerox[[:space:]]+r[0-9]+,[[:space:]]+#0' \
         "$disassembly" ||
       ! grep -Eq 'R_P2_AUG20[[:space:]]+p2_global_bool' \
         "$disassembly" ||
       grep -Eq 'rdbyte[[:space:]]+[^,]+,[[:space:]]*#' \
         "$disassembly";
    then
      rm -rf "$probe_dir"
      return 1
    fi
  done

  rm -rf "$probe_dir"
}

p2llvm_conditional_branch_valid()
{
  local probe_dir
  local source=$ROOT/tools/p2/probes/p2llvm-tj-fallthrough.ll
  local object
  local disassembly
  local optimization

  p2llvm_tools_valid || return 1
  [[ -f "$source" ]] || return 1
  probe_dir=$(mktemp -d "$CACHE/p2-conditional-branch-probe.XXXXXX") ||
    return 1

  for optimization in O0 Os O2
  do
    object=$probe_dir/probe-$optimization.o
    disassembly=$probe_dir/probe-$optimization.dis

    if ! "$P2LLVM_ROOT/bin/clang" --target=p2 -"$optimization" \
         -fno-jump-tables -ffunction-sections -fdata-sections \
         -x ir -c "$source" -o "$object" ||
       ! "$P2LLVM_ROOT/bin/llvm-objdump" -d "$object" \
         > "$disassembly" ||
       grep -Eq 'tjnz[[:space:]]+r[0-9]+,[[:space:]]+#0([[:space:]]|$)' \
         "$disassembly";
    then
      rm -rf "$probe_dir"
      return 1
    fi

    if [[ "$optimization" != O0 ]] &&
       ! grep -Eq 'tjnz[[:space:]]+r[0-9]+,' "$disassembly";
    then
      rm -rf "$probe_dir"
      return 1
    fi
  done

  rm -rf "$probe_dir"
}

p2llvm_compare64_valid()
{
  p2llvm_tools_valid || return 1
  "$PYTHON" "$ROOT/tools/p2/compare64_codegen.py" \
    --toolchain-root "$P2LLVM_ROOT" >/dev/null 2>&1
}

p2llvm_valid()
{
  p2llvm_tools_valid || return 1
  p2llvm_preemption_valid || return 1
  p2llvm_linker_aug20_valid || return 1
  p2llvm_bool_memory_valid || return 1
  p2llvm_conditional_branch_valid || return 1
  p2llvm_compare64_valid || return 1
  [[ -f "$P2LLVM_ROOT/libp2/lib/libp2.a" ]] || return 1
  [[ -f "$P2LLVM_ROOT/libp2/include/propeller2.h" ]] || return 1
}

apply_p2llvm_patch()
{
  local llvm_dir=$P2LLVM_SRC/llvm-project
  local current_patch

  [[ -f "$P2LLVM_PATCH" ]] ||
    die "required p2llvm preemption patch is missing: $P2LLVM_PATCH"

  current_patch=$(mktemp "$CACHE/p2llvm-current-patch.XXXXXX")
  git -C "$llvm_dir" diff -U0 -- |
    sed -E 's/[[:space:]]+$//' > "$current_patch"

  if git -C "$llvm_dir" apply --unidiff-zero --reverse --check \
       "$P2LLVM_PATCH" \
       >/dev/null 2>&1; then
    cmp -s "$current_patch" "$P2LLVM_PATCH" ||
      { rm -f "$current_patch";
        die "p2llvm source has changes in addition to the required patch"; }
    rm -f "$current_patch"
    return
  fi

  if [[ -s "$current_patch" ]] ||
     ! git -C "$llvm_dir" diff --quiet --ignore-submodules=dirty --;
  then
    rm -f "$current_patch"
    die "p2llvm llvm-project has tracked modifications; refusing to overwrite"
  fi

  rm -f "$current_patch"
  git -C "$llvm_dir" apply --unidiff-zero --check "$P2LLVM_PATCH"
  git -C "$llvm_dir" apply --unidiff-zero "$P2LLVM_PATCH"
  echo "Applied $(basename "$P2LLVM_PATCH")"
}

build_libp2()
{
  local include_path=$ROOT/tools/p2/libp2-shims
  local logdir=$CACHE/logs

  [[ -f "$include_path/stdio.h" ]] ||
    die "libp2 stdio build shim is missing"
  [[ -f "$include_path/math.h" ]] ||
    die "libp2 math build shim is missing"

  mkdir -p "$logdir"
  (
    cd "$P2LLVM_SRC"
    C_INCLUDE_PATH="$include_path${C_INCLUDE_PATH:+:$C_INCLUDE_PATH}" \
      "$PYTHON" build.py --configure --skip_llvm --skip_libc \
        --install "$P2LLVM_ROOT" \
        2>&1 | tee "$logdir/p2llvm-libp2-build.log"
  )

  [[ -f "$P2LLVM_ROOT/libp2/lib/libp2.a" ]] ||
    die "p2llvm build.py did not install libp2.a"
  [[ -f "$P2LLVM_ROOT/libp2/include/propeller2.h" ]] ||
    die "p2llvm build.py did not install the libp2 headers"
}

build_p2llvm()
{
  local builddir=$P2LLVM_SRC/llvm-project/build_release
  local logdir=$CACHE/logs
  local supported_build_ok=1

  mkdir -p "$logdir"

  if ! p2llvm_tools_valid; then
    if ! (
      cd "$P2LLVM_SRC"
      "$PYTHON" build.py --configure --skip_libc --install "$P2LLVM_ROOT" \
        2>&1 | tee "$logdir/p2llvm-build.log"
    ); then
      supported_build_ok=0
    fi
  fi

  if ! p2llvm_tools_valid && [[ "$(uname -s)" == Darwin ]]; then
    if [[ $supported_build_ok -eq 0 ]]; then
      echo "Supported p2llvm build failed; preserving its log and applying" \
           "the verified Darwin host-header ordering workaround"
    else
      echo "Applying the verified Darwin host-header ordering workaround"
    fi

    cmake -S "$P2LLVM_SRC/llvm-project/llvm" -B "$builddir" \
      -DLLVM_ENABLE_ZLIB=OFF \
      -DLLVM_ENABLE_LIBXML2=OFF \
      -DCMAKE_DISABLE_FIND_PACKAGE_Backtrace:BOOL=TRUE
    (
      cd "$P2LLVM_SRC"
      "$PYTHON" build.py --skip_libp2 --skip_libc \
        --install "$P2LLVM_ROOT" \
        2>&1 | tee "$logdir/p2llvm-build-darwin-retry.log"
    )
  fi

  p2llvm_tools_valid ||
    die "p2llvm compiler and LLVM tools did not satisfy their postconditions"

  if ! p2llvm_preemption_valid || ! p2llvm_linker_aug20_valid ||
     ! p2llvm_bool_memory_valid || ! p2llvm_conditional_branch_valid ||
     ! p2llvm_compare64_valid;
  then
    (
      cd "$P2LLVM_SRC"
      "$PYTHON" build.py --skip_libp2 --skip_libc \
        --install "$P2LLVM_ROOT" \
        2>&1 | tee "$logdir/p2llvm-preempt-safe-rebuild.log"
    )
  fi

  p2llvm_preemption_valid ||
    die "p2llvm still emits preemption-unsafe P2 CORDIC sequences"
  p2llvm_linker_aug20_valid ||
    die "p2llvm linker does not safely reject offset-zero R_P2_AUG20"
  p2llvm_bool_memory_valid ||
    die "p2llvm does not correctly select global/static i1 memory operations"
  p2llvm_conditional_branch_valid ||
    die "p2llvm loses the fallthrough of conditional TJZ/TJNZ branches"
  "$PYTHON" "$ROOT/tools/p2/compare64_codegen.py" \
    --toolchain-root "$P2LLVM_ROOT" ||
    die "p2llvm does not correctly lower 64-bit comparisons"

  if [[ ! -f "$P2LLVM_ROOT/libp2/lib/libp2.a" ]]; then
    build_libp2
  fi

  p2llvm_valid || die "p2llvm build did not satisfy its postconditions"
}

require_hil_host_tools()
{
  need_command flock
  need_command lsof

  if ! command -v timeout >/dev/null 2>&1 &&
     ! command -v gtimeout >/dev/null 2>&1; then
    die "timeout or gtimeout is required for bounded HIL operations"
  fi
}

write_environment()
{
  local envfile=$HOME/.p2-nuttx-env

  {
    printf 'export NUTTX_APPS_DIR=%q\n' "$APPS_DIR"
    printf 'export P2_CACHE=%q\n' "$CACHE"
    printf 'export P2LLVM_ROOT=%q\n' "$P2LLVM_ROOT"
    printf 'export FLEXPROP_ROOT=%q\n' "$FLEXPROP_ROOT"
    printf 'export FLEXSPIN=%q\n' "$FLEXPROP_ROOT/bin/flexspin"
    printf 'export LOADP2=%q\n' "$FLEXPROP_ROOT/bin/loadp2"
    printf 'export P2_PYTHON=%q\n' "$PYTHON_VENV/bin/python"
    printf 'export KCONFIG_CONF=%q\n' "$KCONFIG_CONF"
    printf 'export P2_TOOLCHAIN_LOCK=%q\n' "$RUNTIME_LOCK"
    printf 'export PATH=%q:%s\n' \
      "$PYTHON_VENV/bin:$P2LLVM_ROOT/bin:$FLEXPROP_ROOT/bin:$(dirname "$KCONFIG_CONF")" \
      "\$PATH"
  } > "$envfile"

  echo "Wrote $envfile"
}

write_lock()
{
  local lock=$RUNTIME_LOCK
  local tmp=$lock.tmp
  local kconfig_pc
  local kconfig_version=unknown

  kconfig_pc=$(find "$(dirname "$(dirname "$KCONFIG_CONF")")" \
    -name kconfig-parser.pc -type f -print -quit 2>/dev/null || true)
  if [[ -n "$kconfig_pc" ]]; then
    kconfig_version=$(sed -n 's/^Version:[[:space:]]*//p' "$kconfig_pc")
  fi

  mkdir -p "$(dirname "$lock")"
  {
    echo "nuttx_commit=$(git_head "$ROOT")"
    echo "nuttx_apps_commit=$(git_head "$APPS_DIR")"
    echo "p2llvm_commit=$(git_head "$P2LLVM_SRC")"
    echo "p2llvm_llvm_project_commit=$(git_head "$P2LLVM_SRC/llvm-project")"
    echo "p2llvm_loadp2_commit=$(git_head "$P2LLVM_SRC/loadp2")"
    echo "flexprop_commit=$(git_head "$FLEXPROP_ROOT")"
    echo "spin2cpp_commit=$(git_head "$FLEXPROP_ROOT/spin2cpp")"
    echo "loadp2_commit=$(git_head "$FLEXPROP_ROOT/loadp2")"
    echo "compiler=$("$P2LLVM_ROOT/bin/clang" --version | head -1)"
    echo "linker=$("$P2LLVM_ROOT/bin/ld.lld" --version | head -1)"
    echo "kconfig_conf=$KCONFIG_CONF"
    echo "kconfig_parser_version=$kconfig_version"
    echo "python=$("$PYTHON_VENV/bin/python" --version 2>&1)"
    echo "pyserial=$("$PYTHON_VENV/bin/python" -c 'import serial; print(serial.__version__)')"
    echo "pyelftools=$("$PYTHON_VENV/bin/python" -c 'import elftools; print(elftools.__version__)')"
    echo "host_os=$(uname -a)"
    echo "p2llvm_libc=skipped_not_installed"
    echo "p2llvm_libp2_shims=unused stdio.h and math.h includes only"
    echo "p2llvm_preempt_safe_integer=verified q-free Hub libcalls and limb-expanded mulh"
    echo "p2llvm_linker_aug20_guard=verified offset-zero rejection and explicit-AUGS link"
    echo "p2llvm_bool_memory=verified global/static i1 loads and stores at O0 Os O2"
    echo "p2llvm_conditional_branch=verified TJZ/TJNZ fallthrough at O0 Os O2"
    echo "p2llvm_compare64=verified high-first signed/unsigned limb comparisons at O0 Os O2"
    echo "p2llvm_preempt_patch=$(basename "$P2LLVM_PATCH")"
    echo "p2llvm_darwin_cmake=-DLLVM_ENABLE_ZLIB=OFF -DLLVM_ENABLE_LIBXML2=OFF -DCMAKE_DISABLE_FIND_PACKAGE_Backtrace:BOOL=TRUE"
    echo "p2_flags=--target=p2 -fno-jump-tables -ffunction-sections -fdata-sections -fno-common -fno-builtin -Os"
    shasum -a 256 "$P2LLVM_PATCH" "$KCONFIG_CONF" \
      "$P2LLVM_ROOT/bin/clang" \
      "$P2LLVM_ROOT/bin/ld.lld" "$P2LLVM_ROOT/libp2/lib/libp2.a" \
      "$FLEXPROP_ROOT/bin/flexspin" "$FLEXPROP_ROOT/bin/loadp2" |
      sed 's/^/sha256=/'
  } > "$tmp"

  mv "$tmp" "$lock"
  echo "Wrote $lock"
}

for command in cmp git make cmake "$PYTHON" shasum
do
  need_command "$command"
done

require_hil_host_tools

JOBS=${JOBS:-$(host_jobs)}
mkdir -p "$CACHE"

ensure_apps_checkout
ensure_checkout p2llvm https://github.com/ne75/p2llvm.git \
  "$P2LLVM_SRC" "$P2LLVM_REF"
require_gitlink "$P2LLVM_SRC/llvm-project" "$LLVM_PROJECT_REF"
require_gitlink "$P2LLVM_SRC/loadp2" "$P2LLVM_LOADP2_REF"
apply_p2llvm_patch
ensure_checkout FlexProp https://github.com/totalspectrum/flexprop.git \
  "$FLEXPROP_ROOT" "$FLEXPROP_REF"
require_gitlink "$FLEXPROP_ROOT/spin2cpp" "$SPIN2CPP_REF"
require_gitlink "$FLEXPROP_ROOT/loadp2" "$LOADP2_REF"

if ! KCONFIG_CONF=$(find_kconfig_conf); then
  build_kconfig_conf
  KCONFIG_CONF=$CACHE/kconfig-frontends/bin/kconfig-conf
fi

if ! p2llvm_valid; then
  build_p2llvm
fi

make -C "$FLEXPROP_ROOT" -j"$JOBS" bin/flexspin bin/loadp2
[[ -x "$FLEXPROP_ROOT/bin/flexspin" ]] || die "flexspin is missing"
[[ -x "$FLEXPROP_ROOT/bin/loadp2" ]] || die "loadp2 is missing"

if [[ ! -x "$PYTHON_VENV/bin/python" ]]; then
  "$PYTHON" -m venv "$PYTHON_VENV"
fi
"$PYTHON_VENV/bin/python" -m pip install --require-hashes \
  -r "$ROOT/tools/p2/requirements-hil.txt"

"$P2LLVM_ROOT/bin/clang" --print-targets |
  grep -qE '^[[:space:]]*p2[[:space:]]+-[[:space:]]+Propeller 2$' ||
  die "installed clang does not advertise the P2 target"

PROBE_DIR=$(mktemp -d "$CACHE/p2-probe.XXXXXX")
trap 'rm -rf "$PROBE_DIR"' EXIT
printf 'int p2_probe(void) { return 42; }\n' > "$PROBE_DIR/probe.c"
"$P2LLVM_ROOT/bin/clang" --target=p2 -fno-jump-tables \
  -ffunction-sections -fdata-sections -c "$PROBE_DIR/probe.c" \
  -o "$PROBE_DIR/probe.o"
"$P2LLVM_ROOT/bin/llvm-readobj" --file-headers "$PROBE_DIR/probe.o" |
  grep -q 'Format: elf32-p2' ||
  die "P2 compiler probe did not produce a Propeller object"

write_environment
write_lock
echo "P2 local toolchain bootstrap: PASS"
