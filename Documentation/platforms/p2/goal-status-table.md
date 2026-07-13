# Propeller 2 flat-UP release goal status

Updated 2026-07-13 while preparing the dual-board
`p2-edge-flat-up-v0.1.0` prerelease.

This table answers two different questions separately:

1. Which P2 facilities already have preserved physical evidence on the
   available P2-EC32MB Rev B?
2. What still has to pass using the exact clean dual-board showcase release?

Status meanings:

- **PASS** — supported by preserved physical-board or host-test evidence.
- **COMPILED / STATICALLY-VERIFIED** — built and checked, but not run on the
  named hardware.
- **PARTIAL / IN PROGRESS** — useful evidence exists, but the final release
  acceptance gate is incomplete.
- **HIL-REQUIRED** — no physical support claim is made for that board/image.
- **DEFERRED / OUT OF SCOPE** — deliberately excluded from the flat-UP goal.

## Boards and installed fixtures

| Board | Release selector | Board-specific hardware | Current qualification |
| --- | --- | --- | --- |
| P2-EC32MB Rev B | `--board p2-ec32mb` | active-high LEDs P38/P39; 32 MiB PSRAM; 16 MiB flash; microSD | Available at `/dev/cu.usbserial-P97cvdxp`. The specialized flat-UP baseline is HIL-verified. The final clean `showcase` image is still being accepted. |
| P2-EC Rev D | `--board p2-ec` | active-high LEDs P56/P57; no PSRAM; 16 MiB flash; microSD | Development `showcase` image is compiled and statically verified. No Rev D hardware is attached, so all runtime behavior remains **HIL-REQUIRED**. |

The available Rev B fixture has direct loopbacks P0–P1, P2–P3, and P6–P7.
P4 reaches P5 through the requested series resistor, with a capacitor from P5
to GND. A BMP180 is installed at I2C address `0x77`, with P24 SDA and P25 SCL.
The module LED power switch must be ON for visible light.

## Working baseline and release acceptance

| Area | What works so far | Exact evidence or qualification | What remains for `p2-edge-flat-up-v0.1.0` |
| --- | --- | --- | --- |
| Flat-UP startup and kernel | **PASS on Rev B.** Native entry, Hub layout, `.data`/`.bss`, upward PTRA stacks, 180 MHz clock, 100 Hz tick, scheduler, preemption, heap, signals, task start/exit, and the applicable OSTest matrix work. | Context switching passed 1,000,000 interrupt-driven switches in `artifacts/hil/20260713T034110.407118Z-context`; bring-up passed 100/100 resets in `artifacts/hil/20260713T034525.287219Z-bringup`; scheduler stress completed 1,004,078 events in `artifacts/hil/20260713T112942.518754Z-schedstress`. | Re-run the compact final-image core checks as part of exact-image HIL. Rev D remains HIL-required. |
| Toolchain, build wrapper, and release provenance | **PASS for the established toolchain; IN PROGRESS for final packaging.** Builds preserve source state, toolchain lock, hashes, configuration, maps, logs, and machine-readable status. | Apps commit `b9433dda16c6b5e0fc3651fc1631ddcc0a779037` is pushed and pinned by `tools/p2/bootstrap-local.sh`. `tools/p2/build.sh`, `tools/p2/build_artifact.py`, and ELF/static tests reject an unsafe, dirty, or commit-mismatched release input. | Commit NuttX, regenerate the runtime toolchain lock, and make clean `p2-ec32mb:showcase` and `p2-ec:showcase` artifacts from those exact commits. |
| Dual-board showcase builds | **COMPILED / STATICALLY-VERIFIED in development builds.** Both raw images fit the serial SD-writer ceiling and contain their exact board marker. | The latest P2-EC32MB development raw image was 389,012 bytes (`842a35f9e216cadc9c125ef3c4936838edc3eb647d989f20e06bdbdf1d073651`); Rev D was 373,312 bytes (`a7ce4db898576d1b0c6f612631de0d15d43276a96e1dfc6a0138add234ac7510`). Both passed ELF/layout checks. These dirty-tree builds are development evidence, not release artifacts. | Produce the same two profiles from clean committed trees. Package only `status.json` artifacts with stable commits and `source_clean=true`. |
| Console and basic NSH | **PASS on the Rev B baseline.** P62/P63 `/dev/ttyS0` works at 230400 8-N-1. NSH prompt, RX/TX, `help`, `uname`, `ps`, `free`, `uptime`, device listing, and mount listing passed 50/50 resets. | `artifacts/hil/20260713T035042.747009Z-nsh`. | Confirm the exact clean showcase image reaches `P2SHOWCASE:READY`, `nsh>`, and `p2help`. |
| Tab completion and history | **PASS on the Rev B development showcase image.** `unam<Tab> -a` expanded to `uname -a`; Up-arrow repeated an `echo P2HISTORY:PASS` command. | Live RAM integration check of the development showcase build. `una<Tab>` is intentionally not the test because it is ambiguous with `unalias`. | Preserve PASS logs tied to the exact release ELF/raw-image hash. |
| Ctrl-C / foreground SIGINT | **PASS on the Rev B development showcase image; exact-release gate pending.** Timer-driven console-ring service prevents foreground work from starving RX, and NSH sleep now establishes a controlling TTY with a temporary SIGINT handler. | In one live RAM session, Ctrl-C returned `nsh>` from both `sleep 30` and the external `pwm -f 1000 -d 50 -t 30` app in under 1.5 seconds; the second check also proved the shell restored the default handler after its built-in command. | Repeat both checks with `tools/p2/test-showcase.py` against the exact clean release ELF and preserve hash-bound PASS evidence. |
| Serial-terminal usability | **PASS for documented host settings.** Screen and tio 3.9 with `--map ODELBS` produced correct CR/LF against the Rev B console. | README records strict 230400 8-N-1/no-flow commands for loadp2, screen, tio, minicom, picocom, PuTTY/plink, and Parallax Serial Terminal. It forbids host LF/CR expansion that produces `CR CR LF` or `CR LF LF`. | Recheck at least one documented terminal after final persistent boot. Ctrl-C remains a separate target-side gate. |
| Board LEDs | **PARTIAL.** `/dev/userleds` exposes mask `0x03`; the NuttX `leds` example cycled sets on the Rev B development showcase image. Board mappings are P38/P39 on Rev B and P56/P57 on Rev D. | Common user-LED lower/upper-half support and board-specific active-high pin tables are present. | Preserve exact-image Rev B device/command evidence and visually confirm with the LED power switch ON. Rev D remains HIL-required. |
| GPIO and edge | **PASS on Rev B baseline.** `/dev/gpio0` P0 output and `/dev/gpio1` P1 input use the installed P0/P1 loopback; edge behavior is included. | Digital Smart Pin campaign passed 50/50 cycles in `artifacts/hil/20260713T063221.439668Z-smartpins`. | Run the nondestructive showcase `p2smartpins gpio` and `edge` stages against the exact Rev B release hash. |
| Configurable UART | **PASS on Rev B baseline.** `/dev/ttyS1` uses P2 TX/P3 RX at 115200 through the installed loopback. | Same 50/50 Smart Pin campaign. | Run the showcase `p2smartpins uart` stage against the exact release hash. Rev D remains HIL-required. |
| PWM and capture | **PASS on Rev B baseline.** `/dev/pwm0` P4 and `/dev/cap0` P5 work with the correct digital fixture. | Same 50/50 Smart Pin campaign. | The installed P4/P5 RC fixture is for analog, not the strongest direct digital capture proof. Demonstrate safe PWM output in the showcase; retain the prior specialized capture result. |
| DAC and ADC | **PASS on Rev B baseline.** `/dev/dac0` P4 through the series resistor to raw `/dev/adc0` P5 plus the P5-to-GND capacitor produced strictly increasing ranges for three DAC codes. | Analog campaign passed 20/20 resets in `artifacts/hil/20260713T110743.191438Z-smartpins`. | Run `p2smartpins analog` on the exact Rev B release image. Values remain raw and uncalibrated. |
| General SPI | **PASS for Rev B loopback.** `/dev/spi0` uses P6 MOSI, P7 MISO, P8 SCK, and P9 CS. | Mode-0 loopback passed 50/50 cycles in the Smart Pin campaign. This does not claim every SPI target/mode/rate. | Run the showcase SPI stage against the exact release image. Rev D remains HIL-required. |
| I2C and BMP180 | **PASS on Rev B baseline.** Open-drain `/dev/i2c0` uses P24 SDA/P25 SCL; BMP180 ID `0x55`, repeated-start reads, `/dev/press0`, and 640 pressure readings passed at 100 kHz. | 20/20 resets in `artifacts/hil/20260713T111043.745628Z-i2c`. | Run `p2i2c` from the exact Rev B showcase image and preserve its hash-bound log. Rev D remains HIL-required. |
| Flash/SmartFS data partition | **PASS on Rev B baseline.** JEDEC `EF7018`, protected 512 KiB boot reservation, 15.5 MiB `/dev/smart0`, persistence, ENOSPC, and reset recovery work. Startup mounts an existing SmartFS at `/mnt/flash` and never auto-formats. | `artifacts/hil/20260713T063712.505220Z-flashfs`. | Run nondestructive `p2storage probe` and verify the startup mount marker from the exact release image. True power-cut recovery remains fixture-blocked. |
| microSD/FAT at runtime | **PASS on Rev B baseline.** `/dev/mmcsd0`, FAT operations, 1 MiB persistence, 64 stress iterations, and 1,000 flash/SD alternations work. | `artifacts/hil/20260713T083209.592794Z-sd`. | Probe the card from the exact showcase image. Card-absent behavior remains unqualified because there is no card-detect GPIO. |
| Shared storage arbitration | **PASS on Rev B baseline.** One mutex/state machine changes P58–P61 safely between flash mode 3 and SD mode 0. | Exercised by the flash/SD alternation campaign. | Confirm both devices probe after exact-image boot; Rev D remains HIL-required. |
| P2-EC32MB PSRAM | **PASS on Rev B baseline.** Explicit 32 MiB `/dev/psram0` service passed walking bits, address lines, boundaries, random cases, full-memory hash, concurrency, timeout/recovery, and CE timing. It is not heap, stack, code, or ordinary C-object memory. | Two consecutive passes in `artifacts/hil/20260713T100106.997809Z-psram` and `artifacts/hil/20260713T100735.645104Z-psram`. | Run a compact `p2psram` proof from the exact EC32MB showcase image. Correctly keep PSRAM absent from Rev D. |
| ROM boot from SPI flash | **PASS for the earlier sealed Rev B flashboot image; final release pending.** The earlier image booted 20/20 DTR resets and mounted existing SmartFS without formatting. | `artifacts/hil/20260713T103452Z-flashboot`. | Program the exact packaged EC32MB showcase flash binary, verify its manifest/hash, and prove reset boot and prompt. Five true power cycles remain unavailable without external power control. |
| `_BOOT_P2.BIX` creation | **HOST-TESTED; physical write pending for the final package.** The release contains one board-specific image per module plus an EC32MB-only root alias. The installer defaults to dry-run and selects by mandatory `--board`. | `tools/p2/release_bundle.py`, `tools/p2/install-release.sh`, `tools/p2/write-sd-boot.sh`, and their host tests validate image limits, hashes, filenames, and destructive gates. | Package exact clean binaries, then use the bundled in-situ writer to recreate root `_BOOT_P2.BIX` from the exact EC32MB release asset. |
| ROM boot from microSD | **HIL-REQUIRED for the packaged image.** Runtime FAT success and successful file creation are not treated as ROM-boot proof. | `tools/p2/verify-sd-boot.py` requires an explicit SD-only confirmation and performs a reset-only, zero-loader-byte check. | After the exact image is written, switch Rev B to `(FLASH,up,down)=(OFF,OFF,ON)` and preserve ordered boot markers plus `nsh>` with no loader signature. Rev D remains HIL-required. |
| Installable release bundle | **PARTIAL / IN PROGRESS.** Packaging and verification tools are host-tested. Planned assets include both boards' RAM ELF, flash bin/manifest, config, and `_BOOT_P2.BIX`; the EC32MB alias; installer; bundle verifier; loader/license; SD writer; evidence; manifest; and checksums. | Bundle prefix and tag are `p2-edge-flat-up-v0.1.0`. HIL-VERIFIED metadata is rejected unless archived PASS evidence contains the exact raw or ELF hash. | Package from final clean builds and exact EC32MB HIL, verify the directory and extracted archive, upload every release asset, redownload into a fresh directory, and verify again. |
| GitHub publication | **IN PROGRESS.** Apps commit `b9433dda16c6b5e0fc3651fc1631ddcc0a779037` is pushed to `codex/p2-hil-finish-apps`; NuttX fork/authentication and the final target branch are ready. | NuttX branch `codex/p2-hil-finish`; apps branch `codex/p2-hil-finish-apps`. | Commit and push NuttX, push tag `p2-edge-flat-up-v0.1.0`, create the prerelease, verify remote assets/checksums, and open the draft PR. |
| NuttX SMP | **DEFERRED / OUT OF SCOPE.** `CONFIG_SMP` is deliberately rejected; service cogs do not constitute SMP. | The completed flat-UP design and `smp-evaluation.rst` state the boundary. | Nothing for this goal. A future SMP project needs secondary CPU startup, per-CPU state, atomics/spinlocks, IPI/reschedule, affinity/migration, barriers, and multicog stress. |

## Remaining finish sequence

| Order | Required completion gate | Done when |
| --- | --- | --- |
| 1 | Reconfirm Ctrl-C in the exact image | Both `sleep 30` and the external 30-second PWM command are interrupted and `nsh>` returns early through the real console/TTY path. |
| 2 | Freeze source | Apps SHA `b9433dda16c6b5e0fc3651fc1631ddcc0a779037` is pinned by the bootstrap; NuttX documentation/code is committed with both trees clean. |
| 3 | Build release candidates | Clean committed `p2-ec32mb:showcase` and `p2-ec:showcase` artifacts pass compile, ELF, size, marker, static, and host tests. |
| 4 | Accept exact EC32MB image | Hash-bound HIL proves boot, `p2help`, LEDs device, Tab, history, Ctrl-C, Smart Pin stages, BMP180, storage probe, and PSRAM. |
| 5 | Package and verify | The dual-board bundle accepts only those clean artifacts and archives the exact EC32MB PASS evidence; Rev D stays labeled HIL-REQUIRED. |
| 6 | Verify persistent flash | The exact packaged EC32MB flash binary programs and boots back to NSH from the configured flash mode. |
| 7 | Verify persistent SD | The exact packaged EC32MB `_BOOT_P2.BIX` is written, the user selects SD-only, and the reset-only verifier reaches NSH without transmitting a loader image. |
| 8 | Publish and reproduce | Commits, branches, tag, prerelease, all binary assets, checksums, and draft PR are remote; a fresh download verifies and dry-runs successfully. |

The earlier specialized-profile flat-UP campaign remains valid baseline
evidence and is documented in
[`final-hil-report.rst`](final-hil-report.rst). It does not replace the final
hash-bound showcase/release gates above. Rev D is intentionally useful as a
buildable release target without pretending that unavailable hardware was
tested.
