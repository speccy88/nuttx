HIL handoff
===========

Status: DRAFTED.

First local commands: identify serial by-id path; run ``./tools/p2/bootstrap-cloud.sh``; run p2llvm hello-world with loadp2; run ``./tools/p2/build.sh nsh``; run ``P2_HIL=1 P2_PORT=/dev/serial/by-id/... ./tools/p2/load-ram.sh --execute``.

Bring-up sequence: verify serial identity, DIP switches, hello-world, P62/P63 console, RAM image load, earliest marker, .data/.bss, periodic counter interrupt, register preservation, two-task preemption, NSH prompt, basic NSH commands, ostest matrix, flash JEDEC, partition protection, flash image program/verify, independent reset boot, flash filesystem, microSD, alternating flash/SD, Smart Pin loopbacks, PSRAM, and only then SMP. Preserve command, expected marker, timeout, log, failure cause, and diagnostic for each step. Use series resistors for loopbacks and never wire two push-pull outputs directly.
