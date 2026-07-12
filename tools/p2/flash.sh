#!/usr/bin/env bash
set -euo pipefail
if [[ ${P2_HIL:-0} != 1 || ${P2_ALLOW_FLASH_WRITE:-0} != 1 || ${1:-} != --execute || -z ${P2_PORT:-} ]]; then
  echo "HIL REQUIRED: no physical P2 target is available in this environment"
  exit 2
fi
echo "DRAFTED: flash gate passed; implement local loadp2 flash command only after boot format is verified"
