# Native p2llvm standalone hello

This is the pre-NuttX proof-of-life image for the Parallax P2-EC32MB Rev-B
module. It uses the P2LLVM revision and `libp2` startup/runtime installed by
`tools/p2/bootstrap-local.sh`; it does not link P2LLVM libc.

The program deliberately owns the board-specific setup instead of using the
stock P2ES crystal configuration:

- selects RCFAST, programs PLL setup `0x010008f4`, waits 300,000 RCFAST
  cycles, and selects final mode `0x010008f7` for the module's 20 MHz external
  oscillator and a 180 MHz system clock;
- configures Smart Pin TX P62 and RX P63 for 230400 baud;
- uses a bounded polled TX routine rather than libp2's unbounded UART putc;
- toggles buffered LEDs P38 and P39;
- proves `.data`, `.bss`, PTRA, and the free-running system counter;
- accepts the single-byte command `?` and replies `P2HELLO:ECHO=?`.

## Build

From the NuttX repository root:

```sh
make -C tools/p2/standalone/hello
```

Set `P2LLVM_ROOT=/path/to/p2llvm/install` if the pinned toolchain is outside
the bootstrap cache. The Makefile calls `ld.lld` directly and passes only the
standalone object plus `libp2.a`; this is intentional because this pinned P2
Clang driver's normal link job adds `-lc` unconditionally.

Artifacts are placed under `tools/p2/standalone/hello/build/`:

- `p2hello.elf`: loader input;
- `p2hello.bin`: flat Hub image;
- `p2hello.map`: linker map.

The `verify` target rejects a non-P2 ELF, a first `PT_LOAD` whose physical
address is not zero, normal C below Hub address `0x0a00`, missing startup/stack
symbols, stack overlap, and unresolved symbols. The linker layout retains the
pinned `libp2` contract: entry at zero, reusable cog startup at `0x40`, LUT
runtime at `0x200`, and ordinary Hub C at `0x0a00` or above.

## RAM-only loading

The Makefile never accesses hardware. From the NuttX repository root, the
exact volatile Hub-RAM load command for the connected board is:

```sh
LOADP2=/Volumes/SSD2TB/Code/.p2-nuttx-cache/flexprop-src/bin/loadp2
"$LOADP2" -v -ZERO -p /dev/cu.usbserial-P97cvdxp \
  -l 2000000 -b 230400 \
  "$PWD/tools/p2/standalone/hello/build/p2hello.elf"
```

This command resets and downloads the ELF, then releases the serial port; it
does not enter terminal mode. It deliberately omits loader-side clock/baud
patching and every flash-programming option: the program configures its own
clock and console, and execution remains RAM-only.

## Expected console protocol

Loading and serial monitoring are intentionally outside this Makefile so a
plain build can never open, reset, or flash a board. At 230400 baud the image
prints:

```text
P2HELLO:ENTRY
P2HELLO:DATA=OK
P2HELLO:BSS=OK
P2HELLO:PTRA=0x........
P2HELLO:COUNTER=0x........
P2HELLO:READY
```

After `P2HELLO:READY`, send one `?` byte. The deterministic response is:

```text
P2HELLO:ECHO=?
```

Any other first byte produces `P2HELLO:ECHO=INVALID`. The program then remains
idle. This target never programs flash.
