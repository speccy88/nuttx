Pinned dependencies
===================

Status: DRAFTED with BLOCKED entries where the cloud image lacks executable
host tools.  ``tools/p2/bootstrap-cloud.sh`` now writes
``tools/p2/dependencies.lock`` and ``~/.p2-nuttx-env`` without committing any
generated binaries.

Current recorded revisions
--------------------------

* NuttX task branch commit: ``39cc55135fd24f02006e56f9fc1f0476edea1888``.
* nuttx-apps: ``62b7e955300b6dafa4f36d391474d3c8925b8106`` from the cached
  apps checkout.
* p2llvm source cache: ``bdcefcce7860b2232c06f35726fea679a3a7309c``.
* p2llvm llvm-project submodule/source tree: ``bdcefcce7860b2232c06f35726fea679a3a7309c`` as recorded from the current cache.
* loadp2: ``BLOCKED_not_available`` in this cloud cache.
* kconfig frontend: ``BLOCKED_missing`` for ``kconfig-conf`` in this cloud
  image; ``tools/p2/build.sh bringup`` reaches olddefconfig and then fails
  with ``kconfig-conf: command not found``.
* P2 compiler executable: ``BLOCKED_missing`` at
  ``$P2LLVM_ROOT/bin/clang``; ABI probes therefore refuse to fabricate
  COMPILED evidence.

Compiler and linker contract
----------------------------

The P2 board Make.defs uses the installed p2llvm clang driver, not raw
``ld.lld``, for final linking.  The target and common flags are::

  --target=p2 -fno-jump-tables -fno-builtin -fno-common \
    -ffunction-sections -fdata-sections

Final link flags are::

  --target=p2 -nostdlib -Wl,--gc-sections -Wl,-Map=nuttx.map
