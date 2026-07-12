SMP evaluation
==============

Status: DRAFTED. No SMP defconfig is provided.

Option A is one NuttX scheduler cog plus deterministic service cogs for serial/storage/PSRAM. Option B is multiple NuttX SMP cogs plus optional service cogs. SMP requires per-cog register and interrupt state, current-TCB handling, attention-event IPIs, carefully mapped hardware locks/spinlocks, timer ownership, task migration, startup, and debug tooling. Service-cog design is preferred until the UP port is stable.
