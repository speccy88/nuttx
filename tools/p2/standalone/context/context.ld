/* SPDX-License-Identifier: Apache-2.0 */

/*
 * Native p2llvm/libp2 Hub layout for the CT1 context-switch proof.
 * Keep the libp2 low-Hub contract and ordinary Hub C at 0x0a00 or above.
 */

ENTRY(__entry)

P2_HUB_LIMIT = 0x0007c000;
P2_BOOT_STACK_SIZE = 0x00004000;
P2_BOOT_STACK_BASE = P2_HUB_LIMIT - P2_BOOT_STACK_SIZE;

SECTIONS
{
  PROVIDE(__stack = P2_BOOT_STACK_BASE);

  . = 0x00000000;
  .text :
  {
    KEEP(*(.text.__entry))

    . = 0x00000040;
    KEEP(__start0())
    KEEP(__start())
    KEEP(*(cog))

    . = 0x00000200;
    KEEP(*(lut))

    . = 0x00000a00;
    KEEP(*(.text.main))
    KEEP(*(.text.p2_context_isr))
    KEEP(*(.text.p2_context_control))
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
    . = ALIGN(16);
    _bss_start = .;
    *(.bss*)
    *(COMMON)
    _bss_end = .;
  }

  .stack P2_BOOT_STACK_BASE (NOLOAD) :
  {
    . = ALIGN(4);
    __stack_base = .;
    . = . + P2_BOOT_STACK_SIZE;
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
ASSERT(_bss_end <= P2_BOOT_STACK_BASE,
       "context proof image overlaps the boot stack")
ASSERT(__stack == __stack_base, "libp2 PTRA does not start at boot stack")
ASSERT(__stack_limit <= P2_HUB_LIMIT, "standalone image exceeds Hub limit")
