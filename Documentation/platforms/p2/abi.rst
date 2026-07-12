p2llvm ABI draft
================

Status: DRAFTED, BLOCKED for compiler execution in this cloud image, and
HIL-REQUIRED for interrupt entry/return validation.

The build contract now selects ``--target=p2`` and adds
``-fno-jump-tables`` to avoid unqualified table-generation assumptions during
bring-up.  ``tools/p2/run-abi-probes.sh`` creates probe sources covering leaf
and non-leaf calls, recursion, register pressure, function pointers, switch
statements, 64-bit division/modulo, structure passing/return, variadics, weak
symbols, custom sections, memcpy/memset, and atomics.  For each of ``-O0``,
``-Os``, and ``-O2`` it preserves commands, assembly, objects, disassembly,
section tables, and symbols below ``artifacts/cloud-p2/abi/`` when p2llvm is
installed.

Cloud execution result: BLOCKED.  Exact command::

  ./tools/p2/run-abi-probes.sh

Exact error::

  BLOCKED: /root/.cache/p2-nuttx/p2llvm/install/bin/clang not found; run tools/p2/bootstrap-cloud.sh or set P2LLVM_ROOT/P2_CLANG

The current frame assumption remains a draft until generated p2llvm assembly
is reviewed against the interrupt veneer and hardware return path.
