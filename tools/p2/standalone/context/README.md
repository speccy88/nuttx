# Native P2 CT1 preemptive-context proof

This directory contains a RAM-only, pre-NuttX hardware proof for the
Propeller 2 interrupt and task-context contract. It builds with the pinned,
preemption-safe P2LLVM toolchain and the port's Q-free software arithmetic
runtime. It does not link P2LLVM libc.

The Makefile is offline-only. It never opens a serial device, resets a board,
programs flash, or accesses an SD card. Building this target is **COMPILED**
and **STATICALLY-VERIFIED** evidence; its one-million-switch result remains
**HIL-REQUIRED** until the separate HIL owner runs the generated ELF.

## Frame contract

PTRA grows upward and always identifies the first free long. The interrupt
veneer never writes its frame through task PTRA: compiler-generated outgoing
arguments may be live at `[PTRA + 0...]` before the caller advances PTRA.
Instead, cog 0 owns a guarded fixed Hub scratch frame `F`:

| Hub offset | Saved state |
|---:|---|
| `F + 0` | Separate IRET1 packed `{C,Z,10'b0,PC[19:0]}` resume long |
| `F + 4` through `F + 128` | Context array `r0` through `r31` |
| `F + 132` | Context array `PA` |
| `F + 136` | Context array `PB` |
| `F + 140` | Context array logical post-resume `PTRA`, entry PTRA + 4 |
| `F + 144` | Context array `PTRB` |
| `F + 148` | Context array normalized interrupt state |

The context array is exactly 37 longs and the detached frame is 38 longs with
its separate resume word. Entry stores IRET1, all 32 GPRs, PA, PB, raw PTRA,
and PTRB to absolute scratch before touching a GPR. It then computes logical
post-resume PTRA, snapshots GETBRK state, switches to a separate guarded
2 KiB IRQ C stack, and calls the dispatcher. The dispatcher copies scratch
to the interrupted task's detached frame and copies the selected detached
frame back. Restore uses only absolute scratch loads, loads logical PTRA last,
subtracts four to recover task PTRA, and executes `RETI1`.

Only initial startup materializes a physical `[resume][37 registers]` frame
on each task stack. `p2_context_start` launches task 0 with RETA; task 1's
detached synthetic frame enters through the normal IRET1/RETI1 restore path.

The veneer does not execute `STALLI` before `GETBRK r0 WCZ`. GETBRK therefore
observes the incoming global stall state truthfully; its bit 1 is normalized
to `P2_IRQSTATE_STALLED`. The proof configures only highest-priority INT1, and
INT1 remains busy throughout its own handler, so same-level nesting is
excluded without changing the state before it is saved. Restore selects raw
`STALLI` or `ALLOWI` while INT1 is still busy.

The pinned assembler has no `SETINT1`, `STALLI`, `ALLOWI`, or `RETI1`
mnemonics. Fixed words are used after offline encoding checks:

- `ALLOWI`: `0xfd604024`
- `STALLI`: `0xfd604224`
- `SETINT1 #1` (CT1): `0xfd640225`
- `SETINT1 #0` (disabled): `0xfd640025`
- `RETI1`: `0xfb3bfff5`

`SETINT1 #0` follows the immediate encoding formula
`0xfd640025 | (event << 9)` with event zero. The verifier checks the formula,
the linked words at their exact symbol offsets, and the assembler-supported
`GETBRK r0 WCZ` word `0xfd7ba035`.

Pinned llvm-mc also does not insert AUGS for a symbolic `##` Hub-memory
operand even though it emits `R_P2_AUG20`; without an explicit preceding
`AUGS #0`, lld rewrites the preceding instruction. Every absolute scratch or
IRQ-stack access therefore has an explicit AUGS immediately before its
symbolic instruction. The 32-register transfers intentionally use
`SETQ #31; AUGS #0; WRLONG/RDLONG`: AUGS is the address prefix for the block
transfer, and SETQ remains the controlling Q value consumed by that transfer.
There is a second pinned-linker constraint: `R_P2_AUG20` adds a symbol's low
nine bits to the instruction field without propagating a carry into AUGS.
Both fixed symbols are therefore 512-byte aligned and every field offset is
below 512. The verifier reconstructs the resolved address from every linked
AUGS+instruction pair and compares it with the exact scratch/stack symbol and
field offset; it also rejects any `ptra[...]`/`ptrb[...]` memory operand in the
linked ISR.

## Stress workload

CT1 uses an absolute deadline advanced by 18,000 clocks per switch. Two
separate 12 KiB upward-growing stacks have low and high canaries. Both tasks
start from synthetic frames and repeatedly execute:

- an assembly window holding task-distinct values in `r4` through `r29`
  across at least eight timer switches;
- nested calls and a 24-long volatile spill array;
- a six-value variadic call;
- 64-bit division, remainder, multiplication, and an algebraic check;
- stack-canary checks.

The variadic call deliberately retains six outgoing stack stores before
`ADD PTRA,#28` and CALLA. This is the exact preemption window that exposed the
old task-PTRA ISR-frame bug, and the verifier requires it to remain present.
Task stacks, fixed IRQ scratch, and the dedicated IRQ C stack all have
independent canaries.

At exactly 1,000,000 dispatches, the dispatcher changes INT1's source to
event zero before returning to a task, so the count cannot drift past the
target. Final reporting permanently stalls and masks interrupt delivery.
Register windows normally require eight observed switches and only those
full windows increment the per-task proof counters. A window entered after
switch 999,992 clamps its wait target to 1,000,000 and returns a distinct
terminal-escape result, avoiding an unreachable wait after CT1 is masked
without misreporting a short terminal window as an eight-switch proof.
Startup also begins in `STALLI`: all UART banners and both synthetic frames
are completed first, then CT1 and IJMP1 are armed without `ALLOWI`.
`p2_context_start` selects task 0's stack before restoring that synthetic
frame's ALLOWI state, so the first timer interrupt cannot accidentally save
the boot stack as task 0.

Expected UART markers at 230400 baud include:

```text
P2CTX:ENTRY
P2CTX:FRAME=37+1
P2CTX:TIMER=CT1 ABSOLUTE
P2CTX:START
P2CTX:TARGET=1000000
P2CTX:PROGRESS=100000
...
P2CTX:PROGRESS=1000000
P2CTX:SWITCHES=1000000
P2CTX:REGS=OK
P2CTX:STACKS=OK
P2CTX:REGPATTERN=OK
P2CTX:CANARY=OK
P2CTX:NESTED_SPILLS=OK
P2CTX:VARARGS=OK
P2CTX:ARITH64=OK
P2CTX:IRQ_CANARIES=OK
P2CTX:PASS
P2CTX:PASS SWITCHES=1000000
```

Any validation failure ends with `P2CTX:FAIL MASK=<decimal-mask>` and never
prints the PASS marker.

## Offline build and verification

From the NuttX repository root:

```sh
make -C tools/p2/standalone/context clean all
```

Set `P2LLVM_ROOT=/path/to/p2llvm/install` only when the pinned install is
outside the workspace cache. Outputs are under `build/`:

- `p2context.elf`: volatile Hub-RAM loader input;
- `p2context.bin`: flat Hub image;
- `p2context.map`: complete link map.

`verify.py` rejects the wrong machine/entry/load address, normal C below
`0x0a00`, missing or undefined symbols, boot-stack overlap, compiler-generated
`QMUL/QDIV/GETQX/GETQY`, any `R_P2_COG9` relocation in the input objects or
linked ELF, a reordered 37+1 save/restore veneer, STALLI before GETBRK, an
interrupt-enabled boot-stack launch, any ISR save through task PTRA, a
symbolic IRQ access without its immediately preceding AUGS, loss of the
outgoing stack-argument hazard probe, or incorrect raw control words.
`test_verify.py` includes negative mutation tests for task-PTRA ISR writes,
missing AUGS prefixes, broken SETQ/AUGS block pairs, missing stack-argument
PTRA advancement, plus the parser and raw-opcode contracts.

Hardware loading and monitoring are intentionally not automated here. The
board/HIL owner must use the repository's separately gated RAM-loader and HIL
tools, preserve the resulting artifact directory, and require the exact PASS
marker before promoting this proof from HIL-REQUIRED.
