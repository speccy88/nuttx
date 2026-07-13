# Apache NuttX on the Parallax Propeller 2

This branch is a hardware-oriented **draft** NuttX port for the Parallax
P2X8C4M64P Propeller 2. The installable showcase supports two official P2 Edge
modules:

| Installer board name | Module | LEDs | External PSRAM |
| --- | --- | --- | --- |
| `p2-ec32mb` | P2-EC32MB Rev B | P38 and P39 | 32 MiB as explicit `/dev/psram0` storage |
| `p2-ec` | P2-EC Rev D | P56 and P57 | none |

Both images run NuttX in flat, uniprocessor mode on cog 0 at 180 MHz. Other
cogs may run bounded peripheral services, but they are not NuttX CPUs. This is
not an SMP port and it is not yet an upstream-supported P2 target.

The quickest and safest first run is to download the release bundle, select
your exact module, dry-run the installer, and load its ELF into Hub RAM. That
path needs no source build and writes neither SPI flash nor the microSD card.

## Quick start: run NuttX from RAM

Release: [`p2-edge-flat-up-v0.1.0`](https://github.com/speccy88/nuttx/releases/tag/p2-edge-flat-up-v0.1.0)

The supplied installer and `loadp2` binary are qualified on Apple-silicon
macOS. Connect a PropPlug to P62/P63, power the board, and close every serial
terminal before continuing. Only one process can own the PropPlug at a time.

Download the complete bundle and verify its outer checksum:

```zsh
TAG=p2-edge-flat-up-v0.1.0
ASSET="$TAG-bundle-macos-arm64.tar.gz"
BASE="https://github.com/speccy88/nuttx/releases/download/$TAG"

mkdir "$TAG-download"
cd "$TAG-download"
curl -fLO "$BASE/$ASSET"
curl -fLO "$BASE/SHA256SUMS.txt"
awk -v name="$ASSET" '$2 == name { print }' SHA256SUMS.txt |
  shasum -a 256 -c -
tar -xzf "$ASSET"
cd "$TAG"
./install-p2.sh verify
```

Set the board and serial device. These examples use the physically tested
P2-EC32MB and PropPlug path; change both values when needed:

```sh
export BOARD=p2-ec32mb
export PORT=/dev/cu.usbserial-P97cvdxp
```

On macOS, find a different PropPlug path with:

```sh
ls /dev/cu.usbserial-*
```

Power off before changing the boot switches. Select the serial-loader row in
the [boot-switch table](#boot-switch-settings), then power or reset the P2.
The recoverable serial-then-flash setting `(ON, OFF, OFF)` also works with the
installer's DTR reset; that was the Rev B setting used during this release
campaign. The 60-second serial override simply gives more manual recovery
time.

First print the exact RAM command without touching the board:

```sh
./install-p2.sh ram --board "$BOARD" --port "$PORT"
```

If the verification, board name, port, and printed command are correct, run it:

```sh
P2_HIL=1 P2_ALLOW_RESET=1 \
  ./install-p2.sh ram --board "$BOARD" --port "$PORT" --execute
```

The installer resets the P2, loads the board-specific RAM ELF, starts NuttX,
and stays attached as the terminal. Press Enter if `nsh>` is not visible. Keep
this one loader/terminal session open: opening another terminal can toggle DTR
and reset a RAM-only image back into the configured persistent boot source.

At the prompt, run:

```text
nsh> p2help
nsh> uname -a
nsh> ps
nsh> free
nsh> ls /dev
```

`p2help` is the release's on-board tour of the module, registered devices,
fixtures, and useful demonstration commands.

## Release files and board selection

`--board` is mandatory for every install action so the installer cannot
silently choose the wrong image.

| Module | `--board` value | RAM | SPI flash | microSD source |
| --- | --- | --- | --- | --- |
| P2-EC32MB Rev B | `p2-ec32mb` | `p2-edge-flat-up-v0.1.0-p2-ec32mb-revb-ram.elf` | `p2-edge-flat-up-v0.1.0-p2-ec32mb-revb-flash.bin` | `p2-edge-flat-up-v0.1.0-p2-ec32mb-revb-_BOOT_P2.BIX` |
| P2-EC Rev D | `p2-ec` | `p2-edge-flat-up-v0.1.0-p2-ec-revd-ram.elf` | `p2-edge-flat-up-v0.1.0-p2-ec-revd-flash.bin` | `p2-edge-flat-up-v0.1.0-p2-ec-revd-_BOOT_P2.BIX` |

The release-root `_BOOT_P2.BIX` is a convenience alias for **P2-EC32MB Rev B
only**. Do not copy that alias to a Rev D card. The bundle also places an exact
`_BOOT_P2.BIX` under each `boards/p2-ec32mb-revb/` and
`boards/p2-ec-revd/` directory, and the installer selects the right one from
`--board`.

Each flash binary has a neighboring `.json` layout manifest. The bundle also
contains both build configurations, `release-manifest.json`, an evidence
archive, `SHA256SUMS.txt`, the pinned microSD writer, and the separately
licensed `loadp2-0.078-macos-arm64`. `./install-p2.sh verify` checks the entire
set before any install command is printed or executed.

## Install in RAM, SPI flash, or microSD

All installer actions are dry-runs unless `--execute` and their named
authorization variables are present. A dry-run verifies the whole release and
prints the exact command without opening serial, resetting the P2, erasing
flash, or writing the card.

### RAM: temporary and safest

```sh
./install-p2.sh ram --board "$BOARD" --port "$PORT"

P2_HIL=1 P2_ALLOW_RESET=1 \
  ./install-p2.sh ram --board "$BOARD" --port "$PORT" --execute
```

RAM loading uses the 2,000,000-baud ROM-loader path and then changes to the
230400-baud console. It does not modify either persistent medium.

### SPI flash: persistent

Remove the microSD card while programming flash: the loader drives shared
P60/P61. Back up anything important, close every terminal, and use the
serial-loader switch setting.

```sh
./install-p2.sh flash --board "$BOARD" --port "$PORT"

P2_HIL=1 P2_ALLOW_RESET=1 \
P2_ALLOW_FLASH_WRITE=1 P2_ALLOW_FLASH_ERASE=1 P2_ALLOW_SD_WRITE=1 \
  ./install-p2.sh flash --board "$BOARD" --port "$PORT" --execute
```

The flash installer erases only the sectors needed by the selected image,
within the protected first 512 KiB boot reservation. The board-specific `.json`
manifest records the exact erase/program bounds. It does not expose or erase
the 15.5 MiB SmartFS data partition after that reservation.

After programming, power off and select either normal serial-then-flash
`(ON, OFF, OFF)` or flash-only `(ON, OFF, ON)`. The former is the friendlier
recovery setting and is the setting used during this release campaign.

### microSD: persistent `_BOOT_P2.BIX`

The P2 ROM looks for the exact filename `_BOOT_P2.BIX` in the root of a FAT32
microSD card. The bundled in-situ writer deletes and recreates that one root
file from the board-specific image; it does not format the card.

Insert a FAT32 card, choose the serial-loader setting, and run:

```sh
./install-p2.sh sd --board "$BOARD" --port "$PORT"

P2_HIL=1 P2_ALLOW_RESET=1 P2_ALLOW_SD_WRITE=1 \
P2_ALLOW_SD_DESTRUCTIVE=1 \
  ./install-p2.sh sd --board "$BOARD" --port "$PORT" --execute
```

The writer's success proves that it recreated `_BOOT_P2.BIX`; it does not by
itself prove a ROM boot. Power off, select SD-only `(OFF, OFF, ON)` for the
strongest test, power on, and attach without loading another image. The
[goal status table](Documentation/platforms/p2/goal-status-table.md) separates
an actual reset-only SD boot from a file-write result. At this documentation
update, exact packaged-image SD-only HIL is still pending on P2-EC32MB and the
Rev D image remains HIL-required.

You may instead copy a board-specific release file to a FAT32 card on a host,
but it must be renamed exactly `_BOOT_P2.BIX`, placed in the root, and stored
contiguously. The bundled serial installer is preferred because it verifies
the selected image and writer, checks their output, and refuses a false PASS.
The separate reset-only ROM-boot proof remains part of the release campaign
recorded in the goal status table.

## Boot-switch settings

Power the module off before moving switches. The columns are the printed
`FLASH`, up-triangle, and down-triangle switches from the
[P2-EC32MB Rev B guide](https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/5789/P2-EC32MB-Edge-Module-Rev-B-Guide-v2.0.pdf)
and [P2-EC Rev D product guide](https://www.parallax.com/package/p2-edge-module-product-guide/).
Never set both triangle switches ON.

| Boot purpose | FLASH | up | down | ROM behavior |
| --- | --- | --- | --- | --- |
| Serial-loader override | either | ON | OFF | wait for serial loading for about 60 seconds |
| Serial, then SPI flash | ON | OFF | OFF | wait about 100 ms for serial, then boot flash |
| SPI flash only | ON | OFF | ON | boot flash without the serial wait |
| microSD, then serial fallback | OFF | OFF | OFF | boot root `_BOOT_P2.BIX`; fall back to serial |
| microSD only | OFF | OFF | ON | boot root `_BOOT_P2.BIX` without serial fallback |

Use serial-loader override for RAM, flash, and in-situ SD writes. Keep a
fallback-enabled setting until the new image has booted. The installer uses
DTR reset; it cannot rescue a bad image after a no-fallback mode has already
made the serial loader unreachable, so switch back with power off if needed.

## Connect with your favorite serial terminal

The NuttX console is **230400 baud, 8 data bits, no parity, 1 stop bit, and no
hardware or software flow control**. NuttX already converts its outgoing `LF`
to `CR LF` and accepts Return as a newline. Do **not** enable a terminal's
“add CR to LF,” “add LF to CR,” or combined input-newline mapping. Depending
on which direction is expanded, that produces either `CR CR LF` or `CR LF LF`
and makes the `nsh>` display stair-step or gain blank lines.

For a RAM-only run, remain in the `install-p2.sh ram --execute` terminal. The
commands below are best for an image booted from flash or SD. Close the current
terminal first and ensure only one process owns the port.

### `loadp2` terminal

The bundled loader can attach without downloading an image:

```sh
./loadp2-0.078-macos-arm64 -p "$PORT" -b 230400 -xTERM
```

Use lowercase `-xTERM`. Do not substitute `-T`: that loadp2 option adds an LF
after a received CR and is wrong for this NuttX console. Exit terminal mode
with `Ctrl-]` or `Ctrl-Z`.

### `tio` 3.9

This exact mapping was checked with tio 3.9:

```sh
tio --no-reconnect \
  -b 230400 -d 8 -p none -s 1 -f none \
  --map ODELBS "$PORT"
```

`ODELBS` changes a typed Delete into Backspace; it does not rewrite received
line endings. Do not use `--map INLCRNL,ODELBS` or any `ONLCRNL` mapping here.
Exit with `Ctrl-T`, then lowercase `q`.

### GNU `screen`

Use explicit macOS settings so Screen's automatic-flow default cannot change
the connection and closing Screen does not pulse the serial hang-up line:

```sh
/bin/stty -f "$PORT" raw 230400 cs8 -cstopb -parenb \
  -ixon -ixoff -crtscts -hupcl
screen -fn "$PORT" 230400,cs8,-ixon,-ixoff,-istrip
```

Do not add a host newline conversion. The shorter
`screen "$PORT" 230400` form often works, but it leaves flow-control details
dependent on host defaults and is not the strict recipe. Exit Screen with
`Ctrl-A`, then `\\`, and confirm.

### `minicom`

Create a profile once with `minicom -s p2`. Set the serial device, 230400
8-N-1, hardware flow control **No**, software flow control **No**, local echo
**off**, “add linefeed” **off**, and “add carriage return” **off**. Choose
**Save setup as p2** before leaving setup. Then use:

```sh
minicom -o -D "$PORT" -b 230400 -8 p2
```

Use `Ctrl-A Q` to leave without sending a reset.

### `picocom` 3.1

```sh
picocom -b 230400 -d 8 -y n -p 1 -f n \
  --noreset --omap delbs "$PORT"
```

Leave the input line map at its default and use only `--omap delbs` for the
Delete key. Exit with `Ctrl-A Ctrl-Q`. See the
[picocom 3.1 manual](https://raw.githubusercontent.com/npat-efault/picocom/3.1/picocom.1.md)
for other platforms and option spellings.

### PuTTY or Plink on Windows

Replace `COM5` with the PropPlug COM port:

```bat
putty.exe -serial COM5 -sercfg 230400,8,n,1,N
plink.exe -serial COM5 -sercfg 230400,8,n,1,N
```

The final uppercase `N` selects no flow control. In PuTTY's Terminal panel set
local echo and local line editing to **Force off**. Leave both “Implicit CR in
every LF” and “Implicit LF in every CR” off. The
[PuTTY serial command-line documentation](https://the.earth.li/~sgtatham/putty/0.84/htmldoc/Chapter3.html#using-cmdline-sercfg)
describes `-sercfg`. Close the PuTTY window to exit. For the Ctrl-C acceptance
test, prefer the PuTTY GUI: Windows Plink may consume Ctrl-C as a local console
interrupt and terminate itself instead of sending byte `0x03` to NuttX.

### Parallax Serial Terminal

In the Propeller Tool on Windows, identify the hardware/COM port, open
Parallax Serial Terminal with `F12`, select that COM port, choose **230400**,
and click **Enable**. Do not enable an extra LF/CR transformation if the local
version offers one. PST is convenient for the prompt, line-oriented commands,
and sensor output; use tio, screen, minicom, picocom, or PuTTY when testing raw
Tab, arrow-history, or Ctrl-C key sequences. Parallax's
[PST setup guide](https://learn.parallax.com/kickstarts/using-the-parallax-serial-terminal/)
shows the COM/baud/Enable workflow. Click **Disable**, then close PST when
finished.

Any terminal can toggle DTR when opening or closing. That is harmless for a
good persistent flash/SD image but can replace a RAM session with the selected
boot source. Never leave a terminal open while running the installer.

## A small NuttShell tutorial

Start by seeing what this compact image includes:

```text
nsh> help
nsh> p2help
nsh> uname -a
nsh> uptime
nsh> ps
nsh> free
nsh> ls -l /dev
nsh> mount
```

The release enables eight-entry command history and completion. Type the first
four letters below, press Tab, then type ` -a` and Enter:

```text
nsh> unam<Tab> -a
```

Use `unam<Tab>`, not `una<Tab>`: `una` is ambiguous with `unalias`. To check
history, run a visible marker and then press Up-arrow and Enter:

```text
nsh> echo P2HISTORY:PASS
```

The shell is configured so Ctrl-C interrupts the foreground command and
returns the prompt. A safe manual check is to run `sleep 30`, press Ctrl-C, and
confirm that `nsh>` returns before 30 seconds. A Rev B development image passed
that check and then interrupted a foreground 30-second PWM command in the same
session. The exact clean release ELF must repeat both checks before publication;
consult the status table for that hash-bound result.

Useful low-footprint commands include `cat`, `cp`, `echo`, `hexdump`, `kill`,
`ls`, `mkdir`, `mount`, `mv`, `ps`, `rm`, `rmdir`, `sleep`, `umount`, and the
P2-specific `p2help`, `p2smartpins`, `p2i2c`, `p2storage`, and (EC32MB only)
`p2psram`. The larger floating-point `dd` statistics path is deliberately not
in this image; use normal file commands and `hexdump` instead.

## Try the P2 hardware

The same showcase image exposes normal NuttX devices and focused commands. Run
`p2help` on the board before using the examples; it prints the module identity
and only the devices actually present in that build.

| Capability | NuttX interface | First useful command |
| --- | --- | --- |
| Board LEDs | `/dev/userleds`, mask `0x03` | `leds` |
| GPIO | `/dev/gpio0` P0 output, `/dev/gpio1` P1 input | `p2smartpins gpio` |
| Edge input | P0/P1 fixture | `p2smartpins edge` |
| Extra serial port | `/dev/ttyS1`, P2 TX/P3 RX, 115200 | `p2smartpins uart` |
| PWM and capture | `/dev/pwm0` P4, `/dev/cap0` P5 | `pwm -f 1000 -d 50 -t 5` |
| DAC and ADC | `/dev/dac0` P4, `/dev/adc0` P5 | `p2smartpins analog` |
| General SPI | `/dev/spi0`, P6/P7/P8/P9 | `p2smartpins spi` |
| I2C and BMP180 | `/dev/i2c0` P24/P25, `/dev/press0` | `p2i2c` |
| Onboard SPI flash | protected `/dev/smart0` data partition | `p2storage probe` |
| Onboard microSD | `/dev/mmcsd0` | `p2storage probe` |
| EC32MB PSRAM | explicit `/dev/psram0` bulk store | `p2psram 12345678` |

The two active-high buffered LEDs are P38/P39 on P2-EC32MB Rev B and P56/P57
on P2-EC Rev D. Turn the module's LED power switch on to see them. `leds`
starts the NuttX LED example; find its PID with `ps` and stop it with
`kill -15 PID`.

The digital self-tests need P0–P1, P2–P3, and P6–P7 loopbacks. For the analog
test, use about 1 kOhm from P4 to P5 and 100 nF from P5 to GND. Do not replace
that RC fixture with a direct DAC-to-ADC short. PWM/capture prefers a direct
digital connection, while DAC/ADC uses the RC fixture, so run the stage that
matches the installed wiring. Receivers are configured before outputs are
driven.

The BMP180 example expects SDA on P24, SCL on P25, open-drain pull-ups, and
address `0x77`. The generic tools are also available:

```text
nsh> i2c bus
nsh> i2c dev 03 77
nsh> spi help
nsh> adc -n 8
nsh> dac put 32768
```

ADC readings and DAC codes are raw and uncalibrated. The loopback campaigns
prove monotonic behavior and device plumbing, not metrology-grade voltage.

### Use onboard filesystems

At startup the image mounts an existing SmartFS volume as `/mnt/flash`. It
never automatically formats a blank or damaged volume. When the boot log says
the mount succeeded:

```text
nsh> echo "hello from NuttX on P2" > /mnt/flash/hello.txt
nsh> cat /mnt/flash/hello.txt
nsh> ls -l /mnt/flash
nsh> hexdump /mnt/flash/hello.txt count=64
```

To mount an already FAT-formatted microSD card at runtime:

```text
nsh> mkdir -p /mnt/sd
nsh> mount -t vfat /dev/mmcsd0 /mnt/sd
nsh> ls -l /mnt/sd
nsh> umount /mnt/sd
```

There is no invented card-detect GPIO and no automatic formatter. A missing,
unformatted, or unsupported card reports an error. Flash and microSD share
P58–P61, so one storage arbiter owns the pin-mode transition.

## What is physically verified

The P2-EC32MB Rev B flat-UP baseline has preserved HIL evidence for startup,
the scheduler and applicable OSTest set, NSH, GPIO/edge, UART1, PWM/capture,
DAC/ADC with the RC fixture, SPI loopback, P24/P25 BMP180 I2C, flash/SmartFS,
runtime microSD/FAT, flash ROM boot, and explicit 32 MiB PSRAM service.

The final release adds a single dual-board `showcase` configuration. Its
P2-EC32MB development image has booted and exposed `p2help`, `/dev/userleds`,
Tab completion, command history, and working Ctrl-C for both a built-in sleep
and an external PWM app. Repeating those checks against the exact clean
release hash, final flash boot, and packaged `_BOOT_P2.BIX` SD-only boot remain
acceptance gates at this documentation update. P2-EC Rev D is build- and
static-verification qualified only because no Rev D module is attached; its
runtime claims remain **HIL-REQUIRED**. See the
[goal status table](Documentation/platforms/p2/goal-status-table.md) for the
line-by-line distinction between the proven baseline and final release work.

## How the port works

The P2 ROM starts a raw image at Hub address zero in COGEXEC mode. A small
entry stub restarts cog 0 in HUBEXEC mode, reserves the loader metadata and LUT
windows, and enters normal Hub text at `0x0a00`. Startup initializes `.data`,
`.bss`, the upward-growing PTRA stack required by p2llvm, the 20 MHz-to-180 MHz
PLL, the P62/P63 console, and then calls `nx_start()`.

The current architecture is intentionally compact:

- cog 0 runs the NuttX flat-UP kernel and applications;
- CT1 feeds INT1 with absolute deadlines for the 100 Hz tick and preemption;
- a fixed 38-long context frame saves compiler-visible state;
- service cogs can drain console RX and service EC32MB PSRAM requests, but do
  not participate in NuttX scheduling;
- one Smart Pin manager owns, configures, and safely releases application
  pins; and
- one storage arbiter changes shared P58–P61 between flash mode 3 and microSD
  mode 0.

The usable Hub loader/runtime window is `[0x00000000, 0x0007c000)`; the upper
16 KiB of the P2's 512 KiB Hub remains reserved for the loader. P2-EC32MB's
external 32 MiB PSRAM is a message-serviced character device, not ordinary
byte-addressable processor memory. It cannot contain code, C objects, the
NuttX heap, or task stacks. P2-EC Rev D correctly omits that device.

Both modules have 16 MiB SPI flash. The first 512 KiB is a private boot
reservation and the remaining 15.5 MiB is exposed through the protected
SmartFS child. `/dev/smart0` cannot address the boot reservation.

## Build both boards from source

The accepted toolchain and release host are currently arm64 macOS. Before
bootstrapping, provide `git`, `make`, `cmake`, Python 3, `shasum`, `flock`,
`lsof`, and `timeout` or `gtimeout`. The bootstrap builds pinned p2llvm and
FlexProp components and checks out the exact companion NuttX-apps revision.

```sh
mkdir p2-nuttx-v0.1.0
cd p2-nuttx-v0.1.0
git clone --branch p2-edge-flat-up-v0.1.0 --depth 1 \
  https://github.com/speccy88/nuttx.git nuttx
cd nuttx

P2_CACHE="$PWD/../.p2-nuttx-cache" ./tools/p2/bootstrap-local.sh
source "$HOME/.p2-nuttx-env"
```

Build the same board-specific showcase profiles used for the release, keeping
artifacts outside the checkout:

```sh
BUILD_ROOT=$(mktemp -d /tmp/p2-edge-v0.1.0.XXXXXX)

P2_ARTIFACTS="$BUILD_ROOT/p2-ec32mb-showcase" \
  ./tools/p2/build.sh p2-ec32mb:showcase

P2_ARTIFACTS="$BUILD_ROOT/p2-ec-revd-showcase" \
  ./tools/p2/build.sh p2-ec:showcase
```

Each artifact directory contains `nuttx` (RAM ELF), `nuttx.bin` (raw
flash/SD image), configuration, maps, symbols, disassembly, source states,
hashes, and a machine-readable `status.json`. Load a source-built EC32MB ELF
in one session with:

```sh
"$LOADP2" -p "$PORT" -l 2000000 -b 230400 \
  -ZERO -v -DTR "$BUILD_ROOT/p2-ec32mb-showcase/nuttx" -t
```

Specialized P2-EC32MB profiles remain available for focused bring-up and HIL:
`nsh`, `flashboot`, `bringup`, `smartpins`, `analog`, `i2c`, `psram`,
`storage`, `clock`, `schedstress`, and the applicable `ostest*` variants. The
Rev D board currently supplies the release `showcase` profile. All HIL helpers
under `tools/p2` default to dry-run or require explicit gates; they never
silently open serial, reset, erase flash, or write SD.

## Important limitations

- Only `CONFIG_BUILD_FLAT=y` uniprocessor operation is supported. SMP,
  protected/kernel builds, affinity, migration, and multicog NuttX scheduling
  are not implemented.
- CT1/INT1 is the only complete architecture interrupt route. Nested
  interrupts and other architecture interrupt inputs remain unsupported.
- GPIO edge callbacks and UART1 receive use a 100 Hz tick-sampled fallback,
  not a hardware-rate interrupt path. Tickless operation is absent.
- ADC is raw and uncalibrated. The BMP180 campaign qualifies one installed
  100 kHz target, not every I2C device or electrical arrangement.
- Flash and microSD use conservative polled transfers. Formatting is never
  automatic. Card-absent behavior and true removal-of-power recovery remain
  physical follow-up items.
- The bundled installer and loader are macOS arm64 only. Other hosts require a
  separately obtained compatible loader and are not release-qualified here.
- P2-EC Rev D compiles and passes static checks, but remains HIL-required until
  run on actual Rev D hardware.
- Exact release-image Ctrl-C, flash boot, and SD-only `_BOOT_P2.BIX` results
  remain pending until the status table records final preserved evidence.

## Port documentation and evidence

- [Goal status table](Documentation/platforms/p2/goal-status-table.md)
- [Final flat-UP HIL report](Documentation/platforms/p2/final-hil-report.rst)
- [P2 documentation index](Documentation/platforms/p2/index.rst)
- [HIL handoff](Documentation/platforms/p2/hil-handoff.rst)
- [Port analysis](Documentation/platforms/p2/port-analysis.rst)
- [Memory map](Documentation/platforms/p2/memory-map.rst)
- [Context frame](Documentation/platforms/p2/context-frame.rst)
- [Interrupts and timer](Documentation/platforms/p2/interrupts.rst)
- [Pin map](Documentation/platforms/p2/pin-map.rst)
- [Smart Pins](Documentation/platforms/p2/smartpins.rst)
- [Storage arbitration](Documentation/platforms/p2/storage-arbitration.rst)
- [Flash layout](Documentation/platforms/p2/flash-layout.rst)
- [PSRAM service](Documentation/platforms/p2/psram-service.rst)
- [SMP evaluation](Documentation/platforms/p2/smp-evaluation.rst)

---

## Upstream Apache NuttX

<p align="center">
<img src="https://raw.githubusercontent.com/apache/nuttx/master/Documentation/_static/NuttX320.png" width="175">
</p>

![POSIX Badge](https://img.shields.io/badge/POSIX-Compliant-brightgreen?style=flat&label=POSIX)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue)](https://nuttx.apache.org/docs/latest/introduction/licensing.html)
![Issues Tracking Badge](https://img.shields.io/badge/issue_track-github-blue?style=flat&label=Issue%20Tracking)
[![Contributors](https://img.shields.io/github/contributors/apache/nuttx)](https://github.com/apache/nuttx/graphs/contributors)
[![GitHub Build Badge](https://github.com/apache/nuttx/workflows/Build/badge.svg)](https://github.com/apache/nuttx/actions/workflows/build.yml)
[![Documentation Badge](https://github.com/apache/nuttx/workflows/Build%20Documentation/badge.svg)](https://nuttx.apache.org/docs/latest/index.html)
[![MemBrowse](https://membrowse.com/badge.svg)](https://membrowse.com/public/apache/nuttx)

Apache NuttX is a real-time operating system (RTOS) with an emphasis on
standards compliance and small footprint. Scalable from 8-bit to 64-bit
microcontroller environments, the primary governing standards in NuttX are POSIX
and ANSI standards. Additional standard APIs from Unix and other common RTOSs
(such as VxWorks) are adopted for functionality not available under these
standards, or for functionality that is not appropriate for deeply-embedded
environments (such as fork()).

For brevity, many parts of the documentation will refer to Apache NuttX as simply NuttX.

## Getting Started
First time on NuttX? Read the [Getting Started](https://nuttx.apache.org/docs/latest/quickstart/index.html) guide!
If you don't have a board available, NuttX has its own simulator that you can run on terminal.

## Documentation
You can find the current NuttX documentation on the [Documentation Page](https://nuttx.apache.org/docs/latest/).

Alternatively, you can build the documentation yourself by following the Documentation Build [Instructions](https://nuttx.apache.org/docs/latest/contributing/documentation.html).

The old NuttX documentation is still available in the [Apache wiki](https://cwiki.apache.org/NUTTX/NuttX).

## Supported Boards
NuttX supports a wide variety of platforms. See the full list on the [Supported Platforms](https://nuttx.apache.org/docs/latest/platforms/index.html) page.

## Contributing
If you wish to contribute to the NuttX project, read the [Contributing](https://nuttx.apache.org/docs/latest/contributing/index.html) guidelines for information on Git usage, coding standard, workflow and the NuttX principles.

## License
The code in this repository is under either the Apache 2 license, or a license compatible with the Apache 2 license. See the [License Page](https://nuttx.apache.org/docs/latest/introduction/licensing.html) for more information.
