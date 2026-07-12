P2 hardware-in-the-loop status
==============================

Status: ACTIVE BRING-UP.  Hardware claims in this file require a linked log or
artifact.  Build success is not hardware success.

Repository starting point
-------------------------

The checkout was initially inspected on 2026-07-12 on branch ``master`` at
``f5e3bb24595b78c5e329d13b1ad559a294ed3ef9``.  It was clean and did not
contain the P2 port.  PR #2 was fetched without rewriting history.  Its head
matched the expected commit exactly:

``765d073a89599a5d1d96fbc84ad4891e3f5b4aa4``

The active HIL branch is ``codex/p2-hil-finish``, created directly from that
PR #2 head.  PR #2 is stacked on PR #1 commit
``39cc55135fd24f02006e56f9fc1f0476edea1888``.

Hardware endpoint
-----------------

* Target: Parallax P2 Edge Module with 32 MB RAM, P2-EC32MB Rev B.
* Host serial path: ``/dev/cu.usbserial-P97cvdxp``.
* USB adapter identity: Parallax ``PropPlug``, USB VID ``0x0403``, PID
  ``0x6015``, device serial ``P97cvdxp``.
* Console target: P2 P62/P63 at 230400 baud.
* Loader target: 2000000 baud.
* No process owned the serial device at initial inspection.
* The serial-number-bearing macOS callout path is used because macOS does not
  provide a Linux-style ``/dev/serial/by-id`` path.
* Reset method: loadp2 DTR through the PropPlug: PASS on physical hardware.
* The board's current physical switch position accepts DTR reset and the ROM
  serial loader.  The printed switch label has not been visually confirmed;
  independent flash-boot behavior remains untested.

Safety gates
------------

The untracked ``.p2-hil.env`` file contains the local endpoint and tool paths.
Flash write, flash erase, destructive microSD, and external loopback gates are
all zero.  No such operation is authorized by the current task.

Current verified state
----------------------

* PR #2 lineage and expected SHA: PASS.
* Required P2 source and documentation directories: PASS on PR #2.
* Clean initial worktree: PASS.
* Serial node present and unowned: PASS.
* PropPlug USB identity: PASS.
* Local ``kconfig-conf`` executable: FOUND at
  ``/Users/fred/.local/nuttx-tools/kconfig-frontends/bin/kconfig-conf``.
* Sibling ``nuttx-apps`` checkout: PASS at
  ``62b7e955300b6dafa4f36d391474d3c8925b8106``.
* Pinned p2llvm compiler and LLD: PASS at p2llvm
  ``bdcefcce7860b2232c06f35726fea679a3a7309c`` with llvm-project
  ``72a9bb1ef2656d9953d1f41a8196d425ff2ab0b1``.  P2LLVM libc was not
  built or installed.
* Pinned libp2: PASS.  The pinned source has unused ``stdio.h`` and
  ``math.h`` includes in two builtins, so two build-only empty shims are used
  while compiling libp2.  They are not visible to LLVM or NuttX.
* Pinned FlexProp ``flexspin`` and ``loadp2``: PASS at FlexProp
  ``858f51c4a24e7ae0f6cbc78f625c731083ad304f`` and loadp2
  ``c20afedd4253d09da449fa740f8d4304481fc560``.
* Hash-locked Python HIL environment: PASS with pyserial 3.5 and pyelftools
  0.32.
* ``tools/p2/bootstrap-local.sh`` clean rerun and target-object probe: PASS.
* Standalone native-p2 hello ELF: COMPILED and ELF-VERIFIED.  The first
  ``PT_LOAD`` physical address is zero, ``main`` is at ``0x0a00``, initial
  PTRA is ``0x78000``, and there are no undefined symbols.
* First standalone physical cycle: PASS.  ``.data`` and ``.bss`` markers,
  PTRA ``0x0007801c``, counter, ready, and the ``?`` serial-command response
  were captured under
  ``artifacts/hil/20260712T211011.686652Z-hello``.
* Required repeated standalone HIL gate: PASS, 10/10 consecutive DTR
  reset/RAM-load/console cycles.  Every cycle contained all seven ordered
  ``P2HELLO`` markers, one entry/reset marker, no panic or failure marker, and
  PTRA ``0x0007801c``.  Evidence is under
  ``artifacts/hil/20260712T211034.259011Z-hello``.
* Standalone ``tools/p2/load-ram.sh`` entry point: PASS with a separate
  RAM-only ELF load under ``artifacts/hil/20260712T211115Z-load-ram``.
* Serial device ownership after HIL: PASS; the loader released the port.
* NuttX runtime status: NOT TESTED; the inherited PR still contains explicit
  panic and not-implemented paths.

Next acceptance gate
--------------------

Run and inspect the p2llvm ABI probes, then define the complete saved context.
Do not integrate NuttX preemption until the asynchronous P2 CORDIC result state
used by compiler-emitted multiply/divide sequences is made safe across timer
interrupts and the standalone one-million-switch context test passes.
