#!/usr/bin/env bash
# Build a non-destructive index over the immutable per-run HIL bundles.

set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
ARTIFACT_ROOT=${1:-"$REPO_ROOT/artifacts/hil"}

exec python3 "$SCRIPT_DIR/artifact_index.py" \
  --root "$ARTIFACT_ROOT" \
  --json "$ARTIFACT_ROOT/index.json" \
  --markdown "$ARTIFACT_ROOT/index.md"
