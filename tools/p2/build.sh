#!/usr/bin/env bash
set -euo pipefail
cfg=${1:-nsh}
./tools/configure.sh -a ../apps "p2-ec32mb:${cfg}"
make olddefconfig
make -j"${NPROC:-$(nproc)}"
