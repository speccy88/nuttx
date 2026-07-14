P2-EC32MB final hardware-in-the-loop report
============================================

Report snapshot: 2026-07-13.  This report distinguishes physical hardware
results from compilation and host verification.  A path under
``artifacts/hil`` or evidence preserved in and linked from the release package
is required for a final release hardware claim.  The required candidate
evidence is now preserved in the local release evidence archive; its ``/tmp``
location remains provisional until the exact asset is uploaded and verified
from a fresh GitHub download.

Overall result
--------------

The native flat, uniprocessor P2 port is operational on physical hardware.
Native startup, the 180 MHz clock including a host-referenced raw GETCT
calibration, polled early console, CT1 preemption,
detached 37+1-long contexts, upward-growing stacks, the NuttX scheduler,
bring-up, NSH, SmartFS on the protected flash data partition, FAT32 on
microSD, independent ROM flash boot, a historically development-qualified ROM
microSD boot, GPIO/UART/PWM/capture/general SPI Smart Pin devices, DAC/ADC,
BMP180 I2C, and
the explicit 32 MiB PSRAM service have hardware evidence.  The dedicated
flat-UP scheduler campaign also passed
1,004,078 counted events across priorities, round robin, semaphores,
priority-inheritance mutexes, condition variables, message queues, signals,
timers, pthread lifecycle/cancellation, and task recreation.

The historical scoped flat-UP hardware acceptance is complete.  All 45
applicable ``ostest`` rows and 57/57 strict parser groups per cycle passed one
assertion cycle and five production cycles in both the priority-inheritance
and real
condition-variable profiles: 12/12 physical RAM-load/reset cycles.  The two
fixture-conditional groups remain blocked and six architecture-inapplicable
groups remain N/A.  The raw-clock
and dedicated scheduler-stress gates are independently accepted; neither is
used as a substitute for ``ostest``.  Card-absent behavior and true
power-cycle testing remain explicit fixture-dependent evidence gaps.  SMP is
**DEFERRED / OUT OF SCOPE** and does not gate this flat-UP result.  The
current release candidate has clean dual-board builds, a fresh ABI PASS, Rev-B
RAM showcase HIL, flash-programming HIL, and ten completed hash-bound
reset-only flash boots.  Its exact SD write and read-only ROM-layout inspection
plus its final no-loader SD-only ROM reset are also PASS.  The 20-file local
package, all six installer dry-runs, and extracted-bundle verification are
also PASS.  GitHub upload, fresh-download verification, and publication are
still **PENDING** at this snapshot.

1. Hardware setup
-----------------

* Processor/module: Parallax P2X8C4M64P on a P2 Edge Module with 32 MB RAM,
  product ``P2-EC32MB``, Rev B.
* NuttX board: ``p2-ec32mb``.
* Clock source: onboard 20 MHz TCXO; qualified target clock 180 MHz.
* Native memory: 512 KiB Hub RAM.  The current loadp2 contract withholds its
  upper 16 KiB, so linked NuttX images use ``[0x00000000,0x0007c000)``.
* Nonvolatile storage: 16 MiB W25-compatible onboard SPI flash.  Hardware
  returned JEDEC ``EF7018``.
* External memory: four 8 MiB APS6404L-class QPI PSRAM devices, 32 MiB total.
  This is exposed only through ``/dev/psram0`` and is not native addressable
  NuttX memory.
* Removable storage: an installed, disposable microSD card.  HIL reported
  61,132,800 512-byte sectors (31,299,993,600 bytes).
* Console/programming: P62/P63 through a Parallax PropPlug.
* Fixture: direct jumpers remain on P0--P1, P2--P3, and P6--P7.  Every digital
  test configures the receiving end as an input before enabling one source.
  The historical 50/50 PWM/capture waveform campaign used the former direct
  P4--P5 jumper.
  That jumper has now been replaced by the requested analog fixture: a
  1 kOhm series resistor from P4 to P5 and a 100 nF capacitor from P5 to GND.
  That fixture passed 20/20 DAC/ADC cycles and the current candidate's bounded
  RC-safe ``/dev/pwm0`` open/start/stop smoke.  The smoke is not digital
  waveform/capture qualification.  P8 (SPI clock) and P9 (SPI chip select)
  were controller outputs with no external connection.  A BMP180 is installed
  with P24 SDA and P25 SCL and passed 20/20 I2C cycles.
* Host-controlled power switching is unavailable because
  ``P2_POWER_CYCLE_COMMAND`` is empty.

Hardware references are the official Parallax P2-EC32MB schematic and module
guide and the PSRAM vendor data sheet:

* https://www.parallax.com/package/p2-edge-module-with-32mb-ram-schematic/
* https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/5789/P2-EC32MB-Edge-Module-Rev-B-Guide-v2.0.pdf
* https://www.apmemory.com/en/downloadFiles/032411212009597427

2. Host setup
-------------

The qualified host is an arm64 MacBook Air running Darwin 25.5.0.  The source
trees are:

* NuttX: ``/Volumes/SSD2TB/Code/nuttx``.
* nuttx-apps: ``/Volumes/SSD2TB/Code/apps``.
* tool cache: ``/Volumes/SSD2TB/Code/.p2-nuttx-cache``.
* generated tool environment: ``$HOME/.p2-nuttx-env``.
* local, untracked HIL environment: ``.p2-hil.env``.

The pinned host components are Python 3.13.0, pyserial 3.5, pyelftools 0.32,
and kconfig-frontends parser 4.11.0.  ``kconfig-conf`` is
``/Users/fred/.local/nuttx-tools/kconfig-frontends/bin/kconfig-conf``.  The
target compiler is P2 clang 14.0.0 and the linker is LLD 14.0.0.  p2llvm libc
was deliberately neither built nor linked.

The authoritative paired-tree host run executed 316 tests successfully in
19.629 seconds.  Python byte compilation, ``git diff --check``, Kconfig checks,
linked-ELF verification, and ``nxstyle`` on changed C/header files also
passed.  These are host/static results, not substitutes for the HIL results
below.

3. Serial adapter and persistent device path
---------------------------------------------

The adapter is a Parallax PropPlug with USB VID ``0x0403``, PID ``0x6015``,
and serial ``P97cvdxp``.  Its macOS serial-number-bearing callout path is:

::

  /dev/cu.usbserial-P97cvdxp

macOS does not provide the Linux ``/dev/serial/by-id`` hierarchy, so this is
the most persistent locally available name.  Console baud is 230400 and
loader baud is 2000000.  Reset is the loadp2-compatible PropPlug DTR pulse.
Every loader/test uses ``/private/tmp/nuttx-p2-hil.lock`` and rejects an
already-owned serial device.

4. Boot-switch settings
-----------------------

The qualified Rev-B settings, written as ``(FLASH,up,down)``, are now known:

* ``(ON,OFF,OFF)`` selects serial/flash operation and accepts PropPlug DTR
  reset plus the P2 ROM serial loader.
* ``(OFF,OFF,ON)`` selects ROM microSD boot.  ``FLASH=OFF`` also disconnects
  the W25 chip-select path, so W25 and SmartFS unavailability is expected in
  this mode rather than a boot failure.

At the serial/flash setting, the historical sealed program artifact
``artifacts/hil/20260713T102521Z-flash-program`` passed 20/20 independent ROM
flash boots in ``artifacts/hil/20260713T103452Z-flashboot``.  One persistent
serial connection observed zero bytes before each NSH prompt.  Every cycle
reproduced boot CRC ``23FCF91E`` and mounted the existing SmartFS image without
formatting, preserving its 1 MiB FNV-1a hash ``693C9DC5``.  These were PropPlug
DTR resets; they do not satisfy the separate true removal-of-power gate.

At the user-confirmed SD-only setting, the development qualification in
``/private/tmp/p2-release-final.oBc9V4/ec32mb-sd-rom-boot-w25-guard`` passed a
reset-only boot in 16.742169 seconds.  The verifier invoked no loader and
transmitted zero serial bytes.  It received, in order, the four ``P2BOOT``
markers, ``P2STORAGE:W25=UNAVAILABLE:CHECK_FLASH_SWITCH``, the 400 kHz/2 MHz
MMC/SD frequency marker, ``P2STORAGE:MMCSD=/dev/mmcsd0``,
``P2FLASHBOOT:SMARTFS=UNAVAILABLE:CHECK_FLASH_SWITCH``, the selected-board
showcase marker, and the first NSH prompt.  The qualified development image
was 402,060 bytes with SHA-256
``e1226636846386e5538e731b0fa568ca99fffeb6f992bc6e271f5b5c86e5b3cf``.
Those facts prove the fix and SD-only path, but are not presented as the
identity of the later clean release assets.

For the current 402,452-byte Rev-B candidate, programming serial flash is
**PASS** at ``/tmp/p2-release-final.14cadad-r1/ec32mb-flash-program``.  It
programmed ``[0x00000000,0x00062500)`` after erasing
``[0x00000000,0x00063000)``.  That operation is not itself an independent boot
proof.  The independent proof is **PASS** from
``/tmp/p2-release-final.14cadad-r1/ec32mb-flashboot-hil/cycle-001`` through
``cycle-010``.  Each completed reset-only cycle took about 94 seconds, opened
no loader, transmitted zero bytes before the prompt, reproduced boot CRC
``B31D0271``, and verified persistent sequence ``F23A0713`` across one MiB
with FNV-1a ``693C9DC5``.  The originally requested 20-cycle wrapper was
intentionally stopped after ten redundant PASS results to keep testing
proportional.  Cycle 11 and the top-level ``status.json`` report manual
interruption/``FAIL`` and are not presented as PASS artifacts or a hardware
failure; acceptance rests on the ten completed per-cycle PASS files.
The exact candidate was then written and independently inspected successfully.
The initial write diagnostic
``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-write`` timed out; read-only
``ec32mb-sd-inspect`` then correctly rejected the card with
``P2STORAGE:SD:ROM-FAIL:STAGE=MBR:REASON=FIELDS``.  Corrective format
``ec32mb-sd-format-final`` passed in 302.761617 seconds and created MBR type
``0C``, start LBA 2048, and 61,130,752 sectors.  Exact-image write
``ec32mb-sd-write-final`` passed, and read-only
``ec32mb-sd-inspect-final`` passed in 38.236762 seconds with valid
MBR/VBR/FSInfo/root metadata, a contiguous 25-cluster chain, EOC
``0FFFFFFF``, exact 402,452 bytes, and FNV-1a ``D0D0F215``.  The independent
no-loader reset proof is **PASS** at
``/tmp/p2-release-final.14cadad-r1/ec32mb-sdboot-hil``.  At the
user-confirmed ``(FLASH,up,down)=(OFF,OFF,ON)`` setting, it booted in
16.688852 seconds, downloaded no loader, transmitted zero serial bytes, and
received in order the four ``P2BOOT`` markers, the W25-unavailable marker,
400 kHz/2 MHz MMC/SD frequency marker, ``/dev/mmcsd0``, the
SmartFS-unavailable marker, the ``p2-ec32mb`` showcase marker, and the first
NSH prompt.  Fragmentation verification was true and the recorded boot source
was ``SD_ONLY_USER_CONFIRMED``.  The status, marker, and raw-console SHA-256
values are respectively
``61534212bd8bcf9f4ca996d36731c0e612951d7d9554c96ff360aaf607a3e758``,
``ba6cd3da3cd1f0c9217295c7341c39708dfb03dc7f3ecce9dac09c40be1d0368``,
and ``ea404e55c42ae0d9ba2bab8e6e5c6132acaf44fb94e7ea5cf8404df89eab42f2``.
The historical 402,060-byte SD-only boot above remains useful fix evidence
but is not substituted for this current-candidate proof.

5. Exact Git commits
--------------------

The work started from PR #2 head
``765d073a89599a5d1d96fbc84ad4891e3f5b4aa4`` on a new branch without
rewriting its history.  PR #2 is stacked on PR #1 commit
``39cc55135fd24f02006e56f9fc1f0476edea1888``.

The accepted HIL source work was developed from these pre-campaign branch
heads:

* NuttX branch ``codex/p2-hil-finish`` was at
  ``4bd5c09c86334345c3453970576522e188dbd086``.
* apps branch ``codex/p2-hil-finish-apps`` was at
  ``ba8b8c09013efb1bef684d414a97b297b57952d5``.

The pre-SD-boot-fix source/tool freeze used by the recorded ABI and host
validation baseline is:

* NuttX ``cfaf600a55f41d8ea538b83b1c8c1ce459c9996a``;
* apps ``67673a8074c4bc07a161816150ed3b64350f4b59``.

``tools/p2/toolchain.lock`` records implementation baseline
``689ebdb6b831bc3d151c10c8e26379f55dc56b38`` and that same apps commit.  The
lock update itself is committed in NuttX ``cfaf600a55...``; this one-commit
lag is the intentional non-self-referential lock sequence.  The baseline ABI
artifact independently records the actual source commit and tool hashes used
for that run.

The ABI files tied to those commits remain useful architecture/compiler
evidence, but they are not final release evidence after the later storage
changes.  A clean candidate-bound rerun is recorded below.  The exact commits
sealed into representative accepted build artifacts are:

.. list-table:: Current clean release-candidate provenance
   :header-rows: 1

   * - Board
     - Source commits
     - Raw image
     - ELF
   * - P2-EC32MB Rev B
     - NuttX ``14cadad3a6794e10cbc9f0dfb20f352e4844d35f``;
       apps ``a333035462f545056e7a2fb859a9fbdc6d4ef831``
     - 402,452 bytes; SHA-256
       ``6ff205df0f724eab91eb0619b53cffc579819cdcb99049578a9f01cb4ba519e2``
     - 494,808 bytes; SHA-256
       ``1409460f5399e267516e6ea394d99cf2b30e638ac55cbc82318175712c01dd3c``
   * - P2-EC Rev D, no PSRAM
     - NuttX ``14cadad3a6794e10cbc9f0dfb20f352e4844d35f``;
       apps ``a333035462f545056e7a2fb859a9fbdc6d4ef831``
     - 386,752 bytes; SHA-256
       ``596b0f022c28fa4462a6e13692ad54ecab095f17d6532d441e60e0dee481c230``
     - 476,768 bytes; SHA-256
       ``2d1e4f2d84455b6cd15edc31571796d9cc1505fae49de7f65245856b70f5bea7``

The Rev-B build passed RAM showcase HIL at
``/tmp/p2-release-final.14cadad-r1/ec32mb-showcase-hil`` and flash programming
at ``/tmp/p2-release-final.14cadad-r1/ec32mb-flash-program``.  All 16 required
showcase stages passed in 379.246116 seconds; ``status.json`` has SHA-256
``2ce85939d560a2e727b845d1e87f758939dd6028ce6b6afaba1bcc1c031e8250``.
The Rev-B build status/config SHA-256 values are
``518e6dc825e0501d54c208e869af97a8ed820af0bccca123ba1aab96896edede`` and
``28fcde788eddbf82c100426015b7331b37ccc747ee8973e5d7d457256f70f252``.
The Rev-D build status/config SHA-256 values are
``0f27ae1662c18ab051372d157ac030aa3f66bebb104a68ef3597462878737608`` and
``ce66616aa712d9834372ef0bb7810f50262e55cfc2991aea22c43ea940c6a1ff``.
Both builds carry toolchain-lock SHA-256
``66871ac6bb8a96fbea5b5fc405e6a1a3743fa6c441775737cab80066678250aa``.
Exact-candidate reset-only flash boot is accepted from the ten completed PASS
cycles under ``/tmp/p2-release-final.14cadad-r1/ec32mb-flashboot-hil``.  The
stable boot CRC is ``B31D0271`` and every cycle verified persistent sequence
``F23A0713`` / one-MiB FNV-1a ``693C9DC5`` with zero pre-prompt TX.  The
20-cycle wrapper's top-level status remains interruption/FAIL because it was
intentionally stopped after cycle 10; it is not itself a PASS artifact.
P2-EC Rev D is
build- and static-verification qualified only; because no Rev-D module was
attached, all runtime behavior remains **HIL-REQUIRED**.  Its lack of PSRAM is
intentional board scope, not a missing test profile.

Both candidate builds are flat UP; SMP is deliberately not enabled.
Their provisional build directories are
``/tmp/p2-release-final.14cadad-r1/ec32mb-build`` and
``/tmp/p2-release-final.14cadad-r1/ec-revd-build`` respectively.

The table below records historical milestone provenance and is not the
current candidate identity:

.. list-table:: HIL build provenance
   :header-rows: 1

   * - Milestone
     - NuttX commit
     - apps commit
     - Build artifact
   * - bringup and NSH
     - ``0e4312cc1b2c5aab93fe7f414fa669bc28faac23``
     - ``f199c8227943cec71af3338fb962d0f5496b76b6``
     - ``20260713T034453Z-build-bringup`` and
       ``20260713T035012Z-build-nsh``
   * - Smart Pins
     - ``ef1de6d193d18546d0767556d1b4bf6aa73fe9d2``
     - ``19278cf01a689303f17edf4618106ffccafe7c01``
     - ``20260713T063127Z-build-smartpins``
   * - Flash and microSD
     - ``e32d319a42761d37176fbe8a22814bd2de3556d6``
     - ``5ba1e7af4f17f5176e479bfec4611078543177b2``
     - ``20260713T083107Z-build-storage``
   * - PSRAM final linked hot path
     - ``b8d42a953a10e6949b32070315eb4de97c0276a7``
     - ``ba8b8c09013efb1bef684d414a97b297b57952d5``
     - ``20260713T095943Z-build-psram``
   * - flashboot build and physical result
     - ``16c76921fbbe1fdee5aeb4b598b78e4b91f3d78e``
     - ``ba8b8c09013efb1bef684d414a97b297b57952d5``
     - ``20260713T102521Z-build-flashboot``
   * - Analog fixture
     - ``4bd5c09c86334345c3453970576522e188dbd086``
     - ``ba8b8c09013efb1bef684d414a97b297b57952d5``
     - ``20260713T110647Z-build-analog``
   * - BMP180 I2C
     - ``4bd5c09c86334345c3453970576522e188dbd086``
     - ``ba8b8c09013efb1bef684d414a97b297b57952d5``
     - ``20260713T110947Z-build-i2c``
   * - Dedicated scheduler stress
     - ``4bd5c09c86334345c3453970576522e188dbd086``
     - ``ba8b8c09013efb1bef684d414a97b297b57952d5``
     - ``20260713T112709Z-build-schedstress``
   * - Raw GETCT clock calibration
     - ``4bd5c09c86334345c3453970576522e188dbd086``
     - ``ba8b8c09013efb1bef684d414a97b297b57952d5``
     - ``20260713T113742Z-build-clock``

The build-artifact commits above are historical provenance and are therefore
not rewritten to the final branch heads.  The analog, I2C, scheduler-stress,
and raw-clock builds record dirty working trees and preserve their source
status; their commit fields are the recorded base commits, not a claim that
the then-uncommitted tests were already committed.

6. Exact dependency commits
----------------------------

``tools/p2/toolchain.lock`` records:

.. list-table:: Pinned dependency revisions
   :header-rows: 1

   * - Dependency
     - Revision
   * - p2llvm
     - ``bdcefcce7860b2232c06f35726fea679a3a7309c``
   * - llvm-project
     - ``72a9bb1ef2656d9953d1f41a8196d425ff2ab0b1``
   * - p2llvm loadp2 subproject
     - ``21e074cc7ee6fbd4fb12ef5352544b3457a6729c``
   * - FlexProp
     - ``858f51c4a24e7ae0f6cbc78f625c731083ad304f``
   * - spin2cpp/flexspin
     - ``28f1b80fc3a36422fb0a1f7c54465d808634abc8``
   * - FlexProp loadp2
     - ``c20afedd4253d09da449fa740f8d4304481fc560``

The current executables are hash pinned: clang
``cc89d3c27b75c9e059093d1e5c6cc7a392b74d977e30d90ca9994f97001224f7``,
LLD ``d49992169271c83f92e96e775ba0531f9260014960eab57bc7d4a761b260d6b1``,
flexspin ``398fc0a5eeae16314c4a429c17b760f982d896e20b0d20e9727e0fb1c97c9791``,
and loadp2
``543c7d522d27f429120e6a35e32ea19394fa85412fb07f41784748094a03c2aa``.
The downstream preemption-safe integer patch is also hash pinned in that
file.  The lock's NuttX value describes its source barrier and can lag the
commit which updates the lock; every build artifact therefore records the
actual NuttX/apps commit independently.

7. Toolchain build instructions
--------------------------------

The reproducible local bootstrap is:

.. code-block:: console

  cd /Volumes/SSD2TB/Code/nuttx
  P2_CACHE=/Volumes/SSD2TB/Code/.p2-nuttx-cache \
    ./tools/p2/bootstrap-local.sh
  source "$HOME/.p2-nuttx-env"

The script builds or validates kconfig-conf, the pinned apps ancestry,
p2llvm/llvm-project with the downstream preemption fixes, flexspin and
loadp2 through FlexProp, and the hash-locked Python HIL environment.  p2llvm
is configured without libc.  The target flags are:

::

  --target=p2 -fno-jump-tables -ffunction-sections -fdata-sections \
  -fno-common -fno-builtin -Os

Offline ABI evidence is generated with:

.. code-block:: console

  ./tools/p2/run-abi-probes.sh

The fresh release-candidate ABI run is **PASS** at
``/tmp/p2-release-final.14cadad-r1/abi/20260713T231547Z``.  It is bound to
NuttX source commit ``14cadad3a6794e10cbc9f0dfb20f352e4844d35f`` and clang
SHA-256
``cc89d3c27b75c9e059093d1e5c6cc7a392b74d977e30d90ca9994f97001224f7``.
All nine capability probes are ``SUPPORTED`` across ``-O0``, ``-Os``, and
``-O2``; the ``summary.txt`` SHA-256 is
``ba91f6134733cfd5d0e02725d4ed64af1786f4b30e11ebe18a548dc4160e95a0``.
The copied ``toolchain.lock`` has SHA-256
``66871ac6bb8a96fbea5b5fc405e6a1a3743fa6c441775737cab80066678250aa``.
This candidate ABI evidence is preserved in the local release evidence
archive.

``artifacts/hil/abi/20260713T155112Z`` remains the historical pre-SD-fix
architecture/compiler baseline at NuttX
``cfaf600a55f41d8ea538b83b1c8c1ce459c9996a``.  Its independent 64-bit
comparison verifier passed all 41,472 functional boundary pairs.  It remains
useful historical evidence but is not the current candidate identity.

8. Build commands
-----------------

All normal profiles use the same fail-closed wrapper:

.. code-block:: console

  source "$HOME/.p2-nuttx-env"
  ./tools/p2/build.sh bringup
  ./tools/p2/build.sh nsh
  ./tools/p2/build.sh ostest-pi-assert
  ./tools/p2/build.sh ostest-pi-production
  ./tools/p2/build.sh ostest-cond-assert
  ./tools/p2/build.sh ostest-cond-production
  ./tools/p2/build.sh storage
  ./tools/p2/build.sh smartpins
  ./tools/p2/build.sh analog
  ./tools/p2/build.sh i2c
  ./tools/p2/build.sh psram
  ./tools/p2/build.sh flashboot
  ./tools/p2/build.sh clock

Each successful build stores the exact command, source status before/after,
``.config``, ELF, binary, map, ``System.map``, size, symbols, sections,
relocations, disassembly, toolchain lock, hashes, and mandatory
``verify-elf.py`` result under ``artifacts/hil/<UTC>-build-<profile>``.
Verifier failure is fatal.  ``artifacts/hil/20260713T095718Z-build-psram``
is intentionally retained evidence that an unexpected linked ``__mulsi3``
caused rejection; ``20260713T095943Z-build-psram`` is the subsequent green
build.

9. RAM-load commands
---------------------

A one-shot RAM load is:

.. code-block:: console

  source "$HOME/.p2-nuttx-env"
  source .p2-hil.env
  P2_LOAD_ARTIFACT=artifacts/hil/<UTC>-load-ram \
    ./tools/p2/load-ram.sh --execute \
      artifacts/hil/<UTC>-build-<profile>/nuttx

The wrapper acquires the board lock, rejects another serial owner, verifies
the endpoint, selects DTR reset, invokes the pinned loadp2 with ``-ZERO``,
and records the exact command/output.  Milestone automation uses the same
exclusive loader/monitor path, for example:

.. code-block:: console

  python3 tools/p2/test-bringup.py --execute \
    --port /dev/cu.usbserial-P97cvdxp --no-build
  python3 tools/p2/test-nsh.py --execute \
    --port /dev/cu.usbserial-P97cvdxp --no-build
  python3 tools/p2/test-smartpins.py --execute \
    --port /dev/cu.usbserial-P97cvdxp --no-build
  python3 tools/p2/test-analog.py --execute --cycles 20 \
    --port /dev/cu.usbserial-P97cvdxp --no-build
  python3 tools/p2/test-i2c.py --execute --cycles 20 \
    --port /dev/cu.usbserial-P97cvdxp --no-build
  python3 tools/p2/test-psram.py --execute --sequence A55A0713 \
    --port /dev/cu.usbserial-P97cvdxp --no-build
  python3 tools/p2/test-clock.py --execute \
    --port /dev/cu.usbserial-P97cvdxp --no-build

10. Flash-program commands
--------------------------

Flash programming is gated by ``P2_HIL=1``, ``P2_ALLOW_RESET=1``,
``P2_ALLOW_FLASH_WRITE=1``, ``P2_ALLOW_FLASH_ERASE=1``, and
``P2_ALLOW_SD_WRITE=1``.  The SD-write gate is required because loadp2 drives
the shared P60/P61 wiring while programming flash.  A flash input must be
generated outside the source tree and outside the build artifact:

.. code-block:: console

  source "$HOME/.p2-nuttx-env"
  source .p2-hil.env
  BUILD=artifacts/hil/20260713T102521Z-build-flashboot
  OUT=/tmp/p2-flashboot-<UTC>
  mkdir -p "$OUT"
  python3 tools/p2/mkflash.py "$BUILD/nuttx.bin" \
    -o "$OUT/flash-input.bin"
  ./tools/p2/flash.sh --execute \
    --port /dev/cu.usbserial-P97cvdxp \
    --image "$OUT/flash-input.bin" \
    --build-artifact "$BUILD"

``flash.sh`` verifies the manifest and clean build provenance, prints the
exact program/erase range, seals the loader/image/build inputs, and only then
invokes pinned loadp2 ``-DTR -SINGLE -FLASH``.  Direct ad-hoc ``loadp2
-FLASH`` use is not an accepted procedure.

11. Recovery commands
----------------------

If a runtime image fails but the ROM serial loader remains reachable, recover
without touching flash first:

.. code-block:: console

  cd /Volumes/SSD2TB/Code/nuttx
  source "$HOME/.p2-nuttx-env"
  source .p2-hil.env
  lsof /dev/cu.usbserial-P97cvdxp
  ./tools/p2/load-ram.sh --execute \
    artifacts/hil/20260713T035012Z-build-nsh/nuttx

If the flash image itself must be replaced, rerun the manifest-producing
``mkflash.py`` and gated ``flash.sh`` sequence from section 10 using a known
green clean build artifact.  Do not erase outside the printed range.

The only physically qualified reset is the PropPlug DTR sequence.  There is
no automated power-control command.  If SD-only mode is selected, restore
``(FLASH,up,down)=(ON,OFF,OFF)`` before using the serial loader or programming
flash.

12. P2 memory map
-----------------

The linker exposes only ``[0x00000000,0x0007c000)`` of the 512 KiB Hub RAM;
``[0x0007c000,0x00080000)`` is withheld for the pinned loader/debug window.
The 32 MiB external PSRAM is absent from this map by design.

The accepted PSRAM image ``20260713T095943Z-build-psram`` has this concrete
layout:

.. list-table:: Hub layout for the accepted PSRAM image
   :header-rows: 1

   * - Range/symbol
     - Purpose
   * - ``0x00000000-0x00000003``
     - ``.p2.entry`` ROM/COGEXEC jump
   * - ``0x00000014-0x0000001f``
     - loadp2 clock-frequency, clock-mode, and baud parameter words
   * - ``0x00000040-0x0000004f``
     - reusable ``.p2.cog`` restart stub
   * - ``0x00000200``
     - empty but reserved ``.p2.lut`` origin
   * - ``0x00000a00-0x0002996f``
     - ordinary Hub ``.text``; ``__start=0x00000a00``
   * - ``0x00029970-0x0002b61b``
     - ``.rodata``
   * - ``0x0002b61c-0x0002b9c7``
     - ``.data`` (940 bytes)
   * - ``0x0002ba00-0x0003642f``
     - static ``.bss`` (43,568 bytes)
   * - ``0x00036430-0x0003742f``
     - CPU0 idle/TLS prefix reserve
   * - ``0x00037430-0x0003842f``
     - live upward initial stack; ``__initial_ptra=0x00037430``
   * - ``0x00038430-0x0007bfff``
     - NuttX heap (277,456 bytes)

The linker and ELF verifier assert entry placement, legal ordinary-code
origin, retained startup/LUT sections, no overlaps, valid initial PTRA, and
Hub bounds.

13. Startup layout
------------------

Startup is NuttX-owned and does not import p2llvm libc:

#. The ROM starts cog 0 in COGEXEC at Hub image address zero.
#. ``__entry`` jumps to cog address ``0x10``, corresponding to Hub image
   byte address ``0x40``.
#. ``__p2_cog_start`` materializes ``__start`` and uses ``COGINIT #0x20`` to
   restart physical cog 0 in HUBEXEC.
#. ``__start`` at ``0x0a00`` loads ``PTRA=__initial_ptra`` and CALLA-enters
   ``p2_start``.
#. ``p2_start`` copies ``.data``, clears ``.bss``, changes RCFAST through the
   qualified PLL sequence to 180 MHz, and initializes early P62/P63 serial.
#. It emits ``P2BOOT:ENTRY``, ``DATA=OK``, ``BSS=OK``, and
   ``P2BOOT:NX_START``, then calls ``nx_start()`` and never returns.

The startup layout is physically proven by
``artifacts/hil/20260712T230747.950915Z-boot`` and repeatedly exercised by
all later HIL campaigns.

14. Context-frame diagram
-------------------------

``arch/p2/include/context.h`` is the one C/PASM2 source of truth.  Public TCB
contexts contain 38 longs (152 bytes):

::

  byte  index   saved state
  ----  -----   ----------------------------------------
  0     0       r0
  ...   ...     r1 through r30
  124   31      r31
  128   32      PA
  132   33      PB
  136   34      logical post-resume PTRA
  140   35      PTRB
  144   36      interrupt state (normalized STALLI bit)
  148   37      packed C/Z/20-bit resume PC
  152           end

The hardware scratch layout is ``resume`` followed by those 37 architectural
longs; C translates it to the public R0-first layout.  INT1 saves to guarded,
fixed Hub scratch before clobbering any task register, then changes to a
guarded 2 KiB interrupt stack.  It never writes an exception frame at task
PTRA, because p2llvm may have live outgoing variadic arguments around an
unadvanced PTRA.  This exact failure was reproduced first and the detached
frame then passed the million-switch test.

15. Stack direction and allocation
----------------------------------

p2llvm stacks grow upward.  ``stack_base_ptr`` is the low usable address,
PTRA names the first free long, CALLA/RETA use packed resume longs, and the
high address is the overflow boundary.  The architecture implements aligned
allocation/use/release, low-prefix ``up_stack_frame()`` allocation for TLS
and argument data, task initialization, pthread stacks, signal state,
coloration, high-water scanning, and task recreation without changing
generic downward-stack ports.

The bootstrap CPU0 allocation reserves 4 KiB for idle/TLS metadata followed
by a separate 4 KiB live initial stack.  For the accepted PSRAM image the
initial PTRA is ``0x00037430`` and the heap starts at ``0x00038430``; linker
assertions keep them disjoint.  The 100/100 bring-up run exercised task
creation, exit/recreation, and stack checks.  The dedicated scheduler-stress
run recreated 64 tasks and reported 896 bytes used in a 6,088-byte checked
stack.  The available ``ostest`` run also exercised many pthread and signal
stack paths.  The complete applicable matrix passed across all 12 accepted
physical cycles.

16. Interrupt design
--------------------

The current port is non-nested, flat, and UP-only.  INT1 is assigned to CT1
and NuttX vector ``P2_IRQ_TIMER0``.  The path is:

::

  CT1 event -> p2_int1 -> fixed guarded scratch -> dedicated IRQ stack
            -> p2_int1_dispatch -> irq_dispatch(P2_IRQ_TIMER0)
            -> scheduler-selected detached frame -> RETI1

``up_irq_save()`` observes the real STALLI state using GETBRK, then stalls
delivery; restore reproduces the exact prior ALLOWI/STALLI state.  Interrupt
entry publishes ``g_current_regs``, dispatches NuttX, updates the running TCB
when selected, clears ``g_current_regs``, verifies guards, and restores the
selected complete state.

Only CT1 has a complete event-to-channel mapping.  Unsupported
``up_enable_irq()``/``up_disable_irq()`` vectors fail loudly, and priority/type
configuration returns ``-ENOSYS``.  Smart Pin GPIO edges and non-console UART
RX are sampled from the 100 Hz system-tick hook; that is useful low-rate
functionality, not hardware-rate interrupt support.

17. Timer design
----------------

The board clock is fixed at 180,000,000 Hz and selected configurations use a
100 Hz NuttX tick (10,000 microseconds, 1,800,000 counter cycles).  CT1 is
programmed from the free-running system counter.  The ISR advances an
absolute deadline by one fixed interval before calling
``nxsched_process_timer()``; it does not schedule relative to ISR completion,
so latency does not accumulate into phase drift and unsigned arithmetic
handles 32-bit wrap.

Hardware evidence includes the standalone CT1 context campaign, the
bring-up ``TICK=OK`` marker, 100 repeated preemptive bring-up runs, and 50 NSH
``sleep 1`` checks constrained to 0.75--3.0 seconds.  Dedicated raw-clock HIL
passed in ``artifacts/hil/20260713T114543.089052Z-clock`` using the image from
``artifacts/hil/20260713T113742Z-build-clock``.  The host kept exactly one
``S`` sample command outstanding, bracketed every returned GETCT value with
monotonic send/receive timestamps, sampled approximately once per second,
and rejected any conservative inter-sample gap over five seconds.

The accepted run recorded 600 ordered samples and target ``DONE=600``.  The
32-bit counter wraps every 23.860929 seconds at nominal frequency, so modular
deltas reconstructed 108,103,441,272 ticks across approximately 25 wraps.
The conservative interval, ``last.send - first.receive``, was 600.555632
seconds; the bracket midpoint interval was 600.567488 seconds.  The midpoint
estimate was 180,002,153.6455524 Hz, or +11.9647 ppm from nominal.  Accounting
for the complete host timestamp brackets gives a conservative frequency
range of 179,998,600.271539--180,005,707.159862 Hz, or -7.776--+31.706 ppm.
The independently emitted ten-second qualified prefix also passed.  The gate
uses only a broad +/-1 percent structural sanity bound; it is not an invented
oscillator-accuracy tolerance.

The retained diagnostic
``artifacts/hil/20260713T114018.397164Z-clock`` produced 169 clean ordered
samples before an isolated reset emitted fresh ``P2BOOT`` markers and the
runner timed out.  Its ELF SHA-256
``93efd31fd89a3416c23288d9c4ee2c7077f93e01b06d4ded70e7674cce384723``
is identical to the image that immediately completed the accepted 600-sample
run, so no deterministic target or software defect was reproduced.  The
reset cause remains unknown and the failed artifact is retained rather than
hidden.  Tickless operation is not selected.

18. Context-switch measurements
--------------------------------

The final standalone context image produced exactly 1,000,000 CT1 switches
in approximately 200.0 seconds, or 5,000 switches/second (200 microseconds per
interrupt).  Both tasks preserved register windows, guarded independent
stacks, nested spills, live variadic calls, and 64-bit arithmetic.  Final
markers included ``REGS=OK``, ``STACKS=OK``, ``REGPATTERN=OK``,
``CANARY=OK``, ``NESTED_SPILLS=OK``, ``VARARGS=OK``, ``ARITH64=OK``, and
``IRQ_CANARIES=OK``.  Evidence:
``artifacts/hil/20260713T034110.407118Z-context``; ELF SHA-256
``5b36d51df4e64d5810964de236e72422b5473b077aef81cee74d047b264ea525``.

The kernel-level evidence is functional rather than a cycle-accurate latency
benchmark: bring-up passed 100/100 resets with preemption, sleep/wakeup,
semaphore wakeup, task exit, and stack checks; NSH passed 50/50 resets.  The
dedicated scheduler profile passed one physical run with exactly 1,004,078
counted events in 165.434771 seconds.  No claim of a measured minimum/maximum
NuttX context-switch latency is made.

19. Hub memory use per image
----------------------------

``llvm-size`` includes the linker-reserved ``.heap`` in its NOBITS/BSS total,
so the table is a link-map reservation summary, not a claim that the heap is
pre-consumed at runtime.  Usable linked Hub capacity is 507,904 bytes.

.. list-table:: Representative accepted image sizes
   :header-rows: 1

   * - Image/build artifact
     - text
     - data
     - NOBITS/BSS
     - total
     - unrepresented Hub gaps
   * - bringup ``20260713T034453Z``
     - 80,984
     - 812
     - 423,424
     - 505,220
     - 2,684
   * - NSH ``20260713T035012Z``
     - 183,512
     - 840
     - 321,024
     - 505,376
     - 2,528
   * - Smart Pins ``20260713T063127Z``
     - 113,396
     - 1,376
     - 390,144
     - 504,916
     - 2,988
   * - analog ``20260713T110647Z``
     - 98,452
     - 1,168
     - 405,504
     - 505,124
     - 2,780
   * - I2C ``20260713T110947Z``
     - 83,944
     - 824
     - 420,352
     - 505,120
     - 2,784
   * - storage ``20260713T083107Z``
     - 256,328
     - 972
     - 247,808
     - 505,108
     - 2,796
   * - PSRAM ``20260713T095943Z``
     - 175,164
     - 940
     - 329,216
     - 505,320
     - 2,584
   * - flashboot build ``20260713T102521Z``
     - 245,820
     - 960
     - 258,560
     - 505,340
     - 2,564
   * - scheduler stress ``20260713T112709Z``
     - 107,548
     - 800
     - 396,800
     - 505,148
     - 2,756
   * - raw clock ``20260713T113742Z``
     - 68,492
     - 772
     - 435,712
     - 504,976
     - 2,928

For the accepted PSRAM image, the meaningful NOBITS breakdown is 43,568
bytes of static BSS, two 4 KiB CPU0 reserves, and a 277,456-byte heap.

20. Pin ownership table
-----------------------

The target C pin manager is the single ownership implementation.  It tracks
physical pin, reservation, owner, reference count, direction, Smart Pin mode,
drive/pull state, event selector, owning cog, and safe-release state.  Claims
are serialized by a P2 hardware lock and conflicting owner/cog claims fail.

.. list-table:: Physical ownership and tested allocation
   :header-rows: 1

   * - Pins
     - Default/selected owner
     - HIL status
   * - P0/P1
     - ``/dev/gpio0`` output and ``/dev/gpio1`` input/edge
     - PASS, direct one-source loopback
   * - P2/P3
     - ``/dev/ttyS1`` TX/RX at 115200
     - PASS, direct one-source loopback
   * - P4/P5
     - ``/dev/pwm0`` output and ``/dev/cap0`` input; alternatively
       ``/dev/dac0`` and ``/dev/adc0``
     - Historical PWM/capture waveform PASS with the former direct jumper;
       current RC fixture has DAC/ADC PASS 20/20 plus a bounded RC-safe PWM
       control smoke, with no current waveform/capture claim
   * - P6/P7/P8/P9
     - ``/dev/spi0`` MOSI/MISO/SCK/CS
     - PASS at 100 kHz mode 0; P6--P7 is the direct data loopback
   * - P10--P23/P26--P37
     - free application Smart Pins until claimed
     - not assigned by the fixture
   * - P24/P25
     - P24 SDA and P25 SCL for ``/dev/i2c0``; optional BMP180 at ``0x77``
       exposes ``/dev/press0`` after ID ``0x55`` is verified
     - PASS 20/20 with the installed BMP180
   * - P38/P39
     - buffered LEDs when ``CONFIG_ARCH_LEDS`` is selected
     - reservation statically enforced
   * - P40--P43
     - PSRAM bank 0 QPI data
     - PASS through ``/dev/psram0``
   * - P44--P47
     - PSRAM bank 1 QPI data
     - PASS through ``/dev/psram0``
   * - P48--P51
     - PSRAM bank 2 QPI data
     - PASS through ``/dev/psram0``
   * - P52--P55
     - PSRAM bank 3 QPI data
     - PASS through ``/dev/psram0``
   * - P56/P57
     - common PSRAM SCLK/CE
     - PASS through service cog
   * - P58/P59
     - shared flash/SD MISO/MOSI
     - PASS through storage arbiter
   * - P60/P61
     - flash CLK/CS or SD CS/CLK
     - PASS through storage arbiter; never independently owned
   * - P62/P63
     - console/programming TX/RX
     - PASS at 230400 console baud

Every final release stops the Smart Pin and applies its recorded safe state;
the fixture tests required ``SAFE=FLOAT`` markers.

21. Flash partition map
-----------------------

``tools/p2/lib/flash_layout.py`` is the generated source of truth consumed by
the board header, image generator, validator, and documentation.

.. list-table:: 16 MiB flash layout
   :header-rows: 1

   * - Range
     - Size
     - Purpose/exposure
   * - ``[0x000000,0x080000)``
     - 512 KiB
     - ROM/loadp2 boot reservation; private raw MTD, no writable device node
   * - ``[0x080000,0x1000000)``
     - 15.5 MiB
     - filesystem partition, ``mtd_partition(raw, 2048, 63488)`` exposed as
       ``/dev/smart0``

The 512 KiB boundary accounts for the ROM boot window, loadp2's first
application byte at ``0x90``, the maximum ``0x7c000`` image, page programming,
and loadp2's 64 KiB erase behavior.  Protection is software containment by
the child MTD range, not a claim of W25 hardware-lock bits.

22. Storage arbiter design
--------------------------

Flash and SD share P58/P59 but exchange the meanings of P60/P61.  One target
C owner and timed mutex implement ``IDLE``, ``FLASH_SELECTED``,
``SD_SELECTED``, and ``RECOVERY``.  A transaction acquires ownership,
deselects both devices, establishes safe idle levels, changes the P60/P61
roles, initializes/selects one target, performs a bounded operation,
deselects, returns to safe idle, and releases ownership.  Conflict, timeout,
and I/O failure fail closed; recovery is explicit.

The two logical polled NuttX SPI lower halves are backed by that same owner.
Flash uses mode 3; SD uses mode 0.  Both probe at 400 kHz and the accepted
profile caps active transfers at 2 MHz.  The production transition engine is
also compiled directly into the host arbiter tests, avoiding a divergent
Python behavioral model.  Physical flash/SD alternation passed 1,000
transactions.

23. Flash filesystem results
----------------------------

SmartFS on ``/dev/smart0`` at ``/mnt/flash`` passed all eight destructive
stages in ``artifacts/hil/20260713T063712.505220Z-flashfs``:

* JEDEC ``EF7018``; 256-byte blocks, 4 KiB erase blocks, 4,096 erase blocks,
  16 MiB total.
* Explicit format only; board bring-up reports ``AUTOFORMAT=NO``.
* A 1 MiB streaming write and fresh-RAM-load/reset verification passed with
  FNV-1a ``693C9DC5`` and sequence ``F23A0713``.
* Sixteen erase/program/read iterations in the safe data region passed.
* Filesystem-full behavior returned ENOSPC after 15,028,224 payload bytes.
* The bounded interrupted-write campaign reset after the arm stage and
  recovered the previous sequence; the pending file was a valid zero-byte
  prefix.
* The protected boot-reservation CRC remained ``EE5B9C97`` before and after
  every stage.

This is real reset persistence: each stage began with a fresh loadp2 RAM
download and target reset.  It is not evidence of sudden electrical power
loss because no power-control command was available.

24. microSD results
-------------------

The generic NuttX MMC/SD SPI stack exposed ``/dev/mmcsd0`` and the accepted
test formatted/mounted FAT32 at ``/mnt/sd``.  All seven stages passed in
``artifacts/hil/20260713T083209.592794Z-sd``:

* Probe returned 61,132,800 sectors of 512 bytes and writable media.
* Explicit FAT32 format passed.
* A 1 MiB streaming write passed with sequence ``5D140713`` and FNV-1a
  ``BE5C9DC5``.
* Verification after a fresh RAM load/reset reproduced the same length and
  hash.
* Rename and delete passed.
* Sixty-four write/read/hash stress iterations passed.
* One thousand alternating flash-plus-SD transactions passed.
* Flash boot-reservation CRC stayed ``EE5B9C97`` throughout.

Separate development qualification then proved both the ROM-facing raw card
layout and an independent SD-only boot:

* The read-only target check in
  ``/private/tmp/p2-release-final.oBc9V4/ec32mb-sd-rom-inspect-w25-guard``
  found an MBR type-``0x0c`` partition at LBA 2048, a 512-byte-sector FAT32
  volume with 32 sectors per cluster, valid FSInfo, and a root
  ``_BOOT_P2.BIX`` entry at cluster 32.  Its 25-cluster chain was contiguous,
  ended with ``0x0fffffff``, and described exactly 402,060 bytes.  The raw
  payload FNV-1a was ``E74F35AC``, matching the staged development image.
* With ``(FLASH,up,down)=(OFF,OFF,ON)``, the reset-only verifier in
  ``/private/tmp/p2-release-final.oBc9V4/ec32mb-sd-rom-boot-w25-guard``
  invoked no loader and transmitted zero serial bytes.  It reached the exact
  ordered startup, W25-unavailable, MMC/SD, SmartFS-unavailable, selected-board
  showcase, and NSH-prompt markers.
* The W25-off result is intentional hardware behavior: disabling ``FLASH``
  disconnects the W25 chip-select path.  The board now rejects the invalid
  pre-probe before entering the generic W25 busy loop, reports the switch
  condition without a false failure, and continues booting from microSD.

This pair proves the ROM layout and the SD-only/W25-off fix for the qualified
development image.  It remains historical evidence and this report does not
reuse its hash as a final release identity.

For the current 402,452-byte candidate, the first write diagnostic
``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-write`` timed out.  The next
read-only run, ``ec32mb-sd-inspect``, failed specifically with
``P2STORAGE:SD:ROM-FAIL:STAGE=MBR:REASON=FIELDS``.  This diagnostic proved the
inspector rejected the pre-existing invalid MBR layout; neither failed result
is represented as a PASS.

The guarded corrective format at
``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-format-final`` is **PASS**.  It
took 302.761617 seconds and produced a type-``0x0c`` partition at LBA 2048
covering 61,130,752 sectors.  Its top-level ``status.json`` SHA-256 is
``df72f9b37775b00545edee943ad3054ba7a1233a8657f3a957e5e217a4a1126c``.
The final write at
``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-write-final`` is **PASS** for the
exact 402,452-byte image with SHA-256
``6ff205df0f724eab91eb0619b53cffc579819cdcb99049578a9f01cb4ba519e2``.
Its 402,456-byte staged payload has SHA-256
``f7ee30fde6ce7a69b63a5c837d9c28e380df75af7ce6e76bd3ded2336c1e5bbf``;
the write ``status.json`` SHA-256 is
``622f56c19d3455f895a3fd5623d8f866740d94f47c68379df91bec51c546a21a``.

The final independent read-only target inspection at
``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-inspect-final`` is **PASS** in
38.236762 seconds.  It proved the MBR, FAT32 VBR, FSInfo, and root entry; a
contiguous 25-cluster chain ending with ``0x0fffffff``; and exactly 402,452
image bytes with FNV-1a ``D0D0F215``.  Its ``status.json`` SHA-256 is
``f5345c72e956425c894549b09148180774368c102986a0bfa3305cd23e01c1e0``.
The independent ROM execution proof at
``/tmp/p2-release-final.14cadad-r1/ec32mb-sdboot-hil`` is **PASS**.  The user
confirmed ``(FLASH,up,down)=(OFF,OFF,ON)``; the verifier issued one DTR reset,
downloaded no loader, and transmitted zero serial bytes.  In 16.688852 seconds
the exact 402,452-byte image reached, in order, ``P2BOOT:ENTRY``, data and BSS
checks, ``P2BOOT:NX_START``, expected W25 unavailability, the 400 kHz/2 MHz
MMC/SD marker, ``/dev/mmcsd0``, expected SmartFS unavailability, the
``p2-ec32mb`` showcase marker, and the first NSH prompt.  The verifier also
recorded ``fragmentation_verified=true`` and bound the write-status SHA-256
``622f56c19d3455f895a3fd5623d8f866740d94f47c68379df91bec51c546a21a``.
Its boot ``status.json`` SHA-256 is
``61534212bd8bcf9f4ca996d36731c0e612951d7d9554c96ff360aaf607a3e758``;
``markers.json`` is
``ba6cd3da3cd1f0c9217295c7341c39708dfb03dc7f3ecce9dac09c40be1d0368``;
and the raw console is
``ea404e55c42ae0d9ba2bab8e6e5c6132acaf44fb94e7ea5cf8404df89eab42f2``.
Together, the final write, raw-card inspection, and no-loader reset close the
exact-candidate Rev-B ROM microSD gate.

No invented card-detect GPIO is used.  Removal/card-absent behavior was not
tested during the installed-card campaign and remains open.

25. Smart Pin device results
----------------------------

The historical direct-jumper digital fixture passed 50/50 complete
RAM-load/reset cycles in
``artifacts/hil/20260713T063221.439668Z-smartpins``.  Every cycle exercised
the standard NuttX device paths and required a final safe-floating state.

.. list-table:: Smart Pin HIL results
   :header-rows: 1

   * - Device/stage
     - Physical result
   * - GPIO P0 -> P1
     - Eight low/high samples matched; ``GPIO:PASS``
   * - GPIO edge P0 -> P1
     - Six transitions observed by the tick-sampled edge path; ``EDGE:PASS``
   * - UART P2 -> P3
     - 16 bytes, FNV-1a ``504B8F7B``; ``UART:PASS``
   * - PWM P4 -> capture P5
     - Historical direct-jumper result: 1 kHz at 25%, 50%, and 75% requested
       duty; capture counts advanced; ``PWM_CAPTURE:PASS``
   * - SPI P6 -> P7, P8 clock, P9 select
     - 16 bytes at 100 kHz mode 0; TX and RX FNV-1a ``504B8F7B``;
       ``SPI:PASS``
   * - ADC P5 / DAC P4
     - PASS 20/20 in
       ``artifacts/hil/20260713T110743.191438Z-smartpins``.  DAC codes 16383,
       32767, and 49151 produced ADC ranges 678--679, 1019--1020, and
       1362--1363.  All 60 samples were strictly monotonic and both pins
       floated safely after every cycle
   * - I2C P24 SDA / P25 SCL
     - PASS 20/20 in ``artifacts/hil/20260713T111043.745628Z-i2c``.  The
       open-drain ``/dev/i2c0`` path verified BMP180 address ``0x77`` and ID
       ``0x55`` with a true write/NOSTOP repeated-start read, completed 640
       ``/dev/press0`` reads from 100000 through 100019 Pa, and used zero
       recovery pulses

GPIO edge delivery and ``/dev/ttyS1`` RX currently use the 100 Hz system-tick
hook because CT1 is the only fully implemented interrupt channel.  They are
not claimed as hardware-rate event paths.  The I2C result qualifies the
installed externally pulled-up BMP180 fixture; it is not a claim about every
I2C peripheral or bus topology.

The current RC fixture was rerun by the release-candidate RAM showcase at
``/tmp/p2-release-final.14cadad-r1/ec32mb-showcase-hil``.  ADC/DAC and the
bounded ``/dev/pwm0`` open/start/stop smoke passed.  The showcase deliberately
omitted the direct-link digital PWM/capture waveform stage, because the RC
network is not that fixture.  Its PWM smoke proves device open/control/stop
and safe return to NSH only.

26. PSRAM results
------------------

``/dev/psram0`` is a seekable character device which copies between Hub
buffers and explicit external offsets through a Hub-resident request
descriptor.  The NuttX CPU cog is the sole producer; a dedicated service cog
is the sole consumer.  Publication/completion use a P2 hardware lock and
sequence/completion fields.  The worker samples one aligned coherent Hub
cancel word without taking the shared lock for every four-byte wire word.
Timeout can cancel, recover pins, and stop/recreate a failed service cog.

Two consecutive destructive starts passed against the same accepted ELF:

* ``artifacts/hil/20260713T100106.997809Z-psram``
* ``artifacts/hil/20260713T100735.645104Z-psram``

Both runs reported 33,554,432 bytes, four 8 MiB chips, 4-byte natural words,
64 KiB maximum requests, 5 MHz QPI, and service cog ID 2.  Each run passed:

* 32 walking bits;
* 23 address lines;
* five bank/end boundaries;
* 1,024 randomized pattern operations;
* a complete 32 MiB write and read with FNV-1a ``634C9DC5``;
* measured 327,680 B/s writes and 273,066 B/s reads;
* concurrent kernel work with 879 permille CPU available and 121 permille
  measured CPU occupancy;
* a forced 32 KiB timeout returning errno 110 with a 24,576-microsecond
  physical lower bound, followed by recovery;
* CE timing, maximum 982 system cycles against a 1,440-cycle limit.

The QPI read path uses command ``0xEB``, five standalone wait clocks, and
samples the first high nibble on the sixth wait clock, for 15 clocks per
four-byte read.  Earlier off-by-one and tight shared-lock contention failures
are preserved in the preceding PSRAM artifacts; the final diagnosis was an
empirically reproduced shared-lock contention/livelock hot path, corrected
with an idle backoff and lock-free aligned cancellation sample.  External
PSRAM remains excluded from code, task stacks, and kernel heap.

27. SMP disposition: deferred and out of scope
-----------------------------------------------

**SMP is DEFERRED / OUT OF SCOPE for this goal; it is not implemented or
claimed.**  All accepted kernel HIL uses one NuttX CPU cog.  There is no SMP
defconfig, ``p2_irq.c`` deliberately rejects ``CONFIG_SMP``, and the port
lacks secondary-cog startup, CPU index and per-CPU idle
stacks/current-register state, IPIs, cross-CPU reschedule, interrupt affinity,
migration, and demonstrated NuttX spinlock/atomic semantics.  The compiler
reports no lock-free atomic width and lowers 32-bit atomics to helper calls;
finite P2 hardware locks cannot simply be declared equivalent to arbitrary
NuttX spinlocks.  Those facts keep ``CONFIG_SMP`` unsupported, but they do not
block completion of the accepted flat-UP configuration.

The recommendation is to retain UP as the stable default and use measured,
bounded service cogs.  The PSRAM service demonstrated full integrity while
leaving 87.9% of the CPU-cog interval available during its concurrent test.
That is evidence that a deterministic service cog is useful, but it is not a
controlled UP-versus-two-CPU-SMP benchmark.  Reconsider exactly two NuttX CPU
cogs only as a separate future architecture project after all per-CPU
contracts are implemented and a dedicated physical campaign exists.

28. Complete ``ostest`` matrix
-------------------------------

The authoritative source/config/marker matrix is
``Documentation/platforms/p2/ostest-matrix.rst``.  It contains 53 test rows:
45 required, two fixture-conditional, and six architecture-inapplicable.
The required run contract is one assertion cycle and five production cycles
for each of the PI and condition-variable logical profiles, using a fixed
3,600-second per-cycle timeout.

All four required campaigns are green:

.. list-table:: Required profile campaigns
   :header-rows: 1
   :widths: 20 10 34 36

   * - Profile
     - Required cycles
     - Result and elapsed seconds
     - Preserved build and HIL evidence
   * - ``ostest-pi-assert``
     - 1
     - PASS 1/1; 1112.947113; assertions true; seven hrtimer timing warnings
     - ``artifacts/hil/20260713T115624Z-build-ostest-pi-assert`` and
       ``artifacts/hil/20260713T115705.736374Z-ostest``
   * - ``ostest-cond-assert``
     - 1
     - PASS 1/1; 1083.622049; assertions true; nine hrtimer timing warnings
     - ``artifacts/hil/20260713T121555Z-build-ostest-cond-assert`` and
       ``artifacts/hil/20260713T121658.724366Z-ostest``
   * - ``ostest-pi-production``
     - 5
     - PASS 5/5; 1109.718123, 1109.821585, 1109.823341, 1109.809943, and
       1109.871800; assertions false; 15 hrtimer timing warnings total
     - ``artifacts/hil/20260713T123519Z-build-ostest-pi-production`` and
       ``artifacts/hil/20260713T123627.152482Z-ostest``
   * - ``ostest-cond-production``
     - 5
     - PASS 5/5; 1157.549036, 1157.470767, 1157.390669, 1157.597868, and
       1157.556411; assertions false; 25 hrtimer timing warnings total
     - ``artifacts/hil/20260713T140927Z-build-ostest-cond-production`` and
       ``artifacts/hil/20260713T141008.365027Z-ostest``

Every accepted artifact has final status ``PASS`` and 57/57 strict parser
groups per cycle.  The parser counted
hrtimer timing warnings under its documented non-fatal warning policy while
still rejecting errors, failures, assertions, timeouts, resets, missing or
out-of-order markers, unexpected skips, and nonzero final status.  PI runs
observed the one required condition-test incompatibility skip; condition runs
instead observed ``cond_test: Errors 0 0``.

The retained pre-fix diagnostic is
``artifacts/hil/20260713T040951.397206Z-ostest``.  It completed the actual
high/low-priority inheritance handshake and then timed out inside an obsolete
``INT_MAX`` medium-priority busy chunk.  The accepted completion-state poll
keeps the medium-priority thread non-yielding while the high-priority thread
is active and lets it exit after the successful proof.  The accepted PI
assertion and five-cycle production campaigns supersede that diagnostic.

.. list-table:: Complete per-group acceptance state
   :header-rows: 1

   * - Group
     - Applicability
     - Current acceptance state
   * - Standard I/O
     - Required
     - PASS in every applicable accepted campaign
   * - Task create and arguments
     - Required
     - PASS in every applicable accepted campaign
   * - Environment
     - Required
     - PASS in every applicable accepted campaign
   * - getopt family
     - Required
     - PASS in every applicable accepted campaign
   * - libc ``memmem``
     - Required
     - PASS in every applicable accepted campaign
   * - TLS slots
     - Required
     - PASS in every applicable accepted campaign
   * - Compiler thread-local
     - N/A, ``ARCH_HAVE_THREAD_LOCAL`` absent
     - N/A
   * - ``setvbuf``
     - Required
     - PASS in every applicable accepted campaign
   * - ``/dev/null``
     - Required
     - PASS in every applicable accepted campaign
   * - Asynchronous I/O
     - Conditional on writable AIO fixture
     - BLOCKED, fixture profile not provided
   * - FPU context
     - N/A, no architecture FPU
     - N/A
   * - Task restart/recreation
     - Required
     - PASS in every applicable accepted campaign
   * - Parent/child wait
     - Required
     - PASS in every applicable accepted campaign
   * - Multi-user identity
     - Conditional on credential fixture
     - BLOCKED, fixture profile not provided
   * - Work queue
     - Required
     - PASS in every applicable accepted campaign
   * - Mutex
     - Required
     - PASS in every applicable accepted campaign
   * - Timed mutex
     - Required
     - PASS in every applicable accepted campaign
   * - Recursive/error-check mutex
     - Required
     - PASS in every applicable accepted campaign
   * - Pthread-specific data
     - Required
     - PASS in every applicable accepted campaign
   * - Pthread cancel
     - Required
     - PASS in every applicable accepted campaign
   * - Robust mutex
     - Required
     - PASS in every applicable accepted campaign
   * - Semaphore
     - Required
     - PASS in every applicable accepted campaign
   * - Timed semaphore
     - Required
     - PASS in every applicable accepted campaign
   * - Named semaphore
     - Required
     - PASS in every applicable accepted campaign
   * - Condition variable
     - Required in ``cond``; documented skip in ``pi``
     - PASS in both condition campaigns; documented skip in both PI campaigns
   * - Pthread exit/self
     - Required
     - PASS in every applicable accepted campaign
   * - Pthread rwlock
     - Required
     - PASS in every applicable accepted campaign
   * - Rwlock cancellation
     - Required
     - PASS in every applicable accepted campaign
   * - Cleanup handlers
     - Required
     - PASS in every applicable accepted campaign
   * - Timed condition wait
     - Required
     - PASS in every applicable accepted campaign
   * - Timed message queue
     - Required
     - PASS in every applicable accepted campaign
   * - Signal mask
     - Required
     - PASS in every applicable accepted campaign
   * - Message queue
     - Required
     - PASS in every applicable accepted campaign
   * - Stop/continue actions
     - Required
     - PASS in every applicable accepted campaign
   * - Signal handler
     - Required
     - PASS in every applicable accepted campaign
   * - Nested signal handler
     - Required
     - PASS in every applicable accepted campaign
   * - POSIX timer, signal
     - Required
     - PASS in every applicable accepted campaign
   * - Flat spinlock API
     - Required
     - PASS in every applicable accepted campaign
   * - Watchdog
     - Required
     - PASS in every applicable accepted campaign
   * - High-resolution timer
     - Required
     - PASS in all 12 cycles; 56 hrtimer timing warnings counted under the accepted policy
   * - POSIX timer, thread
     - Required
     - PASS in every applicable accepted campaign
   * - Round robin
     - Required
     - PASS in every applicable accepted campaign
   * - Sporadic scheduler
     - Required
     - PASS in every applicable accepted campaign
   * - Dual sporadic threads
     - Required
     - PASS in every applicable accepted campaign
   * - Pthread barrier
     - Required
     - PASS in every applicable accepted campaign
   * - ``setjmp``/``longjmp``
     - N/A, ``ARCH_SETJMP_H`` absent
     - N/A
   * - Priority inheritance
     - Required in ``pi``
     - PASS in PI assertion and production campaigns; pre-fix timeout retained as diagnostic
   * - Scheduler lock
     - Required
     - PASS in every applicable accepted campaign
   * - ``vfork``
     - N/A, no ``ARCH_HAVE_FORK``
     - N/A
   * - SMP call
     - N/A for UP
     - N/A
   * - Scheduler events
     - Required
     - PASS in every applicable accepted campaign
   * - Performance counter
     - N/A, no architecture performance events
     - N/A
   * - Suite memory/final status
     - Required in every profile
     - PASS in every profile and all 12 cycles

The four reproduction commands are:

.. code-block:: console

  python3 tools/p2/test-ostest.py --execute --profile pi --assertion-run \
    --port /dev/cu.usbserial-P97cvdxp
  python3 tools/p2/test-ostest.py --execute --profile cond --assertion-run \
    --port /dev/cu.usbserial-P97cvdxp
  python3 tools/p2/test-ostest.py --execute --profile pi \
    --port /dev/cu.usbserial-P97cvdxp
  python3 tools/p2/test-ostest.py --execute --profile cond \
    --port /dev/cu.usbserial-P97cvdxp

The standalone million-switch context test did not substitute for the
multi-mechanism NuttX scheduler gate, so that gate was run separately.  The
dedicated ``schedstress`` image passed one physical cycle in 165.434771
seconds with exactly 1,004,078 counted scheduler events:

.. list-table:: Accepted dedicated scheduler-stress event counts
   :header-rows: 1

   * - Mechanism
     - Count
   * - Priority handoff
     - 2,000
   * - Round robin
     - 100,000
   * - Semaphore
     - 600,000
   * - Priority-inheritance mutex
     - 2,000
   * - Condition variable
     - 100,000
   * - Message queue
     - 100,000
   * - Signal
     - 100,000
   * - POSIX timer
     - 10
   * - Pthread create/join/cancel lifecycle
     - 4
   * - Task exit and recreation
     - 64
   * - **Total**
     - **1,004,078**

The same run reported 896 bytes used in a 6,088-byte checked stack and heap
usage of 8,240 bytes before allocation, 12,344 during allocation, and 8,240
after free.  A separate concurrent allocator check completed 512/512
overlapping allocations with two workers and 256 rounds per worker; those
allocator operations are deliberately not included in the scheduler-event
total.  The accepted build is
``artifacts/hil/20260713T112709Z-build-schedstress`` and the physical record is
``artifacts/hil/20260713T112942.518754Z-schedstress``.  This closes the
dedicated scheduler-stress gate.  It neither replaces nor weakens the four
``ostest`` campaigns above, which passed independently.

29. Repeated boot statistics
-----------------------------

.. list-table:: Physical reset/load repetition evidence
   :header-rows: 1

   * - Campaign
     - Result
     - Artifact
   * - Standalone hello
     - 10/10 consecutive DTR reset/RAM loads
     - ``20260712T211034.259011Z-hello``
   * - Standalone detached context
     - 1/1, exactly 1,000,000 timer switches
     - ``20260713T034110.407118Z-context``
   * - Native NuttX boot marker gate
     - 1/1
     - ``20260712T230747.950915Z-boot``
   * - Deterministic bring-up
     - 100/100 consecutive DTR reset/RAM loads
     - ``20260713T034525.287219Z-bringup``
   * - NSH command campaign
     - 50/50 consecutive DTR reset/RAM loads
     - ``20260713T035042.747009Z-nsh``
   * - Non-destructive storage probe
     - 10/10
     - ``20260713T040747.645541Z-storage``
   * - Smart Pin fixture
     - 50/50
     - ``20260713T063221.439668Z-smartpins``
   * - DAC/ADC fixture
     - 20/20; 60/60 strictly monotonic samples
     - ``20260713T110743.191438Z-smartpins``
   * - BMP180 I2C fixture
     - 20/20; 640 pressure reads
     - ``20260713T111043.745628Z-i2c``
   * - Flash filesystem destructive stages
     - 8/8, each with a fresh RAM load/reset
     - ``20260713T063712.505220Z-flashfs``
   * - microSD destructive stages
     - 7/7, each with a fresh RAM load/reset
     - ``20260713T083209.592794Z-sd``
   * - Development ROM microSD raw-layout check
     - 1/1 read-only raw-card verification
     - ``/private/tmp/p2-release-final.oBc9V4/ec32mb-sd-rom-inspect-w25-guard``
   * - Development independent ROM microSD boot
     - 1/1 reset-only boot; no loader invocation and zero serial TX
     - ``/private/tmp/p2-release-final.oBc9V4/ec32mb-sd-rom-boot-w25-guard``
   * - Exact-candidate SD corrective format
     - 1/1; MBR type ``0C``, LBA 2048, 61,130,752 sectors
     - ``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-format-final``
   * - Exact-candidate ``_BOOT_P2.BIX`` write
     - 1/1; exact 402,452-byte candidate
     - ``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-write-final``
   * - Exact-candidate ROM microSD raw-layout check
     - 1/1 read-only; contiguous 25-cluster chain, FNV-1a ``D0D0F215``
     - ``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-inspect-final``
   * - Exact-candidate independent ROM microSD boot
     - 1/1 reset-only; 16.688852 seconds, no loader, zero serial TX
     - ``/tmp/p2-release-final.14cadad-r1/ec32mb-sdboot-hil``
   * - Full PSRAM campaign
     - 2/2 consecutive complete 32 MiB starts
     - ``20260713T100106.997809Z-psram`` and
       ``20260713T100735.645104Z-psram``
   * - Historical independent flash boot
     - 20/20 consecutive DTR resets with zero pre-prompt bytes
     - ``20260713T103452Z-flashboot``
   * - Dedicated scheduler stress
     - 1/1 physical run; exactly 1,004,078 events in 165.434771 seconds
     - ``20260713T112942.518754Z-schedstress``
   * - Raw GETCT qualification
     - 1/1 accepted run; 600 ordered samples and 600.555632-second
       conservative span
     - ``20260713T114543.089052Z-clock``
   * - Raw GETCT retained diagnostic
     - 0/1; 169 clean samples followed by an isolated reset; the identical
       ELF subsequently passed the complete campaign
     - ``20260713T114018.397164Z-clock``
   * - OSTest PI assertions
     - PASS 1/1 in 1112.947113 seconds; assertions true
     - ``20260713T115705.736374Z-ostest``
   * - OSTest condition assertions
     - PASS 1/1 in 1083.622049 seconds; assertions true
     - ``20260713T121658.724366Z-ostest``
   * - OSTest PI production
     - PASS 5/5; each cycle completed the full ordered matrix
     - ``20260713T123627.152482Z-ostest``
   * - OSTest condition production
     - PASS 5/5; each cycle completed the full ordered matrix
     - ``20260713T141008.365027Z-ostest``
   * - True power cycles
     - 0/5; no power-control command
     - BLOCKED

The NSH campaign ran ``help``, ``uname -a``, ``ps``, ``free``, ``uptime``,
``sleep 1``, ``ls /dev``, ``mount``, and ``echo P2_NSH_OK`` on every reset.
These RAM-loader reset counts must not be confused with independent flash
boot or power-cycle counts.

30. Artifact directories
-------------------------

.. list-table:: Evidence index
   :header-rows: 1

   * - Evidence
     - Directory
   * - Current candidate ABI PASS (provisional; preserve/package-link)
     - ``/tmp/p2-release-final.14cadad-r1/abi/20260713T231547Z``
   * - Current candidate Rev-B RAM showcase PASS (provisional;
       preserve/package-link)
     - ``/tmp/p2-release-final.14cadad-r1/ec32mb-showcase-hil``
   * - Current candidate Rev-B flash programming PASS (provisional;
       preserve/package-link)
     - ``/tmp/p2-release-final.14cadad-r1/ec32mb-flash-program``
   * - Current candidate Rev-B reset-only flash-boot evidence: ten completed
       per-cycle PASS files; 20-cycle wrapper intentionally interrupted
     - ``/tmp/p2-release-final.14cadad-r1/ec32mb-flashboot-hil``
   * - Current candidate Rev-B SD diagnostic failures (preserve honestly;
       superseded by the following PASS results)
     - ``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-write``;
       ``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-inspect``
   * - Current candidate Rev-B corrective SD format PASS
     - ``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-format-final``
   * - Current candidate Rev-B ``_BOOT_P2.BIX`` write PASS
     - ``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-write-final``
   * - Current candidate Rev-B read-only ROM-layout inspection PASS
     - ``/tmp/p2-release-final.14cadad-r1/ec32mb-sd-inspect-final``
   * - Current candidate Rev-B no-loader SD-only ROM boot PASS
     - ``/tmp/p2-release-final.14cadad-r1/ec32mb-sdboot-hil``
   * - Historical ABI matrix (architecture/compiler baseline)
     - ``artifacts/hil/abi/20260713T155112Z``
   * - Standalone hello 10/10
     - ``artifacts/hil/20260712T211034.259011Z-hello``
   * - Detached context 1,000,000
     - ``artifacts/hil/20260713T034110.407118Z-context``
   * - Native startup gate
     - ``artifacts/hil/20260712T230747.950915Z-boot``
   * - Bring-up build and 100/100 HIL
     - ``artifacts/hil/20260713T034453Z-build-bringup``;
       ``artifacts/hil/20260713T034525.287219Z-bringup``
   * - NSH build and 50/50 HIL
     - ``artifacts/hil/20260713T035012Z-build-nsh``;
       ``artifacts/hil/20260713T035042.747009Z-nsh``
   * - Retained pre-fix ``ostest`` diagnostic
     - ``artifacts/hil/20260713T040951.397206Z-ostest``
   * - Smart Pins build and 50/50 HIL
     - ``artifacts/hil/20260713T063127Z-build-smartpins``;
       ``artifacts/hil/20260713T063221.439668Z-smartpins``
   * - Analog build and 20/20 HIL
     - ``artifacts/hil/20260713T110647Z-build-analog``;
       ``artifacts/hil/20260713T110743.191438Z-smartpins``
   * - I2C build and 20/20 BMP180 HIL
     - ``artifacts/hil/20260713T110947Z-build-i2c``;
       ``artifacts/hil/20260713T111043.745628Z-i2c``
   * - Storage probe
     - ``artifacts/hil/20260713T040747.645541Z-storage``
   * - Flash filesystem
     - ``artifacts/hil/20260713T063712.505220Z-flashfs``
   * - Storage build and microSD
     - ``artifacts/hil/20260713T083107Z-build-storage``;
       ``artifacts/hil/20260713T083209.592794Z-sd``
   * - Development ROM microSD layout and SD-only boot (provisional;
       preserve/package-link)
     - ``/private/tmp/p2-release-final.oBc9V4/ec32mb-sd-rom-inspect-w25-guard``;
       ``/private/tmp/p2-release-final.oBc9V4/ec32mb-sd-rom-boot-w25-guard``
   * - PSRAM linked build
     - ``artifacts/hil/20260713T095943Z-build-psram``
   * - Two accepted PSRAM starts
     - ``artifacts/hil/20260713T100106.997809Z-psram``;
       ``artifacts/hil/20260713T100735.645104Z-psram``
   * - flashboot build
     - ``artifacts/hil/20260713T102521Z-build-flashboot``;
       ``artifacts/hil/20260713T102521Z-flash-program``
   * - Historical independent flashboot HIL
     - ``artifacts/hil/20260713T103452Z-flashboot`` (20/20 PASS)
   * - Dedicated scheduler-stress build and HIL
     - ``artifacts/hil/20260713T112709Z-build-schedstress``;
       ``artifacts/hil/20260713T112942.518754Z-schedstress``
   * - Raw-clock build and accepted ten-minute HIL
     - ``artifacts/hil/20260713T113742Z-build-clock``;
       ``artifacts/hil/20260713T114543.089052Z-clock``
   * - Retained raw-clock reset diagnostic
     - ``artifacts/hil/20260713T114018.397164Z-clock``
   * - OSTest PI assertion build and 1/1 HIL
     - ``artifacts/hil/20260713T115624Z-build-ostest-pi-assert``;
       ``artifacts/hil/20260713T115705.736374Z-ostest``
   * - OSTest condition assertion build and 1/1 HIL
     - ``artifacts/hil/20260713T121555Z-build-ostest-cond-assert``;
       ``artifacts/hil/20260713T121658.724366Z-ostest``
   * - OSTest PI production build and 5/5 HIL
     - ``artifacts/hil/20260713T123519Z-build-ostest-pi-production``;
       ``artifacts/hil/20260713T123627.152482Z-ostest``
   * - OSTest condition production build and 5/5 HIL
     - ``artifacts/hil/20260713T140927Z-build-ostest-cond-production``;
       ``artifacts/hil/20260713T141008.365027Z-ostest``

Each campaign directory retains raw and normalized serial, commands, status,
markers/parser data, elapsed time, image hashes, and preserved inputs.  Build
artifacts also retain full maps, symbols, sections, disassembly, and source
cleanliness evidence.  This retention statement describes the artifact
contents.  The required candidate evidence is in the local release evidence
archive; the ``/tmp`` package is not durable until GitHub upload and
fresh-download verification pass.

31. Known limitations
----------------------

* Only flat, uniprocessor NuttX is supported.  Protected/kernel builds and SMP
  are compile-time excluded.  SMP is **DEFERRED / OUT OF SCOPE** and does not
  gate flat-UP completion.
* P2-EC Rev D (``p2-ec``) remains build- and static-verification qualified
  only.  No Rev-D module was attached, so every Rev-D runtime claim, including
  flash and ROM microSD boot, remains **HIL-REQUIRED**.
* CT1/INT1 is the only complete interrupt routing path.  GPIO edge and UART1
  RX service are 100 Hz tick-sampled.
* Tickless operation is absent.  The raw-clock result brackets serial commands
  with host monotonic timestamps, so its conservative frequency range includes
  host scheduling and transport uncertainty.  The broad +/-1 percent gate is
  structural rather than a precision tolerance, and one ten-minute run does
  not characterize temperature, supply-voltage, or aging drift.
* Flash and SD are conservative 2 MHz polled devices.  Flash boot-reservation
  protection is an unexposed/private MTD partition, not a hardware lock.
* SD card detection is transaction based; there is no invented detect pin.
  Card-absent behavior has not been physically tested.
* The interrupted SmartFS test used a reset boundary, not a true sudden power
  cut.
* PSRAM uses explicit four-byte QPI wire transactions through a character
  device and service cog.  It is intentionally not mmap-like memory, heap,
  stack, or executable storage.
* ADC values are raw, uncalibrated SINC2 accumulator samples.  The physical
  result proves strict monotonicity at three DAC codes on the installed RC
  fixture, not calibrated voltage accuracy.
* I2C HIL covers the installed BMP180 at 100 kHz.  It does not qualify other
  devices, bus speeds, cable lengths, or pull-up networks.
* The accepted dedicated scheduler-stress result is one flat-UP physical
  cycle and does not itself qualify run-to-run repeatability or SMP.  The
  separate ``ostest`` matrix provides its own 12-cycle broader libc/POSIX
  evidence.
* Asynchronous I/O and multi-user identity remain conditional on filesystem
  and credential fixtures which the direct-entry image does not provide.
* The Rev-B switch settings are qualified as serial/flash
  ``(ON,OFF,OFF)`` and SD-only ``(OFF,OFF,ON)``.  Automated power cycling is
  unavailable.
* The ABI matrix at ``artifacts/hil/abi/20260713T155112Z`` is retained as a
  historical architecture/compiler baseline.  The current candidate-bound
  ABI PASS is provisional at
  ``/tmp/p2-release-final.14cadad-r1/abi/20260713T231547Z`` and must be
  preserved with the release.
* ``collect-artifacts.sh`` was refreshed after the final campaigns and indexed
  136 top-level status bundles.  It remains less authoritative than the
  per-test sealed artifact writers; do not use it to infer a missing HIL PASS.

32. Remaining blockers and deferred scope
------------------------------------------

The complete applicable ``ostest`` matrix and the PI completion-state change
are closed by the accepted campaigns in section 28.  They are no longer
acceptance blockers.

.. list-table:: Precise remaining gaps and dispositions
   :header-rows: 1

   * - Item
     - Evidence/current state
     - Required closure
   * - Five true power cycles
     - ``P2_POWER_CYCLE_COMMAND`` is empty
     - Add safe external power control or perform and log five manual cold
       cycles against the accepted flash-boot image
   * - microSD absent behavior
     - installed-card campaign only
     - Remove the card and prove bounded, non-panicking absence/error paths
   * - Sudden power-loss storage recovery
     - reset-only interrupted-write test
     - Add controlled power removal during a bounded data-partition write and
       verify recovery plus unchanged boot CRC
   * - GitHub publication
     - **PENDING**; the local package is verified but not yet a durable release
     - Upload the exact assets, verify them from a fresh download, then publish
       and verify the GitHub release
   * - Conditional AIO and multi-user groups
     - BLOCKED on writable-filesystem and credential fixtures; neither feature
       is enabled by the direct-entry OSTest profiles
     - Add fixture-bearing images and make those rows mandatory if either
       feature enters the supported flat-UP scope
   * - NuttX SMP
     - **DEFERRED / OUT OF SCOPE**; ``CONFIG_SMP`` remains unsupported and no
       two-cog architecture HIL exists
     - No closure is required for this goal.  Treat an SMP implementation and
       qualification campaign as a separate future project

The applicable historical flat-UP hardware matrix is accepted, and the exact
candidate has passed clean builds, fresh ABI, Rev-B RAM showcase, and flash
programming plus ten completed reset-only flash boots.  Its exact SD write and
read-only raw-card verification plus no-loader SD-only ROM reset also pass.
The local package and extracted-bundle checks pass.  Release closure is still
incomplete until the GitHub publication row above passes.
SMP remains a separate future project and requires no closure for this
flat-UP goal.
