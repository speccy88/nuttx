Cloud draft report
==================

Starting point
--------------

* Starting branch: ``work``.
* Starting SHA: ``39cc55135fd24f02006e56f9fc1f0476edea1888``.
* The checkout contains ``arch/p2/``, ``boards/p2/p2x8c4m64p/p2-ec32mb/``,
  ``Documentation/platforms/p2/``, and ``tools/p2/``; this is PR #1 work, not
  a restart from master.

Changes in this continuation
----------------------------

* Added ``p2-ec32mb:bringup`` so the requested core build wrapper has a real
  configuration directory.
* Corrected the P2 board Make.defs to use ``--target=p2``, add
  ``-fno-jump-tables``, and use the clang driver for final links instead of
  raw ``ld.lld``.
* Replaced the ABI-probe placeholder with a script that generates and compiles
  broad C probes at ``-O0``, ``-Os``, and ``-O2`` when p2llvm is present.
* Reworked ``tools/p2/bootstrap-cloud.sh`` so it records pinned/cached
  dependency state, writes ``~/.p2-nuttx-env``, and can fetch/build toolchains
  when explicitly enabled.
* Reworked ``tools/p2/build.sh`` to preserve logs, configs, ELFs, maps,
  symbols, sections, sizes, disassembly, and verifier output under
  ``artifacts/cloud-p2/<config>/``.

Validation log
--------------

HOST-TESTED:

* ``./tools/p2/run-host-tests.sh``: 7 unittest cases passed.
* ``P2_BOOTSTRAP_FETCH=0 ./tools/p2/bootstrap-cloud.sh``: dependency lock was
  regenerated without downloading or committing generated binaries.

BLOCKED:

* ``./tools/p2/run-abi-probes.sh``: p2llvm clang is absent at
  ``/root/.cache/p2-nuttx/p2llvm/install/bin/clang``.
* ``./tools/p2/build.sh bringup``: configuration reaches olddefconfig, then
  fails with ``/usr/bin/bash: line 1: kconfig-conf: command not found``.
* bringup, NSH, ostest, storage, and smartpins ELFs are therefore not linked in
  this cloud image.

Remaining mandatory blockers
----------------------------

* Real PASM2 interrupt entry/return and context switching remain DRAFTED and
  HIL-REQUIRED.
* ``up_irqinitialize()`` and ``up_timer_initialize()`` intentionally do not
  claim success until the real P2 interrupt/timer path exists.
* ``p2_lowputc()``, clock setup, the context frame, and upward PTRA stack
  diagnostics require p2llvm compilation plus hardware validation before any
  runtime PASS claim.
