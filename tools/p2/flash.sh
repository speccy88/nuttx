#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
[[ -f "$HOME/.p2-nuttx-env" ]] && source "$HOME/.p2-nuttx-env"

execute=0
port=
image=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute) execute=1 ;;
    --port) shift; port=${1:-} ;;
    --image) shift; image=${1:-} ;;
    *) echo "HIL REQUIRED: usage: $0 --port DEVICE --image BINARY [--execute]"; exit 2 ;;
  esac
  shift
done

[[ -n "$port" && -n "$image" ]] ||
  { echo "HIL REQUIRED: explicit --port and --image are required"; exit 2; }
[[ -s "$image" ]] || { echo "ERROR: image is missing or empty: $image" >&2; exit 2; }
[[ -n "${LOADP2:-}" && -x "$LOADP2" ]] ||
  { echo "ERROR: pinned LOADP2 is unavailable" >&2; exit 2; }

lock=${P2_TOOLCHAIN_LOCK:-$ROOT/tools/p2/toolchain.lock}
expected=$(sed -n "s|^sha256=\([0-9a-f][0-9a-f]*\)  $LOADP2$|\1|p" "$lock")
actual=$(shasum -a 256 "$LOADP2" | awk '{print $1}')
[[ -n "$expected" && "$expected" == "$actual" ]] ||
  { echo "ERROR: LOADP2 is not pinned by $lock" >&2; exit 2; }
help=$({ "$LOADP2" '-?' 2>&1 || true; })
grep -q '\[ -FLASH \].*program application to SPI flash' <<<"$help" ||
  { echo "ERROR: pinned LOADP2 does not advertise -FLASH" >&2; exit 2; }

python=${P2_PYTHON:-python3}
"$python" "$ROOT/tools/p2/verify-flash-layout.py" --image "$image"
command=("$LOADP2" -p "$port" -l "${P2_LOADER_BAUD:-2000000}"
         -DTR -SINGLE -FLASH -v "$image")
printf 'flash_command='
printf '%q ' "${command[@]}"
printf '\n'

if [[ $execute -eq 0 ]]; then
  echo "DRY-RUN: no serial open, reset, erase, or flash write was performed"
  exit 0
fi

[[ "${P2_HIL:-0}" == 1 ]] || { echo "ERROR: P2_HIL=1 is required" >&2; exit 2; }
[[ "${P2_ALLOW_FLASH_WRITE:-0}" == 1 ]] ||
  { echo "ERROR: P2_ALLOW_FLASH_WRITE=1 is required" >&2; exit 2; }
[[ "${P2_ALLOW_SD_WRITE:-0}" == 1 ]] ||
  { echo "ERROR: P2_ALLOW_SD_WRITE=1 is required because flash programming drives shared P60/P61" >&2; exit 2; }
[[ -c "$port" ]] || { echo "ERROR: serial device is absent: $port" >&2; exit 2; }

lock_file=${P2_LOCK_FILE:-/tmp/nuttx-p2-hil.lock}
exec 9>"$lock_file"
flock -n 9 || { echo "ERROR: P2 board lock is busy: $lock_file" >&2; exit 2; }
owners=$(lsof -t "$port" 2>/dev/null || true)
[[ -z "$owners" ]] || { echo "ERROR: serial port is owned by: $owners" >&2; exit 2; }

if command -v timeout >/dev/null 2>&1; then
  timeout_cmd=(timeout "${P2_FLASH_TIMEOUT:-180}")
elif command -v gtimeout >/dev/null 2>&1; then
  timeout_cmd=(gtimeout "${P2_FLASH_TIMEOUT:-180}")
else
  echo "ERROR: timeout or gtimeout is required" >&2
  exit 2
fi
"${timeout_cmd[@]}" "${command[@]}"
