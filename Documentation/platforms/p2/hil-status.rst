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
* Preemption-safe integer lowering: PASS with the downstream
  ``p2llvm-preempt-safe-integer.patch``.  The patched compiler SHA-256 is
  ``71086f5eb8e1bf779201e04008ece0fd41513bca8b5b1792c123c6a7671e8457``.
  Compiler-generated multiply, divide, and remainder operations use ordinary
  Hub-call relocations to software helpers instead of the asynchronous Q
  pipeline.  This is required because the P2 CORDIC result state is per-cog
  and cannot be saved in a task context if a timer interrupt separates a Q
  operation from GETQX or GETQY.
* High-half multiply and constant-division lowering: PASS OFFLINE.  LLVM has
  no libcall legalization for ``MULHS`` or ``MULHU``; the downstream backend
  now expands them with exact Q-free limb arithmetic and keeps non-power-of-
  two constant division on the direct software-helper path.  The former
  ``gmtime_r`` ``i64 mulhs`` selector crash is fixed.  Signed and unsigned
  32/64-bit high products and overflow probes pass at ``-O0``, ``-Os``, and
  ``-O2``.
* Offline P2 ABI matrix: PASS at ``-O0``, ``-Os``, and ``-O2``.  Sources,
  exact commands, diagnostics, objects, relocations, disassembly, maps, and
  linked ELFs are under
  ``artifacts/hil/abi/20260712T212937Z``.  Direct and indirect calls use
  CALLA, returns use RETA, helper calls use ``R_P2_20``, data references use
  ``R_P2_AUG20``, and no ``R_P2_COG9`` relocation was emitted.
* Context contract: OFFLINE VERIFIED.  The fixed register array contains 37
  longs: r0-r31, PA, PB, PTRA, PTRB, and the saved interrupt-enable state.
  The packed C/Z/20-bit-PC resume long is separate at ``[saved PTRA - 4]``;
  it is consumed by RETA and is not a synthetic register-array slot.
* Q-free compiler arithmetic runtime: OFFLINE VERIFIED.  All 17 required
  32-bit and 64-bit multiply, divide, remainder, combined-divmod, and shift
  helpers passed boundary tests and 5,000 randomized cases per group.  Its
  P2 object has no undefined symbols, Q instructions, recursive helper
  relocations, or ``R_P2_COG9`` relocations.
* Atomic ABI probe: NOT LOCK-FREE.  The compiler reports a maximum lock-free
  width of zero and lowers 32-bit atomics to external ``__atomic_*_4``
  helpers.  The P2 configurations now select NuttX's interrupt-serialized
  architecture atomic runtime; link and hardware execution remain to be
  verified before atomics are claimed.
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
* Initial one-million-switch diagnostic: FAIL as designed evidence under
  ``artifacts/hil/20260712T220309.810700Z-context``.  It reached exactly
  1,000,000 switches but reported ``P2CTX:FAIL MASK=8``.  Linked disassembly
  proved that p2llvm leaves outgoing variadic arguments live at and above the
  unadvanced task PTRA before CALLA, while the original ISR wrote IRET1 and
  registers at that same PTRA.  The rare preemption window corrupted only
  the variadic arguments.
* Detached interrupt-frame correction: PASS ON HARDWARE.  INT1 now saves
  IRET1, r0-r31, PA, PB, PTRA, PTRB, and interrupt state into fixed guarded
  Hub scratch before clobbering task state, runs C on a dedicated 2 KiB
  guarded ISR stack, and copies the selected 37+1-long context through
  detached task frames.  The linked verifier reconstructs all 16 AUGS-formed
  scratch addresses and rejects task-PTRA ISR writes.
* Required timer preemption stress gate: PASS at exactly 1,000,000 CT1
  switches.  Both tasks retained register windows, independent stack
  canaries, nested spills, outgoing variadic arguments, and 64-bit arithmetic;
  the scratch and ISR-stack guards also passed.  The RAM-only physical run,
  exact ELF SHA-256
  ``5b36d51df4e64d5810964de236e72422b5473b077aef81cee74d047b264ea525``,
  console log, marker set, map, sources, and toolchain lock are preserved at
  ``artifacts/hil/20260712T222926.532895Z-context``.
* Serial device ownership after HIL: PASS; the loader released the port.
* NuttX runtime status: NOT YET TESTED.  Startup, console, stack, and build
  integration are active work, but the detached resume word and real
  interrupt veneer have not yet been integrated into the kernel.

Next acceptance gate
--------------------

Integrate the detached 37+1-long frame and dedicated ISR stack into the NuttX
TCB/interrupt path, finish the native startup/console link, and require a
RAM-loaded NuttX early-boot banner before enabling the scheduler tick and NSH.
