#!/usr/bin/env bash
set -euo pipefail
mkdir -p artifacts/p2
cp -f .config nuttx.map nuttx artifacts/p2/ 2>/dev/null || true
echo "DRAFTED: collected available P2 artifacts into artifacts/p2"
