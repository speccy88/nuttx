/* SPDX-License-Identifier: Apache-2.0 */

/*
 * p2llvm/libp2-compatible Hub layout for P2-EC32MB standalone tests.
 *
 * The P2 linker encodes calls using the upstream p2.ld conventions.  Keep the
 * one-shot entry at zero, cog startup at 0x40, LUT runtime at 0x200, and normal
 * Hub-executed C at or above 0x0a00.
 */

ENTRY(__entry)

P2_HUB_LIMIT = 0x0007c000;
P2_STACK_SIZE = 0x00004000;
P2_STACK_BASE = P2_HUB_LIMIT - P2_STACK_SIZE;

SECTIONS
{
  PROVIDE(__stack = P2_STACK_BASE);

  . = 0x00000000;
  .text :
  {
    KEEP(*(.text.__entry))

    /* 0x04-0x3f is the libp2-compatible global parameter area. */

    . = 0x00000040;
    KEEP(__start0())
    KEEP(__start())
    KEEP(*(cog))

    /* p2llvm has LUT-call relocation assumptions tied to this address. */

    . = 0x00000200;
    KEEP(*(lut))

    /* Normal Hub C must not execute from the reserved low Hub window. */

    . = 0x00000a00;
    KEEP(*(.text.main))
    *(.text*)
  }

  .rodata :
  {
    *(.rodata*)
  }

  .data :
  {
    *(.data*)

    PROVIDE(__enable_p2db = .);
    LONG(0)

    _init_array_start = .;
    KEEP(*(.init_array))
    _init_array_end = .;

    _fini_array_start = .;
    KEEP(*(.fini_array))
    _fini_array_end = .;
  }

  .bss :
  {
    . = ALIGN(4);
    _bss_start = .;
    *(.bss*)
    *(COMMON)
    _bss_end = .;
  }

  .stack P2_STACK_BASE (NOLOAD) :
  {
    . = ALIGN(4);
    __stack_base = .;
    . = . + P2_STACK_SIZE;
    __stack_limit = .;
  }

  _end = .;

  /DISCARD/ :
  {
    *(.comment)
    *(.note*)
  }
}

ASSERT(__entry == 0x00000000, "libp2 __entry must be at Hub address zero")
ASSERT(main >= 0x00000a00, "normal Hub C main is below 0x0a00")
ASSERT(_bss_end <= P2_STACK_BASE, "standalone image overlaps initial stack")
ASSERT(__stack == __stack_base, "libp2 PTRA does not start at stack base")
ASSERT(__stack_limit <= P2_HUB_LIMIT, "standalone image exceeds Hub limit")
