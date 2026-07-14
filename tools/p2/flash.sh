#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
[[ -f "$HOME/.p2-nuttx-env" ]] && source "$HOME/.p2-nuttx-env"

execute=0
allow_dirty_build=0
port=
image=
build_artifact=
artifact_dir=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute) execute=1 ;;
    --allow-dirty-build) allow_dirty_build=1 ;;
    --port) shift; port=${1:-} ;;
    --image) shift; image=${1:-} ;;
    --build-artifact) shift; build_artifact=${1:-} ;;
    --artifact-dir) shift; artifact_dir=${1:-} ;;
    *) echo "HIL REQUIRED: usage: $0 --port DEVICE --image BINARY --build-artifact DIR [--artifact-dir DIR] [--allow-dirty-build] [--execute]"; exit 2 ;;
  esac
  shift
done

[[ -n "$port" && -n "$image" && -n "$build_artifact" ]] ||
  { echo "HIL REQUIRED: explicit --port, --image, and --build-artifact are required"; exit 2; }
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
layout=$("$python" "$ROOT/tools/p2/verify-flash-layout.py" --image "$image" \
  --require-manifest)
printf '%s\n' "$layout"
image=$(cd "$(dirname "$image")" && pwd)/$(basename "$image")
manifest=$image.json
image_size=$(wc -c < "$image" | tr -d ' ')
image_sha256=$(shasum -a 256 "$image" | awk '{print $1}')
manifest_sha256=$(shasum -a 256 "$manifest" | awk '{print $1}')
build_args=(--artifact "$build_artifact" --image "$image")
if [[ $allow_dirty_build -eq 0 ]]; then
  build_args+=(--require-clean)
fi
build_info=$("$python" "$ROOT/tools/p2/build_artifact.py" "${build_args[@]}")
printf '%s\n' "$build_info"
build_artifact=$(printf '%s\n' "$build_info" | sed -n 's/^build_artifact=//p')
build_status_sha256=$(printf '%s\n' "$build_info" | sed -n 's/^build_status_sha256=//p')
build_profile=$(printf '%s\n' "$build_info" | sed -n 's/^build_profile=//p')
build_nuttx_commit=$(printf '%s\n' "$build_info" | sed -n 's/^build_nuttx_commit=//p')
build_apps_commit=$(printf '%s\n' "$build_info" | sed -n 's/^build_apps_commit=//p')
build_clock_hz=$(printf '%s\n' "$build_info" | sed -n 's/^build_clock_hz=//p')
build_source_clean=$(printf '%s\n' "$build_info" | sed -n 's/^build_source_clean=//p')
[[ "$build_source_clean" == true || "$build_source_clean" == false ]] ||
  { echo "ERROR: build artifact did not report source cleanliness" >&2; exit 2; }
program_range=$(printf '%s\n' "$layout" | sed -n 's/^program_range=//p')
erase_range=$(printf '%s\n' "$layout" | sed -n 's/^erase_range=//p')
loader_baud=${P2_LOADER_BAUD:-2000000}
[[ "$loader_baud" =~ ^[0-9]+$ && $loader_baud -gt 0 ]] ||
  { echo "ERROR: P2_LOADER_BAUD must be a positive integer" >&2; exit 2; }
command=("$LOADP2" -p "$port" -l "$loader_baud"
         -DTR -SINGLE -FLASH -v "$image")
printf 'flash_command='
printf '%q ' "${command[@]}"
printf '\n'

if [[ $execute -eq 0 ]]; then
  echo "DRY-RUN: no serial open, reset, erase, or flash write was performed"
  exit 0
fi

[[ "${P2_HIL:-0}" == 1 ]] || { echo "ERROR: P2_HIL=1 is required" >&2; exit 2; }
[[ "${P2_ALLOW_RESET:-0}" == 1 ]] ||
  { echo "ERROR: P2_ALLOW_RESET=1 is required because loadp2 uses -DTR" >&2; exit 2; }
[[ "${P2_ALLOW_FLASH_WRITE:-0}" == 1 ]] ||
  { echo "ERROR: P2_ALLOW_FLASH_WRITE=1 is required" >&2; exit 2; }
[[ "${P2_ALLOW_FLASH_ERASE:-0}" == 1 ]] ||
  { echo "ERROR: P2_ALLOW_FLASH_ERASE=1 is required" >&2; exit 2; }
[[ "${P2_ALLOW_SD_WRITE:-0}" == 1 ]] ||
  { echo "ERROR: P2_ALLOW_SD_WRITE=1 is required because flash programming drives shared P60/P61" >&2; exit 2; }
if [[ "$build_source_clean" == false ]]; then
  [[ $allow_dirty_build -eq 1 && "${P2_ALLOW_DIRTY_BUILD:-0}" == 1 ]] ||
    { echo "ERROR: dirty development builds require --allow-dirty-build and P2_ALLOW_DIRTY_BUILD=1" >&2; exit 2; }
fi
[[ -c "$port" ]] || { echo "ERROR: serial device is absent: $port" >&2; exit 2; }

if command -v timeout >/dev/null 2>&1; then
  timeout_cmd=(timeout "${P2_FLASH_TIMEOUT:-180}")
elif command -v gtimeout >/dev/null 2>&1; then
  timeout_cmd=(gtimeout "${P2_FLASH_TIMEOUT:-180}")
else
  echo "ERROR: timeout or gtimeout is required" >&2
  exit 2
fi
settle_seconds=${P2_FLASH_SETTLE_SECONDS:-5}
[[ "$settle_seconds" =~ ^[0-9]+$ && $settle_seconds -ge 3 ]] ||
  { echo "ERROR: P2_FLASH_SETTLE_SECONDS must be an integer >= 3" >&2; exit 2; }

lock_file=${P2_LOCK_FILE:-/tmp/nuttx-p2-hil.lock}
exec 9>"$lock_file"
flock -n 9 || { echo "ERROR: P2 board lock is busy: $lock_file" >&2; exit 2; }
owners=$(lsof -t "$port" 2>/dev/null || true)
[[ -z "$owners" ]] || { echo "ERROR: serial port is owned by: $owners" >&2; exit 2; }

if [[ -z "$artifact_dir" ]]; then
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  artifact_dir=$ROOT/artifacts/hil/${stamp}-flash-program
fi
artifact_dir=$("$python" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$artifact_dir")
[[ ! -e "$artifact_dir" ]] ||
  { echo "ERROR: flash artifact already exists: $artifact_dir" >&2; exit 2; }
"$python" - "$artifact_dir" "$build_artifact" "$(dirname "$image")" <<'PY'
import pathlib
import sys

output, build, image_parent = (pathlib.Path(value).resolve() for value in sys.argv[1:])
for label, prerequisite in (("build artifact", build),
                            ("flash input directory", image_parent)):
    try:
        output.relative_to(prerequisite)
    except ValueError:
        continue
    raise SystemExit("ERROR: flash artifact cannot be inside the " + label)
PY

started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
write_status()
{
  FLASH_STATUS=$1 FLASH_EXIT_CODE=$2 FLASH_ENDED_UTC=$3 \
  FLASH_ARTIFACT_DIR=$artifact_dir FLASH_STARTED_UTC=$started_utc \
  FLASH_PORT=$port FLASH_IMAGE=$image FLASH_IMAGE_SIZE=$image_size \
  FLASH_IMAGE_SHA256=$image_sha256 FLASH_PROGRAM_RANGE=$program_range \
  FLASH_ERASE_RANGE=$erase_range FLASH_MANIFEST=$manifest \
  FLASH_MANIFEST_SHA256=$manifest_sha256 FLASH_LOADP2=$LOADP2 \
  FLASH_LOADP2_SHA256=$actual FLASH_BUILD_ARTIFACT=$build_artifact \
  FLASH_LOADER_BAUD=$loader_baud \
  FLASH_BUILD_STATUS_SHA256=$build_status_sha256 \
  FLASH_BUILD_PROFILE=$build_profile \
  FLASH_BUILD_NUTTX_COMMIT=$build_nuttx_commit \
  FLASH_BUILD_APPS_COMMIT=$build_apps_commit \
  FLASH_BUILD_CLOCK_HZ=$build_clock_hz \
  FLASH_BUILD_SOURCE_CLEAN=$build_source_clean \
  FLASH_DIRTY_BUILD_AUTHORIZED=$allow_dirty_build \
  FLASH_PROGRAM_SETTLE_SECONDS=$settle_seconds \
  "$python" - <<'PY'
import json
import os
import pathlib

path = pathlib.Path(os.environ["FLASH_ARTIFACT_DIR"]) / "status.json"
exit_text = os.environ["FLASH_EXIT_CODE"]
value = {
    "status": os.environ["FLASH_STATUS"],
    "action": "flash-program",
    "started_utc": os.environ["FLASH_STARTED_UTC"],
    "ended_utc": os.environ["FLASH_ENDED_UTC"] or None,
    "exit_code": int(exit_text) if exit_text else None,
    "port": os.environ["FLASH_PORT"],
    "image": os.environ["FLASH_IMAGE"],
    "image_size": int(os.environ["FLASH_IMAGE_SIZE"]),
    "image_sha256": os.environ["FLASH_IMAGE_SHA256"],
    "manifest": os.environ["FLASH_MANIFEST"],
    "manifest_file": "inputs/flash-input.bin.json",
    "manifest_format": "loadp2-single-flash-input-v1",
    "manifest_sha256": os.environ["FLASH_MANIFEST_SHA256"],
    "build_artifact": os.environ["FLASH_BUILD_ARTIFACT"],
    "build_artifact_copy": "inputs/build",
    "build_status_sha256": os.environ["FLASH_BUILD_STATUS_SHA256"],
    "build_profile": os.environ["FLASH_BUILD_PROFILE"],
    "build_nuttx_commit": os.environ["FLASH_BUILD_NUTTX_COMMIT"],
    "build_apps_commit": os.environ["FLASH_BUILD_APPS_COMMIT"],
    "build_source_clean": os.environ["FLASH_BUILD_SOURCE_CLEAN"] == "true",
    "dirty_build_authorized": os.environ["FLASH_DIRTY_BUILD_AUTHORIZED"] == "1",
    "board_clock_hz": int(os.environ["FLASH_BUILD_CLOCK_HZ"]),
    "program_settle_seconds": int(os.environ["FLASH_PROGRAM_SETTLE_SECONDS"]),
    "program_range": os.environ["FLASH_PROGRAM_RANGE"],
    "erase_range": os.environ["FLASH_ERASE_RANGE"],
    "boot_partition_range": "[0x00000000,0x00080000)",
    "loadp2": os.environ["FLASH_LOADP2"],
    "loadp2_sha256": os.environ["FLASH_LOADP2_SHA256"],
    "loadp2_copy": "inputs/loadp2",
    "loader_baud": int(os.environ["FLASH_LOADER_BAUD"]),
    "loader_command_file": "command.json",
    "layout_file": "layout.txt",
    "flash_write_gate": True,
    "flash_erase_gate": True,
    "reset_gate": True,
    "shared_sd_write_gate": True,
}
temporary = path.with_suffix(".json.tmp")
temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
temporary.replace(path)
(path.parent / "metadata.json").write_text(
    json.dumps(value, indent=2, sort_keys=True) + "\n"
)
PY
}

finalized=0
finalize_on_exit()
{
  result=$?
  if [[ $finalized -eq 0 ]]; then
    [[ $result -ne 0 ]] || result=1
    set +e
    write_status FAIL "$result" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  fi
}

mkdir "$artifact_dir"
trap finalize_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
write_status RUNNING "" ""
mkdir "$artifact_dir/inputs"
cp "$image" "$artifact_dir/inputs/flash-input.bin"
cp "$manifest" "$artifact_dir/inputs/flash-input.bin.json"
cp -R "$build_artifact" "$artifact_dir/inputs/build"
cp "$lock" "$artifact_dir/inputs/toolchain.lock"
sealed_loader=$artifact_dir/inputs/loadp2
cp "$LOADP2" "$sealed_loader"
sealed_loader_sha256=$(shasum -a 256 "$sealed_loader" | awk '{print $1}')
[[ -x "$sealed_loader" && "$sealed_loader_sha256" == "$actual" ]] ||
  { echo "ERROR: sealed loadp2 copy is not executable or changed" >&2; exit 2; }
cp "$ROOT/tools/p2/flash.sh" "$artifact_dir/inputs/flash.sh"
cp "$ROOT/tools/p2/verify-flash-layout.py" "$artifact_dir/inputs/verify-flash-layout.py"
printf '%s\n' "$layout" > "$artifact_dir/layout.txt"
sealed_image=$artifact_dir/inputs/flash-input.bin
command=("$sealed_loader" -p "$port" -l "$loader_baud"
         -DTR -SINGLE -FLASH -v "$sealed_image")
printf '%q ' "${command[@]}" > "$artifact_dir/command.txt"
printf '\n' >> "$artifact_dir/command.txt"
"$python" - "$artifact_dir/command.json" "$loader_baud" "${command[@]}" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = {"loader_baud": int(sys.argv[2]), "argv": sys.argv[3:]}
path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
PY

set +e
"${timeout_cmd[@]}" "${command[@]}" \
  > "$artifact_dir/loader.stdout" 2> "$artifact_dir/loader.stderr"
result=$?
set -e
[[ ! -s "$artifact_dir/loader.stdout" ]] || cat "$artifact_dir/loader.stdout"
[[ ! -s "$artifact_dir/loader.stderr" ]] || cat "$artifact_dir/loader.stderr" >&2
if [[ $result -eq 0 ]]; then
  sleep "$settle_seconds"
  ended_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  write_status PASS "$result" "$ended_utc"
  finalized=1
  echo "P2 flash program artifact: $artifact_dir"
else
  ended_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  write_status FAIL "$result" "$ended_utc"
  finalized=1
  echo "ERROR: flash programming failed with status $result; artifact: $artifact_dir" >&2
fi
exit "$result"
