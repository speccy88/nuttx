Pinned dependencies
===================

Status: DRAFTED/BLOCKED where unavailable.

See ``tools/p2/dependencies.lock``. Cloud bootstrap creates cache locations for nuttx-apps, p2llvm, and loadp2 but does not commit generated toolchain binaries. If p2llvm cannot be built, rerun ``./tools/p2/bootstrap-cloud.sh`` locally and preserve full logs.
