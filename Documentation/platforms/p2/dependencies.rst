Pinned dependencies
===================

Status: the legacy macOS local toolchain is **COMPILED**, hash-pinned, and was
used by the recorded builds and HIL campaigns.  The tracked
``tools/p2/toolchain.lock`` remains the authoritative record for those legacy
artifacts, but it does not claim the opt-in unified-memory compiler pass.  A
``unified`` or ``unified-hil`` build must use a newly generated exact lock for
the rebuilt compiler; ``tools/p2/build.sh`` rejects the legacy compiler before
configuration.  ``tools/p2/dependencies.lock`` is a historical cloud snapshot
and may still contain old ``BLOCKED_missing`` entries.

Pinned source revisions
-----------------------

The active lock records:

* NuttX implementation baseline:
  ``689ebdb6b831bc3d151c10c8e26379f55dc56b38``;
* nuttx-apps: ``67673a8074c4bc07a161816150ed3b64350f4b59``;
* p2llvm: ``bdcefcce7860b2232c06f35726fea679a3a7309c``;
* llvm-project: ``72a9bb1ef2656d9953d1f41a8196d425ff2ab0b1``;
* p2llvm loadp2 submodule: ``21e074cc7ee6fbd4fb12ef5352544b3457a6729c``;
* FlexProp: ``858f51c4a24e7ae0f6cbc78f625c731083ad304f``;
* spin2cpp: ``28f1b80fc3a36422fb0a1f7c54465d808634abc8``; and
* FlexProp loadp2: ``c20afedd4253d09da449fa740f8d4304481fc560``.

The lock update itself is committed at NuttX
``cfaf600a55f41d8ea538b83b1c8c1ce459c9996a``.  The NuttX source can
legitimately advance beyond the lock's implementation baseline because a lock
file cannot name the commit which contains itself.  Every build/HIL artifact
therefore records the exact NuttX and apps commits, source cleanliness,
configuration, and copied lock used for that image.

Executable identities
---------------------

The tracked legacy lock records these SHA-256 executable identities:

* clang: ``cc89d3c27b75c9e059093d1e5c6cc7a392b74d977e30d90ca9994f97001224f7``;
* ld.lld: ``d49992169271c83f92e96e775ba0531f9260014960eab57bc7d4a761b260d6b1``;
* kconfig-conf: ``8ee692c50735715d1259b0775b75bf231b9703f6c4f233254facaec9c5d2bcf8``;
* FlexProp loadp2: ``543c7d522d27f429120e6a35e32ea19394fa85412fb07f41784748094a03c2aa``.

The unified worktree's compiler patch-source identities, which a fresh
unified lock must also pin, are:

* the regenerated preemption-safe p2llvm patch:
  ``3d4c7a031bc9d260ba9ebe93a93e287d27f6142ccb081eb3a544fa7875cb8d27``;
  and
* the opt-in unified-memory p2llvm patch:
  ``9a11e6a10ae8d66a970c0db94a0bacb543d4adfe023fead010a947be5181af32``.

``tools/p2/bootstrap-local.sh`` checks pinned repositories, applies only the
exact preemption-safe and unified-memory patch series, builds missing tools,
runs both the existing backend postconditions and the unified-memory codegen
contract, installs a hash-locked Python environment, writes
``~/.p2-nuttx-env``, and regenerates the selected runtime lock.  Patch-state
validation uses an isolated temporary index and object database, accepts only
an exact series prefix for safe upgrades, and never resets the compiler
worktree.  Set ``P2_BOOTSTRAP_PATCH_SELFTEST=only`` to exercise clean,
prefix-upgrade, exact, and tampered states in a sparse temporary clone.  The
Python HIL requirements are pyserial 3.5 and pyelftools 0.32 with hashes in
``tools/p2/requirements-hil.txt``.

``tools/p2/bootstrap-cloud.sh`` enforces the same exact p2llvm outer commit,
llvm-project gitlink/checkout, and two-patch source state before building.  An
existing checkout at another commit, with outer-tree changes, or with an
unexpected llvm-project checkout is rejected without resetting it.  Once the
unified code-generation postcondition passes, the cloud bootstrap writes the
selected ``P2_TOOLCHAIN_LOCK`` (default ``$P2_CACHE/toolchain.lock``) with the
exact NuttX, apps, p2llvm, and llvm-project commits plus SHA-256 entries for
clang/clang++, ld.lld, llc, the LLVM archive, symbol, object-copy,
disassembly, ELF-inspection, size, and strip utilities, and both compiler
patches.  The local bootstrap pins the same complete set.  The unified build
rejects a lock missing any of those exact paths and hashes before
configuration.  The cloud bootstrap exports that lock through
``~/.p2-nuttx-env`` so ``tools/p2/build.sh unified`` consumes the same record;
if loadp2 was built, its exact executable is included for RAM-load HIL as well.

The bootstrap deliberately skips p2llvm libc.  NuttX supplies the runtime
environment and architecture helpers; libp2 headers/archive are installed for
toolchain completeness but are not linked into normal NuttX images.
