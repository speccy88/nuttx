/* SPDX-License-Identifier: Apache-2.0 */

ENTRY(_start)

SECTIONS
{
  . = 0x00000000;
  .text :
  {
    KEEP(*(.p2probe.entry))
    *(.text .text.*)
    *(.p2probe.text)
  }

  .rodata :
  {
    *(.rodata .rodata.*)
    *(.p2probe.rodata)
  }

  .data :
  {
    *(.data .data.*)
    *(.p2probe.data)
  }

  .bss (NOLOAD) :
  {
    *(.bss .bss.* COMMON)
  }
}
