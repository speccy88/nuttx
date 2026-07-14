# P2 Edge Flat-UP v0.1.1

This is the first P2 Edge showcase release whose two supported module images
are both hardware-verified. It keeps the exact v0.1.0 functional binaries and
adds the completed P2-EC Rev D qualification, dual-board evidence gates, and a
permanent Rev D storage configuration.

## Supported modules

- P2-EC32MB Rev B: P38/P39 LEDs and 32 MiB PSRAM at `/dev/psram0`.
- P2-EC Rev D without PSRAM: P56/P57 LEDs and no PSRAM device or command.

Both images provide NSH with Tab completion, history, Ctrl-C, GPIO, edge
capture, UART, PWM, DAC, ADC, SPI, BMP180 I2C, flash/SmartFS, microSD, and the
`p2help` on-board tour. NuttX runs flat and uniprocessor on cog 0 at 180 MHz.

## Rev D qualification added in v0.1.1

The exact release image passed one bounded RAM showcase, SPI-flash programming
and reset-only boot, and ROM microSD-only boot. The showcase covered all 16
applicable stages, including P56/P57 LEDs and the explicit no-PSRAM runtime
contract. The raw flash/SD image is 386,752 bytes with SHA-256
`596b0f022c28fa4462a6e13692ad54ecab095f17d6532d441e60e0dee481c230`.

The serial microSD writer timed out with the tested 31.3 GB card. Copying the
board-specific file from macOS to the FAT32 root as `_BOOT_P2.BIX` was verified
byte-for-byte and then passed SD-only ROM boot. This host-copy method is the
recommended Rev D SD installation path for that card.

## Install

Download all release assets, or just the bundle plus `SHA256SUMS.txt`, extract
the bundle, and run:

```sh
./install-p2.sh verify
./install-p2.sh ram --board p2-ec --port /dev/cu.usbserial-P97cvdxp
```

Installer actions are dry-runs until `--execute` and the documented safety
gate are supplied. Read the repository README first for the complete RAM,
flash, SD, boot-switch, serial-terminal, and hardware-showcase tutorial.

For manual Rev D SD installation, copy
`p2-edge-flat-up-v0.1.1-p2-ec-revd-_BOOT_P2.BIX` to the FAT32 card root under
the exact name `_BOOT_P2.BIX`. The release-root `_BOOT_P2.BIX` alias is for
P2-EC32MB Rev B, so do not use that alias on Rev D.
