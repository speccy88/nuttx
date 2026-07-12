#!/usr/bin/env bash
set -euo pipefail
python3 -m unittest discover -s tools/p2/tests -v
