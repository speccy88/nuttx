#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)

if [[ -f "$HOME/.p2-nuttx-env" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/.p2-nuttx-env"
fi

hil_env=${P2_HIL_ENV_FILE:-$ROOT/.p2-hil.env}
if [[ -f "$hil_env" ]]; then
  # shellcheck disable=SC1090,SC1091
  source "$hil_env"
fi

# The pinned FlexProp writer occupies Hub RAM below 0x8000.  Its payload is a
# little-endian 32-bit byte count followed by the image and zero padding to a
# four-byte boundary, so the largest image it can stage is 0x80000 - 0x8004.
# Construct that envelope here instead of using loadp2's @ADDR+FILE form: the
# pinned loadp2 incorrectly prefixes a later file with the previous file's size.

readonly HUB_LIMIT=$((0x80000))
readonly PAYLOAD_ADDRESS=$((0x8000))
readonly PAYLOAD_LIMIT=$((HUB_LIMIT - PAYLOAD_ADDRESS - 4))
readonly WRITER_LIMIT=$((0x8000))
readonly WRITER_SHA256_DEFAULT=b71f5d92e6b491c7b62fdc4b80baa63cea24d3975e98d6df4e3d2e8ae1b412e4
readonly LOADP2_SUCCESS='writing _BOOT_P2.BIX...OK'

execute=0
port=${P2_PORT:-}
image=
writer=${P2_SD_WRITER:-}
loadp2=${LOADP2:-}
writer_sha256=${P2_SD_WRITER_SHA256:-$WRITER_SHA256_DEFAULT}
loadp2_sha256=${P2_LOADP2_SHA256:-}
artifact_dir=

usage()
{
  cat >&2 <<EOF
usage: $0 --port DEVICE --image RAW-BINARY --writer P2ES_sdcard.bin [options]

Options:
  --loadp2 FILE             loadp2 executable (default: \$LOADP2)
  --loadp2-sha256 SHA256    expected loadp2 digest; otherwise use toolchain.lock
  --writer-sha256 SHA256    expected writer digest (pinned default is built in)
  --artifact-dir DIR        execution evidence directory
  --execute                 enable reset, serial access, and destructive SD write

Dry-run is the default. Execution also requires P2_HIL=1,
P2_ALLOW_RESET=1, P2_ALLOW_SD_WRITE=1, and P2_ALLOW_SD_DESTRUCTIVE=1.
The writer deletes and recreates root _BOOT_P2.BIX; it does not format the card.
EOF
  exit 2
}

need_value()
{
  [[ $# -ge 2 && -n "$2" ]] || usage
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute)
      execute=1
      ;;
    --port)
      need_value "$@"
      shift
      port=$1
      ;;
    --image)
      need_value "$@"
      shift
      image=$1
      ;;
    --writer)
      need_value "$@"
      shift
      writer=$1
      ;;
    --loadp2)
      need_value "$@"
      shift
      loadp2=$1
      ;;
    --loadp2-sha256)
      need_value "$@"
      shift
      loadp2_sha256=$1
      ;;
    --writer-sha256)
      need_value "$@"
      shift
      writer_sha256=$1
      ;;
    --artifact-dir)
      need_value "$@"
      shift
      artifact_dir=$1
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "ERROR: unknown option $1" >&2
      usage
      ;;
  esac
  shift
done

[[ -n "$port" ]] || { echo "ERROR: --port DEVICE is required" >&2; exit 2; }
[[ -n "$image" ]] || { echo "ERROR: --image RAW-BINARY is required" >&2; exit 2; }
[[ -n "$writer" ]] || { echo "ERROR: --writer P2ES_sdcard.bin is required" >&2; exit 2; }
[[ -n "$loadp2" && -x "$loadp2" ]] ||
  { echo "ERROR: loadp2 executable is unavailable" >&2; exit 2; }
[[ -f "$image" && -s "$image" ]] ||
  { echo "ERROR: image is missing or empty: $image" >&2; exit 2; }
[[ -f "$writer" && -s "$writer" ]] ||
  { echo "ERROR: SD writer is missing or empty: $writer" >&2; exit 2; }

absolute_path()
{
  local path=$1
  (cd "$(dirname "$path")" && printf '%s/%s\n' "$PWD" "$(basename "$path")")
}

sha256_file()
{
  shasum -a 256 "$1" | awk '{print $1}'
}

valid_sha256()
{
  [[ "$1" =~ ^[0-9a-f]{64}$ ]]
}

image=$(absolute_path "$image")
writer=$(absolute_path "$writer")
loadp2=$(absolute_path "$loadp2")
for path in "$image" "$writer"; do
  [[ "$path" != *,* ]] ||
    { echo "ERROR: loadp2 multi-file paths cannot contain a comma: $path" >&2; exit 2; }
done

image_size=$(wc -c < "$image" | tr -d ' ')
writer_size=$(wc -c < "$writer" | tr -d ' ')
image_sha256=$(sha256_file "$image")
writer_actual_sha256=$(sha256_file "$writer")
loadp2_actual_sha256=$(sha256_file "$loadp2")

[[ $image_size -le $PAYLOAD_LIMIT ]] ||
  { echo "ERROR: image is $image_size bytes; the staged writer limit is $PAYLOAD_LIMIT bytes" >&2; exit 2; }
[[ $writer_size -le $WRITER_LIMIT ]] ||
  { echo "ERROR: SD writer is $writer_size bytes; it must fit below Hub 0x8000" >&2; exit 2; }

magic=$(od -An -N4 -tx1 "$image" | tr -d ' \n')
[[ "$magic" != 7f454c46 ]] ||
  { echo "ERROR: _BOOT_P2.BIX must be a raw P2 binary, not ELF" >&2; exit 2; }

valid_sha256 "$writer_sha256" ||
  { echo "ERROR: --writer-sha256 must be 64 lowercase hexadecimal characters" >&2; exit 2; }
[[ "$writer_actual_sha256" == "$writer_sha256" ]] ||
  { echo "ERROR: SD writer SHA-256 does not match the expected pinned digest" >&2; exit 2; }

if [[ -z "$loadp2_sha256" ]]; then
  lock=${P2_TOOLCHAIN_LOCK:-$ROOT/tools/p2/toolchain.lock}
  [[ -f "$lock" ]] ||
    { echo "ERROR: --loadp2-sha256 is required when toolchain.lock is unavailable" >&2; exit 2; }
  loadp2_sha256=$(sed -n "s|^sha256=\([0-9a-f][0-9a-f]*\)  $loadp2$|\1|p" "$lock")
fi
valid_sha256 "$loadp2_sha256" ||
  { echo "ERROR: loadp2 is not pinned; pass --loadp2-sha256 from a trusted checksum file" >&2; exit 2; }
[[ "$loadp2_actual_sha256" == "$loadp2_sha256" ]] ||
  { echo "ERROR: loadp2 SHA-256 does not match the expected digest" >&2; exit 2; }

help=$({ "$loadp2" '-?' 2>&1 || true; })
grep -q -- 'In -CHIP mode' <<<"$help" ||
  { echo "ERROR: loadp2 does not advertise -CHIP multi-file loading" >&2; exit 2; }
grep -q '@ADDR=file' <<<"$help" ||
  { echo "ERROR: loadp2 does not advertise @ADDR=file loading" >&2; exit 2; }

python=${P2_PYTHON:-python3}
staged_payload=
cleanup_staged_payload()
{
  if [[ -n "$staged_payload" ]]; then
    rm -f -- "$staged_payload"
  fi
}
trap cleanup_staged_payload EXIT

staged_payload=$(mktemp "${TMPDIR:-/tmp}/p2-sd-boot-payload.XXXXXX")
[[ "$staged_payload" != *,* ]] ||
  { echo "ERROR: temporary payload path cannot contain a comma: $staged_payload" >&2; exit 2; }

staged_metadata=$("$python" - "$image" "$staged_payload" <<'PY'
import hashlib
import pathlib
import struct
import sys

source = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
image_size = source.stat().st_size
padding = b"\0" * (-image_size % 4)
image_digest = hashlib.sha256()
staged_digest = hashlib.sha256()
copied = 0

with source.open("rb") as input_file, destination.open("wb") as output_file:
    prefix = struct.pack("<I", image_size)
    output_file.write(prefix)
    staged_digest.update(prefix)
    while True:
        chunk = input_file.read(65536)
        if not chunk:
            break
        output_file.write(chunk)
        image_digest.update(chunk)
        staged_digest.update(chunk)
        copied += len(chunk)
    output_file.write(padding)
    staged_digest.update(padding)

if copied != image_size:
    raise SystemExit("image size changed while staging")

print(4 + image_size + len(padding), staged_digest.hexdigest(),
      image_digest.hexdigest())
PY
)
read -r staged_expected_size staged_expected_sha256 staged_image_sha256 \
  <<<"$staged_metadata"
staged_size=$(wc -c < "$staged_payload" | tr -d ' ')
staged_sha256=$(sha256_file "$staged_payload")

[[ "$staged_expected_size" =~ ^[0-9]+$ &&
   "$staged_expected_sha256" =~ ^[0-9a-f]{64}$ &&
   "$staged_image_sha256" =~ ^[0-9a-f]{64}$ ]] ||
  { echo "ERROR: staged payload metadata is malformed" >&2; exit 2; }
[[ "$staged_size" == "$staged_expected_size" ]] ||
  { echo "ERROR: staged payload size verification failed" >&2; exit 2; }
[[ "$staged_sha256" == "$staged_expected_sha256" ]] ||
  { echo "ERROR: staged payload SHA-256 verification failed" >&2; exit 2; }
[[ "$staged_image_sha256" == "$image_sha256" ]] ||
  { echo "ERROR: image changed while the staged payload was created" >&2; exit 2; }
[[ $staged_size -le $((HUB_LIMIT - PAYLOAD_ADDRESS)) ]] ||
  { echo "ERROR: staged payload does not fit below the Hub RAM limit" >&2; exit 2; }

loader_baud=${P2_LOADER_BAUD:-2000000}
console_baud=${P2_CONSOLE_BAUD:-230400}
writer_clock_hz=${P2_SD_WRITER_CLOCK_HZ:-160000000}
writer_clock_mode=${P2_SD_WRITER_CLOCK_MODE:-010007f8}
recv_timeout_ms=${P2_SD_BOOT_RECV_TIMEOUT_MS:-120000}

for value_name in loader_baud console_baud writer_clock_hz recv_timeout_ms; do
  value=${!value_name}
  [[ "$value" =~ ^[0-9]+$ && $value -gt 0 ]] ||
    { echo "ERROR: $value_name must be a positive integer" >&2; exit 2; }
done
[[ "$writer_clock_mode" =~ ^[0-9A-Fa-f]{1,8}$ ]] ||
  { echo "ERROR: P2_SD_WRITER_CLOCK_MODE must be one to eight hexadecimal digits" >&2; exit 2; }

filespec="@0=$writer,@8000=$staged_payload"
recv_script="recvtimeout($recv_timeout_ms) recv(SD Updater) recv(Card mounted) recv($LOADP2_SUCCESS)"
command=("$loadp2" -p "$port" -l "$loader_baud" -b "$console_baud"
         -f "$writer_clock_hz" -m "$writer_clock_mode" -PATCH
         -DTR -ZERO -CHIP -v -e "$recv_script" "$filespec")

printf 'sd_write_command='
printf '%q ' "${command[@]}"
printf '\n'
printf 'sd_output_name=_BOOT_P2.BIX\n'
printf 'image_size=%s\nimage_sha256=%s\n' "$image_size" "$image_sha256"
printf 'staged_payload_size=%s\nstaged_payload_sha256=%s\n' \
  "$staged_size" "$staged_sha256"
printf 'staged_payload_format=le32-image-size+image+zero-pad-to-4\n'
printf 'writer_sha256=%s\nloadp2_sha256=%s\n' "$writer_actual_sha256" "$loadp2_actual_sha256"
printf 'sd_pins=P58:MISO,P59:MOSI,P60:nCS,P61:CLK\n'

if [[ $execute -eq 0 ]]; then
  echo "DRY-RUN: no serial open, reset, delete, or SD write was performed"
  echo "BOOT-UNVERIFIED: after writing, use SD-only (OFF,OFF,ON) and verify-sd-boot.py"
  exit 0
fi

[[ "${P2_HIL:-0}" == 1 ]] ||
  { echo "ERROR: P2_HIL=1 is required" >&2; exit 2; }
[[ "${P2_ALLOW_RESET:-0}" == 1 ]] ||
  { echo "ERROR: P2_ALLOW_RESET=1 is required because loadp2 uses -DTR" >&2; exit 2; }
[[ "${P2_ALLOW_SD_WRITE:-0}" == 1 ]] ||
  { echo "ERROR: P2_ALLOW_SD_WRITE=1 is required" >&2; exit 2; }
[[ "${P2_ALLOW_SD_DESTRUCTIVE:-0}" == 1 ]] ||
  { echo "ERROR: P2_ALLOW_SD_DESTRUCTIVE=1 is required because root _BOOT_P2.BIX is deleted and recreated" >&2; exit 2; }
[[ -c "$port" ]] ||
  { echo "ERROR: serial device is absent: $port" >&2; exit 2; }

if command -v timeout >/dev/null 2>&1; then
  timeout_command=(timeout "${P2_SD_BOOT_TIMEOUT:-150}")
elif command -v gtimeout >/dev/null 2>&1; then
  timeout_command=(gtimeout "${P2_SD_BOOT_TIMEOUT:-150}")
else
  echo "ERROR: timeout or gtimeout is required" >&2
  exit 2
fi

lock_file=${P2_LOCK_FILE:-/tmp/nuttx-p2-hil.lock}
mkdir -p "$(dirname "$lock_file")"
exec 9>"$lock_file"
flock -n 9 || { echo "ERROR: P2 board lock is busy: $lock_file" >&2; exit 2; }
owners=$(lsof -t "$port" 2>/dev/null || true)
[[ -z "$owners" ]] ||
  { echo "ERROR: serial port is owned by PID(s): $owners" >&2; exit 2; }

if [[ -z "$artifact_dir" ]]; then
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  artifact_dir=$ROOT/artifacts/hil/${stamp}-sd-boot-write
fi
mkdir -p "$(dirname "$artifact_dir")"
artifact_dir=$(absolute_path "$artifact_dir")
[[ ! -e "$artifact_dir" ]] ||
  { echo "ERROR: artifact directory already exists: $artifact_dir" >&2; exit 2; }
mkdir -p "$artifact_dir"
started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)

printf '%q ' "${command[@]}" > "$artifact_dir/command.txt"
printf '\n' >> "$artifact_dir/command.txt"

SD_ARTIFACT=$artifact_dir SD_IMAGE=$image SD_IMAGE_SIZE=$image_size \
SD_IMAGE_SHA256=$image_sha256 SD_WRITER=$writer \
SD_WRITER_SHA256=$writer_actual_sha256 SD_LOADP2=$loadp2 \
SD_LOADP2_SHA256=$loadp2_actual_sha256 SD_PORT=$port \
SD_STAGED_SIZE=$staged_size SD_STAGED_SHA256=$staged_sha256 \
SD_LOADER_BAUD=$loader_baud SD_CONSOLE_BAUD=$console_baud \
SD_STARTED_UTC=$started_utc \
"$python" - <<'PY'
import json
import os
import pathlib

artifact = pathlib.Path(os.environ["SD_ARTIFACT"])
value = {
    "action": "sd-boot-write",
    "status": "RUNNING",
    "boot_status": "UNVERIFIED",
    "port": os.environ["SD_PORT"],
    "output_filename": "_BOOT_P2.BIX",
    "image": os.environ["SD_IMAGE"],
    "image_size": int(os.environ["SD_IMAGE_SIZE"]),
    "image_sha256": os.environ["SD_IMAGE_SHA256"],
    "staged_payload_format": "le32-image-size+image+zero-pad-to-4",
    "staged_payload_size": int(os.environ["SD_STAGED_SIZE"]),
    "staged_payload_sha256": os.environ["SD_STAGED_SHA256"],
    "writer": os.environ["SD_WRITER"],
    "writer_sha256": os.environ["SD_WRITER_SHA256"],
    "loadp2": os.environ["SD_LOADP2"],
    "loadp2_sha256": os.environ["SD_LOADP2_SHA256"],
    "loader_baud": int(os.environ["SD_LOADER_BAUD"]),
    "console_baud": int(os.environ["SD_CONSOLE_BAUD"]),
    "pins": {"P58": "MISO", "P59": "MOSI", "P60": "nCS", "P61": "CLK"},
    "fragmentation_verified": False,
    "started_utc": os.environ["SD_STARTED_UTC"],
    "ended_utc": None,
}
(artifact / "status.json").write_text(
    json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY

set +e
"${timeout_command[@]}" "${command[@]}" 2>&1 | tee "$artifact_dir/loader.log"
pipeline_status=("${PIPESTATUS[@]}")
set -e
result=${pipeline_status[0]}

failure=
if [[ ${pipeline_status[1]} -ne 0 ]]; then
  failure="could not preserve loadp2 output"
  if [[ $result -eq 0 ]]; then
    result=2
  fi
elif [[ $result -ne 0 ]]; then
  failure="loadp2 exited with status $result"
else
  set +e
  grep -Fq 'ERROR:' "$artifact_dir/loader.log"
  error_scan=$?
  set -e
  if [[ $error_scan -eq 0 ]]; then
    failure="loadp2 or its receive script reported an error"
  elif [[ $error_scan -ne 1 ]]; then
    failure="could not inspect preserved loadp2 output"
    result=2
  fi
fi

SD_ARTIFACT=$artifact_dir SD_RESULT=$result SD_FAILURE=$failure \
SD_ENDED_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
"$python" - <<'PY'
import json
import os
import pathlib

path = pathlib.Path(os.environ["SD_ARTIFACT"]) / "status.json"
value = json.loads(path.read_text(encoding="utf-8"))
failure = os.environ["SD_FAILURE"]
value.update(
    {
        "status": "FAIL" if failure else "PASS",
        "exit_code": int(os.environ["SD_RESULT"]),
        "failure": failure or None,
        "boot_status": "UNVERIFIED",
        "fragmentation_verified": False,
        "ended_utc": os.environ["SD_ENDED_UTC"],
    }
)
path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

if [[ -n "$failure" ]]; then
  echo "ERROR: $failure; artifact: $artifact_dir" >&2
  exit 1
fi

echo "SD-WRITE-PASS: root _BOOT_P2.BIX was closed with the requested byte count"
echo "BOOT-UNVERIFIED: set FLASH=OFF, up=OFF, down=ON for SD-only mode, then run verify-sd-boot.py"
echo "The ROM requires an unfragmented FAT32 root file; this write alone does not prove contiguity"
echo "P2 SD boot write artifact: $artifact_dir"
