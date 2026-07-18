# P2 draft-port workspace notes

* P2 source lives in `arch/p2`, board support in `boards/p2/p2x8c4m64p/p2-ec32mb`, docs in `Documentation/platforms/p2`, and host/HIL tools in `tools/p2`.
* Build wrappers: `./tools/p2/bootstrap-cloud.sh`, `./tools/p2/build.sh nsh|ostest|storage|smartpins|unified|unified-hil`, `./tools/p2/run-host-tests.sh`, and `./tools/p2/report-memory.sh`.
* This is a cloud first draft: use COMPILED, HOST-TESTED, STATICALLY-VERIFIED, DRAFTED, HIL-REQUIRED, and BLOCKED labels honestly.
* Never add fake success hooks. Unimplemented optional operations must be Kconfig-excluded or fail explicitly such as `-ENOSYS`.
* P2 external PSRAM is not hardware byte-addressable Hub memory.  The legacy profile must not use it for heap, stacks, code, or C objects; only the opt-in `unified` profile may expose dynamic C objects through its compiler/runtime tagged-pointer ABI.  Code, globals, kernel objects, and every task stack remain in Hub RAM.
* HIL scripts must default to dry-run and must not touch serial, flash, reset, or SD unless explicitly enabled.
