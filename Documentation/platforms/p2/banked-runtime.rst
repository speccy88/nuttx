Banked runtime for large application sets
==========================================

Status: **DRAFTED**.  The manager/bank split and destructive warm-boot path
remain **HIL-REQUIRED** until a recorded build and physical-board run qualify
the integrated profiles.  This document describes the intended contract; it
does not turn external PSRAM into normal NuttX memory.

Why banks are necessary
-----------------------

The P2 has 512 KiB of byte-addressable Hub RAM.  This port deliberately limits
each linked image to ``[0x00000000, 0x0007c000)``, or 507,904 bytes, and leaves
the physical top 16 KiB ``[0x0007c000, 0x00080000)`` outside the image.  Code,
read-only data, writable data, BSS, task stacks, and the NuttX heap for the
currently running system must all fit below ``0x0007c000``.

The board's 32 MiB PSRAM and 16 MiB W25 flash provide useful capacity, but
neither changes that active-execution limit:

* PSRAM is reached through explicit QPI service transfers.  It is not
  byte-addressable Hub memory and cannot directly contain executable code,
  the kernel heap, task stacks, or ordinary C objects.
* W25 flash provides persistent boot and filesystem storage.  The P2 does not
  execute this NuttX image directly from that SPI flash.

Consequently, putting NSH, editors, filesystem tools, Berry, LVGL, display
drivers, and future applications into one flat image does not scale.  The
banked design keeps a small operational manager resident in boot flash and
stores complete alternative NuttX images as files.  A transition replaces the
manager in Hub RAM with one selected bank.  Only one bank is active at a time.

Storage and Hub layout
----------------------

.. list-table:: Banked-runtime regions
   :header-rows: 1

   * - Region
     - Use
   * - W25 ``[0x000000, 0x080000)``
     - Private 512 KiB boot reservation containing the manager image
   * - W25 ``[0x080000, 0x1000000)``
     - 15.5 MiB SmartFS data partition, exposed as ``/dev/smart0`` and
       mounted at ``/mnt/flash``
   * - ``/mnt/flash/banks/berry.bin``
     - Raw, self-contained Berry/LVGL NuttX bank image
   * - PSRAM ``[0x01f84000, 0x02000000)``
     - Fixed 507,904-byte staging window at the top of the explicit 32 MiB
       PSRAM store
   * - Hub ``[0x00000000, 0x0007c000)``
     - Manager or selected bank, never both
   * - Hub ``[0x0007c000, 0x00080000)``
     - Preserved 16 KiB warm-start area; the CRC-protected handoff begins at
       ``0x0007c000``

The 512 KiB W25 boot reservation is slightly larger than the maximum manager
image, but the manager is still bound by the same ``0x7c000`` Hub limit.  The
remaining flash can hold multiple future bank files and persistent data.  It
does not allow their code or heaps to coexist while one bank is executing.

Manager and Berry bank responsibilities
---------------------------------------

The ``p2-ec32mb:berrymgr`` profile is the flash-resident control environment.
Its intended resident feature set is:

* NSH with command-line editing and bounded command history;
* the ``vi`` editor;
* non-destructive SmartFS mount at ``/mnt/flash``;
* FAT microSD automount at ``/mnt/sd`` without automatic formatting;
* ``p2recv`` for CRC-checked serial file provisioning;
* the explicit PSRAM service and its diagnostic command;
* the ILI9341 LCD device; and
* ``p2bank`` plus a ``berry`` convenience alias for bank transitions.

Berry and LVGL are intentionally absent from the manager.  The
``p2-ec32mb:berrybank`` profile is a separate, self-contained flat NuttX image
with the Berry VM, native LVGL bindings, ILI9341/XPT2046 support, and enough
FAT/SD support to mount ``/dev/mmcsd0`` at ``/mnt/sd``.  LVGL uses the direct
``/dev/lcd0`` interface and a small partial draw buffer in this memory-limited
bank; the separate framebuffer profiles remain available for native C demos.
The bank omits the resident manager shell and flash services to leave Hub RAM
for the VM, LVGL draw buffer, widgets, and the selected script.

The manager seeds these example scripts on the SD card when they do not
already exist:

* ``/mnt/sd/berry-p2/core_smoke.be``;
* ``/mnt/sd/berry-p2/lvgl_bars.be``; and
* ``/mnt/sd/berry-p2/lvgl_widgets.be``.

Existing files are not overwritten.  The bars and widgets examples exercise
animation and several LVGL controls.  A valid manager request runs exactly the
selected script; a missing requested script fails over to the REPL rather than
silently running something else.  A valid empty request, produced by the bare
``berry`` command, intentionally starts the REPL.  The widgets script is used
only as recovery when the bank was entered directly without a valid handoff.

Warm-bank transition
--------------------

``p2bank [bank-file [script-path]]`` selects a raw bank file.  With no bank
argument it uses ``/mnt/flash/banks/berry.bin``.  The convenience command
``berry [script-path]`` uses that same fixed Berry bank.

Before changing the running system, the manager:

#. verifies that the bank is a regular file with a nonzero, four-byte-aligned
   size no greater than ``0x7c000`` bytes;
#. streams it through Hub-RAM chunks into PSRAM starting at ``0x01f84000``
   while computing CRC-32/ISO-HDLC;
#. reads the staged bytes back through the PSRAM service and requires the same
   CRC;
#. writes a versioned handoff at Hub ``0x0007c000`` containing the image size,
   image CRC, optional script path, and a separate handoff CRC; and
#. transfers the PSRAM service cog into a small self-contained COGEXEC loader.

The manager holds an owner-scoped PSRAM bank reservation from the first stage
write through verification and destructive launch.  Other nonempty PSRAM
transfers fail with ``-EBUSY`` during that interval, so the verified staging
window cannot be changed by another manager task.

All failures before the final transfer return to the still-running manager
with an error.  Once the COGEXEC loader begins, the transition is deliberately
destructive: it stops the other cogs, copies the staged image to Hub address
zero, preserves the top 16 KiB handoff area, and starts cog 0 at Hub address
zero.  There is no live manager left to return to and no reverse swap.

The Berry bank copies and validates the handoff before using it.  Requested
scripts must be safe absolute paths below ``/mnt/sd/`` and fit in the
192-byte handoff field including the terminating NUL.  It mounts the existing
SD filesystem without formatting, runs Berry with the SD module path, and
issues ``BOARDIOC_RESET`` after Berry returns.  That reset boots the manager
from W25 flash again.  At the bank REPL, ``quit()`` (or ``quit(status)`` with
an integer status) raises Berry's normal VM exit and returns to the manager by
reset.  Raw UART Ctrl-D handling is terminal-dependent and is not the bank's
exit contract.

Build and inspect
-----------------

Build the two profiles independently from the NuttX root:

.. code-block:: console

  cd /Volumes/SSD2TB/Code/nuttx
  source "$HOME/.p2-nuttx-env"
  ./tools/p2/build.sh berrymgr
  ./tools/p2/build.sh berrybank

Each command prints its timestamped artifact directory, for example
``artifacts/hil/<UTC>-build-berrymgr`` and
``artifacts/hil/<UTC>-build-berrybank``.  The wrapper runs the P2 ELF verifier
and records the source/configuration provenance.  Before installation, verify
that both raw files obey the bank limit:

.. code-block:: console

  wc -c \
    artifacts/hil/<manager-UTC>-build-berrymgr/nuttx.bin \
    artifacts/hil/<bank-UTC>-build-berrybank/nuttx.bin

Neither size may exceed 507,904 bytes.  Size compliance alone is not a runtime
memory proof: inspect ``nuttx.map``, ``symbols.txt``, and the linked size report
for usable heap and stack margin in each profile.

Install the manager and Berry bank
----------------------------------

The boot manager is the only image programmed into the private W25 boot
reservation.  Generate a flash input with its manifest, then use the gated
flash helper.  Omitting ``--execute`` is a dry run.

.. code-block:: console

  source .p2-hil.env
  MANAGER=artifacts/hil/<manager-UTC>-build-berrymgr
  OUT=/tmp/p2-berrymgr-flash
  mkdir -p "$OUT"
  python3 tools/p2/mkflash.py "$MANAGER/nuttx.bin" \
    -o "$OUT/flash-input.bin"
  ./tools/p2/flash.sh \
    --port /dev/cu.usbserial-P97cvdxp \
    --image "$OUT/flash-input.bin" \
    --build-artifact "$MANAGER"

An actual flash operation additionally requires ``--execute`` and the explicit
``P2_HIL``, reset, flash-write, flash-erase, and shared-SD-write authorization
environment variables documented in :doc:`flash-layout`.  Keep the boot
switches in serial/flash mode ``(FLASH,up,down)=(ON,OFF,OFF)``.

Physically remove the microSD card before this W25 operation.  The authorization
for shared-SPI writes is a safety gate, not media protection; an installed card
lost its sector-zero MBR during bring-up even though its FAT32 structures and
files survived.  See :doc:`flash-layout` and :doc:`storage-arbitration` for the
guarded, exact-layout-only recovery procedure and HIL evidence.

.. code-block:: console

  P2_HIL=1 P2_ALLOW_RESET=1 \
  P2_ALLOW_FLASH_WRITE=1 P2_ALLOW_FLASH_ERASE=1 \
  P2_ALLOW_SD_WRITE=1 \
    ./tools/p2/flash.sh --execute \
      --port /dev/cu.usbserial-P97cvdxp \
      --image "$OUT/flash-input.bin" \
      --build-artifact "$MANAGER"

After the manager has booted and mounted SmartFS, provision the raw bank file
through the serial receiver.  This updates only the SmartFS file; it does not
rewrite the boot reservation:

.. code-block:: console

  BANK=artifacts/hil/<bank-UTC>-build-berrybank
  P2_HIL=1 python3 tools/p2/p2recv.py \
    "$BANK/nuttx.bin" /mnt/flash/banks/berry.bin \
    --port /dev/cu.usbserial-P97cvdxp --execute --force

``p2recv`` is dry-run by default, restricts destinations to ``/mnt/``, checks
each transfer chunk and the whole file, writes a temporary file, calls
``fsync()``, and renames only after validation.  While receiving, it places the
console in raw termios mode so bytes such as ``0x08`` and ``0x0d`` cannot be
consumed or translated by canonical line editing, and it restores the saved
terminal state on every exit path.  The bank loader independently checks the
file size and PSRAM readback CRC before switching.

Run and edit
------------

From the manager NSH prompt:

.. code-block:: console

  vi /mnt/flash/notes.txt
  berry
  > quit()
  berry /mnt/sd/berry-p2/lvgl_widgets.be
  berry /mnt/sd/berry-p2/lvgl_bars.be
  berry /mnt/sd/berry-p2/core_smoke.be

The generic form is useful for future banks:

.. code-block:: console

  p2bank /mnt/flash/banks/berry.bin \
    /mnt/sd/berry-p2/lvgl_widgets.be

Launching a bank temporarily removes NSH, line editing, ``vi``, and manager
services because their Hub image has been replaced.  They return after the
bank resets to flash.  Persistent files on SmartFS and SD survive that reset.

The current Berry interface is a deliberately small P2 binding, not the full
upstream LVGL object surface.  It roots wrappers, styles, and callbacks until
``lv.stop()`` so the fixed demos remain safe while native callbacks can still
refer to them.  A long interactive REPL session which repeatedly creates
widgets without calling ``lv.stop()`` will consume the bank's limited Hub heap.

Safety and recovery contract
----------------------------

* A bank is trusted firmware, not a sandboxed application.  ``p2recv`` checks
  the host-to-SmartFS transfer CRC, and ``p2bank`` checks that PSRAM matches
  the file it just read.  There is currently no persisted expected digest, so
  those checks do not detect later SmartFS bit rot or replacement with another
  well-formed image.  Do not install or launch untrusted bank files.
* Staging overwrites the fixed top ``0x7c000`` bytes of PSRAM.  Applications
  must reserve that range and must not expect data there to survive a launch.
* If a bank-launch task is forcibly killed after taking the PSRAM reservation,
  the manager intentionally fails closed.  Reset to the flash manager to clear
  that stranded reservation before retrying.
* Validation and PSRAM errors before the destructive handoff leave the manager
  running.  Errors after other cogs stop cannot safely re-enter that manager;
  the loader can only park for an external reset.  It must never print a false
  success marker after starting the destructive copy.
* Reset or power-cycle in ``(ON,OFF,OFF)`` mode to recover the independently
  flashed manager.  A bad bank file does not overwrite the W25 boot
  reservation.  Remove or replace ``/mnt/flash/banks/berry.bin`` from the
  recovered manager before retrying.
* If the manager image itself is damaged, use the guarded serial flash
  procedure in :doc:`flash-layout`.  If SmartFS is damaged, do not silently
  autoformat it; preserve the existing non-destructive startup policy and
  recover data explicitly.
* Remove the microSD card before programming W25.  If it was deliberately left
  installed, validate it before mounting.  ``p2storage sd-mbr-repair
  P2STORAGE-I-ACCEPT-DATA-LOSS-V1`` is permitted only for the exact
  NuttX-produced layout after its VBR, FSInfo, backup, geometry, and bounds all
  validate; follow it with read-only ``p2storage sd-rom-verify``.  Never run
  this repair or any formatter automatically at boot.

Expansion rules
---------------

Future features should be grouped into additional complete banks when they no
longer fit with the resident manager.  Each bank must include the kernel,
drivers, runtime, heap, and stacks it needs and must still fit below
``0x7c000``.  Banks cannot call code or retain pointers into a replaced bank,
and manager tasks do not survive the transition.  Persistent files, explicit
PSRAM data ranges below ``0x01f84000``, and the versioned handoff are the
appropriate cross-bank boundaries.

This design therefore extends the *installed application set* and available
data storage, not the byte-addressable memory of one running NuttX image.
See :doc:`memory-map`, :doc:`psram-service`, and :doc:`flash-layout` for the
underlying hardware contracts.
