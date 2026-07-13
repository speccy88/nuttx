p2llvm ABI evidence
===================

Status: compiler and linker code generation is **STATICALLY-VERIFIED** with
offline probes.  The 38-long interrupt frame and upward-stack return path are
**HIL-VERIFIED**.  The supported runtime contract remains flat and UP only.

Build contract
--------------

The board compiles C and assembly with the pinned p2llvm toolchain and these
core flags::

  --target=p2 -fno-jump-tables -fno-builtin -fno-common \
    -ffunction-sections -fdata-sections

Normal board builds use ``-Os``.  Final linking uses the pinned ``ld.lld``
with the board script, section garbage collection, and a map file.  The build
wrapper rejects unsafe offset-zero ``R_P2_AUG20`` input relocations before
linking and runs ``tools/p2/verify-elf.py`` on the result.

Offline probes
--------------

``tools/p2/run-abi-probes.sh`` refuses to run unless the compiler executable
hash and reported LLVM commit match ``tools/p2/toolchain.lock``.  It compiles
the probe set at ``-O0``, ``-Os``, and ``-O2`` and preserves commands,
assembly, objects, disassembly, ELF headers, sections, relocations, symbols,
maps, and sizes under ``artifacts/hil/abi/<UTC-run-id>/``.  It covers:

* leaf, non-leaf, recursive, indirect, variadic, and register-pressure calls;
* scalar and structure arguments and returns;
* switches, volatile memory, custom and weak sections, and fixed registers;
* 32-bit and 64-bit arithmetic, division/modulo, and comparisons;
* memcpy/memset lowering and linked freestanding objects;
* atomic load/store and compare-exchange diagnostics; and
* PASM2 special-register, C/Z, and block-transfer encodings.

The backend lowers ordinary C multiply/divide operations to Hub-callable
runtime helpers instead of emitting preemption-unsafe shared CORDIC sequences.
Atomic probes compile but report no lock-free width and reference runtime
``__atomic_*`` helpers; that is evidence of lowering, not proof that arbitrary
C atomics are lock-free.  ``block_context.S`` proves assembler encoding only,
not interrupt correctness by itself.

Release evidence rule
---------------------

An ABI artifact is current only when ``toolchain.txt`` records the release
source commit and its ``clang_sha256`` matches the active lock.  The runner
currently hashes clang but only checks the other LLVM tools for executability,
so final provenance must also retain ``toolchain.lock`` and the normal build
artifact.

The accepted release run is
``artifacts/hil/abi/20260713T155112Z``.  It was generated from a clean detached
worktree at source/tool commit
``cfaf600a55f41d8ea538b83b1c8c1ce459c9996a`` with clang SHA-256
``cc89d3c27b75c9e059093d1e5c6cc7a392b74d977e30d90ca9994f97001224f7``.
All nine optimization/capability status files are ``SUPPORTED`` and the
preserved ``summary.txt`` SHA-256 is
``1e295203780fcf387eeeffca1fd7601735c004be85f4e133e40c1e08ac8f7b25``.
The independent ``compare64_codegen.py`` verification passed 41,472 functional
boundary pairs.  A later documentation-only commit does not invalidate this
source/tool result.  To regenerate the evidence after any future source or
tool change::

  ./tools/p2/run-abi-probes.sh

The native context proof at
``artifacts/hil/20260713T034110.407118Z-context`` supplies the separate
hardware evidence for CALLA/RETI1 behavior, packed C/Z/PC resume state, PTRA,
register preservation, nested spills, variadics, and 64-bit arithmetic across
1,000,000 CT1 switches.  It does not validate SMP, protected builds, or every
possible compiler optimization.
