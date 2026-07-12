#!/usr/bin/env bash
set -euo pipefail
if [[ ${P2_HIL:-0} != 1 || ${1:-} != --execute || -z ${P2_PORT:-} ]]; then
  echo "HIL REQUIRED: no physical P2 target is available in this environment"
  exit 2
fi
echo "DRAFTED: RAM load gate passed; call pinned loadp2 locally after serial identity is verified"
