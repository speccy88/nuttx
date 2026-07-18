P2 microSD performance record
=============================

Status: **DRAFTED / HIL-REQUIRED**.  No result above 41 MB/s is claimed by
this document until the strict raw-read protocol below has passed on physical
hardware.  The onboard P2-EC32MB socket cannot meet that target; the external
native four-bit path and its high-clock timing still require HIL.

Target and reference result
---------------------------

The acceptance threshold is strictly greater than 41,000,000 bytes/second on
every measured pass.  This is numerically above both the 40 MB/s challenge
and evanh's 40,028 KiB/s filesystem result (40,988,672 B/s), rather than
relying on a unit-label ambiguity.  The protocol here is a raw block-device
test, however, so it does not present that comparison as a like-for-like
filesystem record.  Decimal MB/s is used for the decision and MiB/s is
reported separately.  A single peak, an average whose slowest pass is below
the threshold, or a Hub-RAM-only copy is not a pass.

The design was informed by evanh's `4-bit SD OBEX entry
<https://obex.parallax.com/obex/sdsd-cc/>`_.  It describes ordered four-bit DAT
pins, pull-ups, native transfers, a default ``sysclk/4`` clock, and an optional
CRC-disabled ``sysclk/2`` read mode.  The implementation in this tree is a
clean Apache-2.0 implementation rather than a source copy: the OBEX metadata
says MIT, while the downloaded source has a different custom notice.

Two forum results establish the comparison baseline:

* `the filesystem result on page 29
  <https://forums.parallax.com/discussion/174988/new-sd-mode-p2-accessory-board/p29>`_
  reports 40,028 ``kB/s`` with a 340 MHz P2, 170 MHz SD clock, 64 KiB buffer,
  and read CRC disabled.  The tester divides by 1024, so this is 40.989
  decimal MB/s (39.090 MiB/s).
* `the raw result on page 14
  <https://forums.parallax.com/discussion/174988/new-sd-mode-p2-accessory-board/p14>`_
  reports a genuine CMD18 card-to-Hub transfer at 59.5 MiB/s for 16 MiB on an
  Apacer card, with a 270 MHz P2, 135 MHz SD clock, and read CRC disabled.  The
  same small range was reread repeatedly, so this port uses a 256 MiB default
  range and seven full passes.

The raw 59.5 MiB/s result is a separate, faster record.  This profile's raw
bus ceiling is 60,000,000 B/s (57.220 MiB/s), so it cannot honestly claim to
beat that number; its defined target is the 40 MB/s challenge and the reported
filesystem result above.

Why there are two paths
-----------------------

The onboard socket exposes only DAT0/MISO, CMD/MOSI, CS/DAT3, and CLK on
P58-P61.  With DAT1 and DAT2 absent it has only one payload data lane.  The
wiring could speak either SPI or native one-bit SD, but neither can meet the
record target: standard 50 MHz one-bit High Speed has a 6.25 MB/s line-rate
ceiling, and even the record profile's out-of-spec 120 MHz clock would provide
only 15 MB/s on one lane.  Native block framing lowers those payload maxima to
about 6.223 and 14.934 MB/s respectively, before command and inter-block gaps.
The board schematic also places 240 ohms in series
between DAT0/MISO and P58, powers the socket directly from the P56-P63 3.3 V
I/O rail, and provides neither card detect nor switched card power.

The implemented ``sdspi-perf`` profile accelerates full block reads with a P2
pulse-generator and synchronous-receive Smart Pin at ``sysclk/5``.  At the
qualified 180 MHz system clock its line-rate ceiling is 4.5 MB/s; an ideal
512-byte SPI data frame including its token and CRC is limited to about
4.474 MB/s even before command and card-response gaps.  It is a useful onboard
improvement, not a record candidate; the native four-bit driver must use a
separate socket rather than modifying or sharing J301.

The ``sdio-record`` profile uses a separate native four-bit socket.  Its
record setting is an explicitly experimental 360 MHz P2 system clock and a
``sysclk/3`` SD clock: 120 MHz on four data wires, or 60 MB/s raw payload
bandwidth before framing and software overhead.  The lower half first uses
SD CMD6 to negotiate High Speed and calibrates its input mode/phase with
repeated CRC16-valid switch-status blocks.  This profile falls back to its
5 MHz CRC-verified command/slow path if either step fails, which cannot pass
the record threshold.

One 512-byte four-bit data frame consumes 1,042 bus clocks from its start bit
through its end bit: 1,024 payload clocks, one start clock, 16 CRC clocks, and
one end clock.  Even with no card or command gap, 120 MHz therefore caps framed
payload at about 58.964 MB/s.  ``sysclk/3`` also produces an asymmetric
one-clock/two-clock duty cycle.  Both the frequency and duty cycle are
experimental timing points, not standard High Speed operation.

Both clocks are outside their normal qualified operating points.  The board
port is qualified at 180 MHz, and 3.3 V SD High Speed is specified for a much
lower clock than the record profile.  The Kconfig overclock opt-in and native
record profile are therefore experimental.  A passing card/P2 combination is
not evidence that another unit, voltage, wiring length, or temperature is
safe or reliable.

External socket wiring
----------------------

Use a 3.3 V SD socket or adapter intended for native SD, not a module that
exposes SPI only and not a 5 V/level-shifting breakout.  At 120 MHz this should
be a short PCB or interposer with a continuous ground return, not loose jumper
wires.  The record profile reserves P16-P23 exclusively; a build fails if an
enabled configurable board peripheral assigns any of those pins.

The fixture needs a common P2/card ground and a dedicated 3.3 V card supply
through an active-high load switch.  Place a local ceramic bypass capacitor
and the bulk capacitance recommended by the socket, card, and switch vendors at
the socket.  CMD and DAT0-DAT3 need external pull-ups to the *switched card
VDD*, never to an always-on rail; otherwise an unpowered card can be back-fed
through its signal pins.  Give the load-switch enable a hardware pull-down so
the card remains off while P23 is floating during reset.

The switch must provide active discharge, or an equivalent discharge path,
that has been measured to take card VDD below 0.5 V.  The driver holds all SD
signals low, disables the switch for 10 ms, then waits 5 ms after enabling it.
The fixture must keep card VDD below 0.5 V for at least 1 ms within that off
window; elapsed time at the enable pin alone does not prove a valid SD power
cycle.  The signal and supply layout is:

.. list-table:: External native four-bit SD connection
   :header-rows: 1

   * - P2 pin
     - SD signal
     - Notes
   * - P16-P19
     - DAT0-DAT3
     - Four consecutive pins aligned to a four-pin boundary
   * - P20
     - CMD
     - Bidirectional command, with pull-up
   * - P21
     - CLK
     - Keep short; no pull-up
   * - P22
     - Activity LED
     - Reserved; the current driver leaves it as an input
   * - P23
     - Active-high power-switch input
     - Drive an external 3.3 V load switch; never power the card directly
   * - P2 GND
     - VSS and switch ground
     - Common low-inductance ground and return plane
   * - Switched 3.3 V
     - VDD and CMD/DAT pull-ups
     - Locally decouple at the socket; never connect VDD directly to P23

Do not enable the profile until the actual adapter's power-switch polarity,
active discharge, VDD decay, 3.3 V signal levels, and common ground have been
verified.

Read-only proof protocol
------------------------

The target command is::

  p2storage sd-benchmark-read 8HEX 268435456 7

It opens ``/dev/mmcsd0`` with ``O_RDONLY`` and never mounts, formats, or writes
the card.  Before timing, it enables all four native payload CRC16 checks and
hashes one full 256 MiB baseline.  It then restores the profile's explicitly
reported record policy (CRC16 off by default), hashes the exact bytes returned
by every timed read call outside that call's timing interval, and requires
every timed hash to equal the CRC-verified baseline.

Each of seven passes accumulates P2 ``GETCT`` cycles spent inside the raw
``read()`` calls.  Every call is bounded well below one 32-bit counter wrap.
The host parser uses the reported system-clock frequency to recompute each
integer rate; it also rejects rates above the reported bus-width/clock raw
ceiling.  Geometry is captured before and after, and media-change, short
reads, driver errors, protocol drift, inconsistent telemetry, or any pass at
or below 41,000,000 B/s fails the run.

The host parser independently recomputes every integer rate and aggregate.
It is dry-run by default::

  ./tools/p2/test-sd-benchmark.py \
    --port /dev/cu.usbserial-P97cvdxp

Serial-only live capture requires the HIL gate::

  P2_HIL=1 ./tools/p2/test-sd-benchmark.py \
    --execute \
    --port /dev/cu.usbserial-P97cvdxp \
    --timeout 600

In that mode the helper only opens the serial console and sends the read-only
command.  It does not reset, load, or flash the P2.  Its artifact records the
raw and normalized consoles, parsed result, local image and configuration
candidates with hashes, and the fact that image binding was not performed.
A successful serial transcript is therefore labelled
``MEASUREMENT_PASS_IMAGE_UNVERIFIED`` and exits with status 3; it is not a
final proof.

The optional bound mode loads and measures one exact image through one pinned
``loadp2`` terminal session.  It is also dry-run by default::

  ./tools/p2/test-sd-benchmark.py \
    --ram-load \
    --port /dev/cu.usbserial-P97cvdxp

Do not enable it until the external socket's 3.3 V levels, common ground,
active-high power-switch input, and discharged-off VDD interval have been
checked.  A live bound run deliberately resets the P2, so it requires the HIL
and reset gates, an exact ``P2_PORT`` match, one clean
``p2-ec32mb:sdio-record`` build artifact, and a ``loadp2`` executable whose
digest is present in that artifact's embedded toolchain lock::

  P2_HIL=1 \
  P2_ALLOW_RESET=1 \
  P2_PORT=/dev/cu.usbserial-P97cvdxp \
  LOADP2=/absolute/path/to/loadp2 \
    ./tools/p2/test-sd-benchmark.py \
      --execute \
      --ram-load \
      --port /dev/cu.usbserial-P97cvdxp \
      --build-artifact /absolute/path/to/build-sdio-record \
      --timeout 600

Bound mode validates the clean build manifest and hashes and preserves its
ELF, generated configuration, loader, toolchain lock, source commits, board,
and profile before starting.  It invokes a RAM-only load with exactly one
authorized reset, waits for the NSH prompt, sends the canonical 256 MiB by
seven read-only command through that same terminal process, and rechecks the
immutable inputs before accepting the DONE marker.  It never selects a flash
option and never writes, mounts, or formats the SD card.  The raw loader
transcript, command vector, hashes, parsed rates, and proof status are retained
in one artifact.

Acceptance record
-----------------

This section remains **HIL-REQUIRED**.  Replace that status only after an
artifact shows all seven 256 MiB passes strictly above 41,000,000 B/s, stable
geometry, timed hashes equal to the full CRC16 baseline, the expected
four-bit/clock/phase/CRC telemetry, and ``proof_complete=true`` from the bound
target-image mode.  Until then, compiled or statically verified code is not a
speed proof.
