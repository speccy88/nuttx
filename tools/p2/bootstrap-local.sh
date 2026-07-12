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

APPS_REF=62b7e955300b6dafa4f36d391474d3c8925b8106
P2LLVM_REF=bdcefcce7860b2232c06f35726fea679a3a7309c
LLVM_PROJECT_REF=72a9bb1ef2656d9953d1f41a8196d425ff2ab0b1
P2LLVM_LOADP2_REF=21e074cc7ee6fbd4fb12ef5352544b3457a6729c
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

p2llvm_valid()
{
  p2llvm_tools_valid || return 1
  [[ -f "$P2LLVM_ROOT/libp2/lib/libp2.a" ]] || return 1
  [[ -f "$P2LLVM_ROOT/libp2/include/propeller2.h" ]] || return 1
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

  if ! (
    cd "$P2LLVM_SRC"
    "$PYTHON" build.py --configure --skip_libc --install "$P2LLVM_ROOT" \
      2>&1 | tee "$logdir/p2llvm-build.log"
  ); then
    supported_build_ok=0
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
    printf 'export PATH=%q:%s\n' \
      "$PYTHON_VENV/bin:$P2LLVM_ROOT/bin:$FLEXPROP_ROOT/bin:$(dirname "$KCONFIG_CONF")" \
      "\$PATH"
  } > "$envfile"

  echo "Wrote $envfile"
}

write_lock()
{
  local lock=$ROOT/tools/p2/toolchain.lock
  local tmp=$lock.tmp
  local kconfig_pc
  local kconfig_version=unknown

  kconfig_pc=$(find "$(dirname "$(dirname "$KCONFIG_CONF")")" \
    -name kconfig-parser.pc -type f -print -quit 2>/dev/null || true)
  if [[ -n "$kconfig_pc" ]]; then
    kconfig_version=$(sed -n 's/^Version:[[:space:]]*//p' "$kconfig_pc")
  fi

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
    echo "p2llvm_darwin_cmake=-DLLVM_ENABLE_ZLIB=OFF -DLLVM_ENABLE_LIBXML2=OFF -DCMAKE_DISABLE_FIND_PACKAGE_Backtrace:BOOL=TRUE"
    echo "p2_flags=--target=p2 -fno-jump-tables -ffunction-sections -fdata-sections -fno-common -fno-builtin -Os"
    shasum -a 256 "$KCONFIG_CONF" "$P2LLVM_ROOT/bin/clang" \
      "$P2LLVM_ROOT/bin/ld.lld" "$P2LLVM_ROOT/libp2/lib/libp2.a" \
      "$FLEXPROP_ROOT/bin/flexspin" "$FLEXPROP_ROOT/bin/loadp2" |
      sed 's/^/sha256=/'
  } > "$tmp"

  mv "$tmp" "$lock"
  echo "Wrote $lock"
}

for command in git make cmake "$PYTHON" shasum
do
  need_command "$command"
done

require_hil_host_tools

JOBS=${JOBS:-$(host_jobs)}
mkdir -p "$CACHE"

ensure_checkout nuttx-apps https://github.com/apache/nuttx-apps.git \
  "$APPS_DIR" "$APPS_REF"
ensure_checkout p2llvm https://github.com/ne75/p2llvm.git \
  "$P2LLVM_SRC" "$P2LLVM_REF"
require_gitlink "$P2LLVM_SRC/llvm-project" "$LLVM_PROJECT_REF"
require_gitlink "$P2LLVM_SRC/loadp2" "$P2LLVM_LOADP2_REF"
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
