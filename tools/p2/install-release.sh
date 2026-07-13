#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Install one verified P2 release image in RAM, SPI flash, or the microSD card
# attached to the P2.  Every mutating mode is a dry-run unless --execute and
# the corresponding authorization gates are supplied.

set -euo pipefail

ROOT=$(cd "$(dirname "$0")" && pwd)
PYTHON=${P2_PYTHON:-python3}
action=${1:-}
[[ $# -eq 0 ]] || shift
execute=0
board=
port=

usage()
{
  cat >&2 <<'EOF'
usage:
  ./install-p2.sh verify
  ./install-p2.sh ram   --board BOARD --port DEVICE [--execute]
  ./install-p2.sh flash --board BOARD --port DEVICE [--execute]
  ./install-p2.sh sd    --board BOARD --port DEVICE [--execute]

BOARD must be exactly p2-ec32mb (P2-EC32MB Rev B) or p2-ec (P2-EC Rev D).
Selection is mandatory so an image cannot silently be installed on the wrong
hardware.  The direct _BOOT_P2.BIX compatibility asset is EC32MB Rev B only.

Execution authorization:
  ram:   P2_HIL=1 P2_ALLOW_RESET=1
  flash: P2_HIL=1 P2_ALLOW_RESET=1 P2_ALLOW_FLASH_WRITE=1
         P2_ALLOW_FLASH_ERASE=1 P2_ALLOW_SD_WRITE=1
  sd:    P2_HIL=1 P2_ALLOW_RESET=1 P2_ALLOW_SD_WRITE=1
         P2_ALLOW_SD_DESTRUCTIVE=1

Without --execute, the installer verifies the complete release and prints the
exact command without opening serial, resetting the P2, erasing flash, or
writing flash/SD media.  The bundled loader is macOS arm64 only.
EOF
  exit 2
}

case "$action" in
  verify|ram|flash|sd) ;;
  -h|--help|"") usage ;;
  *) echo "ERROR: unknown action: $action" >&2; usage ;;
esac

while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute)
      execute=1
      ;;
    --board)
      shift
      [[ $# -gt 0 ]] || usage
      board=$1
      ;;
    --port)
      shift
      [[ $# -gt 0 ]] || usage
      port=$1
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "ERROR: unknown option: $1" >&2
      usage
      ;;
  esac
  shift
done

[[ -x "$ROOT/verify-release.py" ]] ||
  { echo "ERROR: bundled verify-release.py is absent or not executable" >&2;
    exit 2; }
verification=$("$PYTHON" "$ROOT/verify-release.py" verify "$ROOT")
printf '%s\n' "$verification"
value()
{
  printf '%s\n' "$verification" | sed -n "s/^$1=//p"
}

bundled_loadp2=$ROOT/$(value loadp2)
loadp2_override=${P2_LOADP2:-}
if [[ -n "$loadp2_override" ]]; then
  if [[ "$loadp2_override" == */* ]]; then
    loadp2=$loadp2_override
  else
    loadp2=$(command -v "$loadp2_override" || true)
  fi
elif [[ -x "$bundled_loadp2" ]]; then
  loadp2=$bundled_loadp2
else
  loadp2=$(command -v loadp2 || true)
fi
[[ -n "$loadp2" && -x "$loadp2" ]] ||
  { echo "ERROR: selected loadp2 is absent or not executable" >&2; exit 2; }
bundled_loadp2_sha=$(shasum -a 256 "$bundled_loadp2" | awk '{print $1}')
loadp2_sha=$(shasum -a 256 "$loadp2" | awk '{print $1}')
[[ "$loadp2_sha" == "$bundled_loadp2_sha" ]] ||
  { echo "ERROR: loadp2 override differs from the verified bundled loader" >&2;
    exit 2; }
sd_writer=$ROOT/$(value sd_writer)
loader_baud=${P2_LOADER_BAUD:-2000000}
console_baud=${P2_CONSOLE_BAUD:-230400}

[[ "$loader_baud" =~ ^[0-9]+$ && $loader_baud -gt 0 ]] ||
  { echo "ERROR: P2_LOADER_BAUD must be a positive integer" >&2; exit 2; }
[[ "$console_baud" =~ ^[0-9]+$ && $console_baud -gt 0 ]] ||
  { echo "ERROR: P2_CONSOLE_BAUD must be a positive integer" >&2; exit 2; }

if [[ "$action" == verify ]]; then
  [[ $execute -eq 0 && -z "$board" && -z "$port" ]] || usage
  exit 0
fi

case "$board" in
  p2-ec32mb)
    board_key=p2_ec32mb
    ;;
  p2-ec)
    board_key=p2_ec
    ;;
  *)
    echo "ERROR: --board must be p2-ec32mb or p2-ec" >&2
    usage
    ;;
esac
[[ -n "$port" ]] || usage
ram_elf=$ROOT/$(value "${board_key}_ram_elf")
flash_image=$ROOT/$(value "${board_key}_flash_image")
sd_boot_image=$ROOT/$(value "${board_key}_sd_boot_image")
echo "selected_board=$board"

print_command()
{
  printf 'install_command='
  printf '%q ' "$@"
  printf '\n'
}

case "$action" in
  ram)
    command=("$loadp2" -p "$port" -l "$loader_baud" -b "$console_baud"
             -ZERO -v -DTR "$ram_elf" -t)
    ;;
  flash)
    command=("$loadp2" -p "$port" -l "$loader_baud"
             -DTR -SINGLE -FLASH -v "$flash_image")
    ;;
  sd)
    for filespec_path in "$sd_writer" "$sd_boot_image"; do
      [[ "$filespec_path" != *,* ]] ||
        { echo "ERROR: SD writer paths cannot contain a comma" >&2; exit 2; }
    done
    writer_clock_hz=${P2_SD_WRITER_CLOCK_HZ:-160000000}
    writer_clock_mode=${P2_SD_WRITER_CLOCK_MODE:-010007f8}
    recv_timeout_ms=${P2_SD_BOOT_RECV_TIMEOUT_MS:-120000}
    [[ "$writer_clock_hz" =~ ^[0-9]+$ && $writer_clock_hz -gt 0 ]] ||
      { echo "ERROR: P2_SD_WRITER_CLOCK_HZ must be a positive integer" >&2;
        exit 2; }
    [[ "$recv_timeout_ms" =~ ^[0-9]+$ && $recv_timeout_ms -gt 0 ]] ||
      { echo "ERROR: P2_SD_BOOT_RECV_TIMEOUT_MS must be positive" >&2;
        exit 2; }
    [[ "$writer_clock_mode" =~ ^[0-9A-Fa-f]{1,8}$ ]] ||
      { echo "ERROR: P2_SD_WRITER_CLOCK_MODE must be hexadecimal" >&2;
        exit 2; }
    filespec="@0=$sd_writer,@8000+$sd_boot_image"
    recv_script="recvtimeout($recv_timeout_ms) recv(SD Updater) recv(Card mounted) recv(writing _BOOT_P2.BIX...OK)"
    command=("$loadp2" -p "$port" -l "$loader_baud" -b "$console_baud"
             -f "$writer_clock_hz" -m "$writer_clock_mode" -PATCH
             -DTR -ZERO -CHIP -v -e "$recv_script" "$filespec")
    echo "sd_output_name=_BOOT_P2.BIX"
    echo "BOOT-UNVERIFIED: physical SD-only reset is still required"
    ;;
esac
print_command "${command[@]}"

if [[ $execute -eq 0 ]]; then
  echo "DRY-RUN: no serial open, reset, erase, flash write, or SD write was performed"
  exit 0
fi

[[ "${P2_HIL:-0}" == 1 ]] ||
  { echo "ERROR: P2_HIL=1 is required" >&2; exit 2; }
[[ "${P2_ALLOW_RESET:-0}" == 1 ]] ||
  { echo "ERROR: P2_ALLOW_RESET=1 is required because loadp2 uses -DTR" >&2;
    exit 2; }

if [[ "$action" == flash ]]; then
  [[ "${P2_ALLOW_FLASH_WRITE:-0}" == 1 ]] ||
    { echo "ERROR: P2_ALLOW_FLASH_WRITE=1 is required" >&2; exit 2; }
  [[ "${P2_ALLOW_FLASH_ERASE:-0}" == 1 ]] ||
    { echo "ERROR: P2_ALLOW_FLASH_ERASE=1 is required" >&2; exit 2; }
  [[ "${P2_ALLOW_SD_WRITE:-0}" == 1 ]] ||
    { echo "ERROR: P2_ALLOW_SD_WRITE=1 is required because flash programming drives shared P60/P61" >&2;
      exit 2; }
fi

if [[ "$action" == sd ]]; then
  [[ "${P2_ALLOW_SD_WRITE:-0}" == 1 ]] ||
    { echo "ERROR: P2_ALLOW_SD_WRITE=1 is required" >&2; exit 2; }
  [[ "${P2_ALLOW_SD_DESTRUCTIVE:-0}" == 1 ]] ||
    { echo "ERROR: P2_ALLOW_SD_DESTRUCTIVE=1 is required because root _BOOT_P2.BIX is deleted and recreated" >&2;
      exit 2; }
fi

if [[ "${P2_ALLOW_TEST_HOST:-0}" != 1 ]]; then
  [[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] ||
    { echo "ERROR: bundled loadp2 requires macOS arm64" >&2; exit 2; }
fi
[[ -c "$port" ]] ||
  { echo "ERROR: serial device is absent: $port" >&2; exit 2; }

lock_dir=${P2_LOCK_DIR:-/tmp/nuttx-p2-release.lock}
if ! mkdir "$lock_dir" 2>/dev/null; then
  echo "ERROR: P2 board lock is busy: $lock_dir" >&2
  exit 2
fi
# shellcheck disable=SC2329  # Invoked by the EXIT/INT/TERM trap below.
release_lock()
{
  if [[ -n "${run_log:-}" ]]; then
    rm -f "$run_log"
  fi
  rm -f "$lock_dir/pid"
  rmdir "$lock_dir" 2>/dev/null || true
}
trap release_lock EXIT INT TERM
printf '%s\n' "$$" > "$lock_dir/pid"

owners=$(lsof -t "$port" 2>/dev/null || true)
[[ -z "$owners" ]] ||
  { echo "ERROR: serial port is owned by PID(s): $owners" >&2; exit 2; }
case "$action" in
  ram) timeout_seconds=${P2_LOAD_TIMEOUT:-0} ;;
  flash) timeout_seconds=${P2_FLASH_TIMEOUT:-180} ;;
  sd) timeout_seconds=${P2_SD_BOOT_TIMEOUT:-150} ;;
esac
[[ "$timeout_seconds" =~ ^[0-9]+$ ]] ||
  { echo "ERROR: load timeout must be a non-negative integer" >&2; exit 2; }
if [[ "$action" != ram && $timeout_seconds -eq 0 ]]; then
  echo "ERROR: flash and SD timeouts must be positive" >&2
  exit 2
fi

run_log=$(mktemp "${TMPDIR:-/tmp}/nuttx-p2-release-run.XXXXXX")
set +e
"$PYTHON" "$ROOT/verify-release.py" run \
  --timeout "$timeout_seconds" -- "${command[@]}" 2>&1 | tee "$run_log"
pipeline_status=("${PIPESTATUS[@]}")
set -e
result=${pipeline_status[0]}
if [[ ${pipeline_status[1]} -ne 0 && $result -eq 0 ]]; then
  echo "ERROR: could not preserve installer command output" >&2
  result=2
fi
if [[ "$action" == sd ]]; then
  set +e
  grep -Fq 'ERROR:' "$run_log"
  error_scan=$?
  set -e
  if [[ $error_scan -eq 0 ]]; then
    echo "ERROR: SD writer reported ERROR: output; refusing PASS even if loadp2 exited zero" >&2
    if [[ $result -eq 0 ]]; then
      result=3
    fi
  elif [[ $error_scan -ne 1 ]]; then
    echo "ERROR: could not inspect preserved SD writer output" >&2
    if [[ $result -eq 0 ]]; then
      result=2
    fi
  fi
fi
if [[ $result -eq 0 && "$action" == sd ]]; then
  echo "PASS: writer recreated root _BOOT_P2.BIX from the selected board image"
  echo "BOOT-UNVERIFIED: switch to SD-only and perform the reset-only boot proof"
fi
exit "$result"
