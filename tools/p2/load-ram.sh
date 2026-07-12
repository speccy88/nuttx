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

execute=0
image=

usage()
{
  echo "usage: $0 --execute ELF-OR-BINARY" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute)
      execute=1
      ;;
    -h|--help)
      usage
      ;;
    --*)
      echo "ERROR: unknown option $1" >&2
      usage
      ;;
    *)
      [[ -z "$image" ]] || usage
      image=$1
      ;;
  esac
  shift
done

[[ $execute -eq 1 ]] || { echo "ERROR: --execute is required" >&2; exit 2; }
[[ "${P2_HIL:-0}" == 1 ]] || { echo "ERROR: P2_HIL=1 is required" >&2; exit 2; }
[[ -n "$image" ]] || usage
[[ -s "$image" ]] || { echo "ERROR: image is missing or empty: $image" >&2; exit 2; }
[[ -n "${P2_PORT:-}" ]] || { echo "ERROR: P2_PORT is unset" >&2; exit 2; }
[[ -c "$P2_PORT" ]] || { echo "ERROR: serial device is absent: $P2_PORT" >&2; exit 2; }
[[ -n "${LOADP2:-}" && -x "$LOADP2" ]] ||
  { echo "ERROR: pinned LOADP2 executable is unavailable" >&2; exit 2; }

loader_baud=${P2_LOADER_BAUD:-2000000}
console_baud=${P2_CONSOLE_BAUD:-230400}
reset_method=${P2_RESET_METHOD:-loadp2}
lock_file=${P2_LOCK_FILE:-/tmp/nuttx-p2-hil.lock}
timeout_seconds=${P2_LOAD_TIMEOUT:-20}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
artifact=${P2_LOAD_ARTIFACT:-$ROOT/artifacts/hil/$timestamp-load-ram}

mkdir -p "$artifact" "$(dirname "$lock_file")"

stop_stale_monitor()
{
  local pid
  local command

  while read -r pid; do
    [[ -n "$pid" ]] || continue
    command=$(ps -p "$pid" -o command= 2>/dev/null || true)
    if [[ "$command" == *"$ROOT/tools/p2/monitor.py"* ]]; then
      echo "Stopping stale P2 monitor PID $pid"
      kill -TERM "$pid"
      for _ in 1 2 3 4 5; do
        if ! kill -0 "$pid" 2>/dev/null; then
          break
        fi
        sleep 0.1
      done
      kill -0 "$pid" 2>/dev/null &&
        { echo "ERROR: stale monitor PID $pid did not exit" >&2; exit 2; }
    fi
  done < <(lsof -t "$P2_PORT" 2>/dev/null || true)
}

stop_stale_monitor

exec 9>"$lock_file"
flock -n 9 || { echo "ERROR: P2 board lock is busy: $lock_file" >&2; exit 2; }

owners=$(lsof -t "$P2_PORT" 2>/dev/null || true)
[[ -z "$owners" ]] ||
  { echo "ERROR: serial port is owned by PID(s): $owners" >&2; exit 2; }

command=("$LOADP2" -p "$P2_PORT" -l "$loader_baud" -b "$console_baud" \
         -ZERO -v)

case "$reset_method" in
  loadp2|dtr)
    command+=(-DTR)
    ;;
  rts)
    command+=(-RTS)
    ;;
  command)
    [[ -n "${P2_RESET_COMMAND:-}" ]] ||
      { echo "ERROR: P2_RESET_COMMAND is empty" >&2; exit 2; }
    "$SHELL" -lc "$P2_RESET_COMMAND"
    command+=(-n)
    ;;
  manual)
    echo "ERROR: manual reset cannot be automated" >&2
    exit 2
    ;;
  *)
    echo "ERROR: unsupported P2_RESET_METHOD=$reset_method" >&2
    exit 2
    ;;
esac

command+=("$image")

if command -v timeout >/dev/null 2>&1; then
  timeout_command=(timeout "$timeout_seconds")
elif command -v gtimeout >/dev/null 2>&1; then
  timeout_command=(gtimeout "$timeout_seconds")
else
  echo "ERROR: timeout or gtimeout is required" >&2
  exit 2
fi

{
  printf 'utc=%s\n' "$timestamp"
  printf 'branch=%s\n' "$(git -C "$ROOT" branch --show-current)"
  printf 'commit=%s\n' "$(git -C "$ROOT" rev-parse HEAD)"
  printf 'serial_port=%s\n' "$P2_PORT"
  printf 'reset_method=%s\n' "$reset_method"
  printf 'loader_baud=%s\n' "$loader_baud"
  printf 'console_baud=%s\n' "$console_baud"
  printf 'image=%s\n' "$(cd "$(dirname "$image")" && pwd)/$(basename "$image")"
  printf 'image_sha256=%s\n' "$(shasum -a 256 "$image" | awk '{print $1}')"
  printf 'command='
  printf '%q ' "${command[@]}"
  printf '\n'
} > "$artifact/loader-command.txt"

echo "P2 RAM load: $image"
set +e
"${timeout_command[@]}" "${command[@]}" 2>&1 | tee "$artifact/loader.log"
rc=${PIPESTATUS[0]}
set -e

printf 'exit_code=%d\nstatus=%s\n' "$rc" \
  "$([[ $rc -eq 0 ]] && echo PASS || echo FAIL)" > "$artifact/status.txt"

if [[ $rc -ne 0 ]]; then
  echo "ERROR: loadp2 failed with exit code $rc; see $artifact/loader.log" >&2
  exit "$rc"
fi

echo "P2 RAM load artifact: $artifact"
