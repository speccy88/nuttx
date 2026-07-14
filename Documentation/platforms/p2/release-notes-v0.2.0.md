# P2 Edge Berry base v0.2.0

This release is a smaller, extensible base image for both supported P2 Edge
modules. It keeps the storage and compiler reliability work from the P2 draft
port while replacing the hardware-showcase application set with the tools that
are useful for everyday development.

## Supported modules

- P2-EC32MB Rev B: P38/P39 LEDs, 16 MiB flash, microSD, and 32 MiB PSRAM
  exposed as `/dev/psram0`.
- P2-EC Rev D: P56/P57 LEDs, 16 MiB flash, microSD, and no PSRAM device.

Both images run flat, uniprocessor NuttX on cog 0 at 180 MHz.

## Included

- NSH command-line editing, Tab completion, eight-entry history, and corrected
  CR/LF, Backspace, arrow-key, and Ctrl-C handling for a serial terminal.
- The `vi` editor configured for an 80-by-24 terminal.
- Compact Berry with 32-bit integers, single-precision floating point, the
  script compiler, filesystem support, bytecode saving, and a corrected raw
  console reader.
- A built-in Berry check at `/etc/berry-p2/core_smoke.be`.
- Existing SmartFS automount at `/mnt/flash` and FAT microSD automount at
  `/mnt/sd`. Startup never formats either medium.
- The protected W25 flash layout, guarded SD-layout recovery, and the existing
  non-destructive `p2storage` command.
- P2 setjmp/longjmp, the required single-precision compiler runtime helpers,
  the SPI bit-bang receive fix, and conditional-branch regression checks for
  the pinned p2llvm toolchain.

## Intentionally not included

The base images do not include LCD, framebuffer, touch, LVGL, graphical Berry
bindings, ELF modules, or the experimental PSRAM banked runtime. The earlier
`p2-edge-flat-up-v0.1.1` hardware showcase remains available separately.

Berry, `vi`, task stacks, and the normal NuttX heap still use Hub RAM. On the
EC32MB, external PSRAM remains an explicit message-serviced bulk-storage
device; it is not byte-addressable heap or executable memory. This same base
therefore works unchanged on Rev D, which has no external PSRAM.

## First checks

After loading the board-specific RAM ELF, use the 230400 8-N-1 console with no
flow control and run:

```text
nsh> help
nsh> uname -a
nsh> free
nsh> mount
nsh> ls -l /dev
nsh> berry /etc/berry-p2/core_smoke.be
```

The final Berry line should be:

```text
P2BERRY:CORE=PASS:VALUE=42:EXCEPTION=PASS
```

Run `vi`, press `i`, enter a short line, press `Esc`, type `:q!`, and press
Enter to verify the editor without writing a file.

## Qualification boundary

The release package marks both new base artifacts **HIL-REQUIRED**. Prior
v0.1.1 hardware evidence remains evidence for those exact older binaries and
is not reused for v0.2.0. The P2-EC Rev D v0.2.0 image especially requires an
exact-board follow-up run before this prerelease can be promoted to a normal
Latest release.

Installer actions remain dry-runs unless their documented `--execute` and
safety gates are supplied. Remove the microSD card before programming W25
flash because the loader and card share P58-P61.
