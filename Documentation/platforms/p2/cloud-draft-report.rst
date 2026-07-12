Cloud draft report
==================

Status summary
--------------

* Architecture and board selection: DRAFTED.
* p2llvm build: BLOCKED in this draft; reproducible bootstrap placeholder added.
* ABI probes: DRAFTED, not compiled.
* NSH/ostest/storage/smartpins target builds: BLOCKED pending toolchain/build integration cleanup.
* Host tests: HOST-TESTED when ``./tools/p2/run-host-tests.sh`` passes.
* HIL scripts: DRAFTED and refuse hardware actions by default.

Major risks
-----------

Context switching, interrupt return, PTRA upward stack integration, linker script, and low-level serial are not proven. All runtime features are HIL-REQUIRED.

Validation log from this cloud run
----------------------------------

HOST-TESTED:

* ``./tools/p2/run-host-tests.sh``: 7 unittest cases passed (flash layout, Hub overflow, storage arbiter, pin manager, clock/tick/counter arithmetic, HIL log parser, destructive flash refusal).
* ``git diff --check``: no whitespace errors.
* ``./tools/p2/verify-flash-layout.py``: flash layout validates.

BLOCKED:

* ``git clone --depth 1 https://github.com/apache/nuttx-apps ../apps`` failed with ``CONNECT tunnel failed, response 403`` in this cloud network, so apps commit and ostest source enumeration are not pinned here.
* ``make olddefconfig`` after ``./tools/configure.sh -a ../apps p2-ec32mb:nsh`` failed with ``kconfig-conf: command not found``. The selected board links were generated, so board/arch discovery progressed, but this environment lacks the kconfig frontend executable.
* p2llvm was not built in this run. ``tools/p2/bootstrap-cloud.sh`` records a reproducible placeholder and cache locations.

Acceptance criteria assessment
------------------------------

1. Architecture appears in configuration: DRAFTED.
2. Board appears and is selectable: DRAFTED; configure reached symlink generation.
3. p2llvm: BLOCKED, not cloned/built.
4. ABI probes: DRAFTED.
5. Context frame: DRAFTED with exact offsets in source/docs.
6. NSH build: BLOCKED by host kconfig/toolchain.
7. Ostest build: BLOCKED by apps clone/kconfig/toolchain.
8. Storage build: BLOCKED by kconfig/toolchain.
9. Smartpins build: BLOCKED by kconfig/toolchain.
10. Host tests: HOST-TESTED.
11. Linker map: BLOCKED; no target link.
12. No runtime-critical fake success: DRAFTED review required; unresolved operations fail or panic rather than pretending to work.
13. HIL scripts refuse hardware actions by default: HOST-TESTED.
14. Unverified features are HIL-REQUIRED in docs.
15. Local next-step commands are documented in ``hil-handoff.rst``.

Hostile follow-up review fixes
------------------------------

A second review removed or tightened the most misleading cloud-draft paths:

* ``up_timer_initialize()`` now returns ``-ENOSYS`` rather than successful completion because the P2 counter interrupt is not armed.
* ``up_irqinitialize()`` now returns ``-ENOSYS`` rather than claiming an interrupt controller is initialized; ``up_enable_irq()`` panics if used before the real PASM2 path exists.
* Python HIL entry points are now valid Python wrappers instead of shell scripts with ``.py`` names.
* Stack setup no longer delegates to an unverified helper and explicitly records the upward-stack PTRA assumption.
* Tool wrappers now separate build, host-test, bootstrap, flash, and RAM-load behavior instead of using one basename-switch script for every action.

Remaining BLOCKED items after hostile review
--------------------------------------------

* Real context switching and full-context restore remain deliberately panic paths until p2llvm assembly probes and PASM2 interrupt return are implemented.
* The architecture still does not produce a linked ELF in this cloud environment because the Kconfig frontend and p2llvm toolchain are unavailable.
* ``p2_getsp()`` is not trusted for stack diagnostics; it is marked HIL-REQUIRED until inline PASM2 is derived from actual compiler output.
