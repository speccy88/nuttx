/****************************************************************************
 * libs/libc/machine/p2/arch_elf.c
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.  The
 * ASF licenses this file to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance with the
 * License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
 * WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the
 * License for the specific language governing permissions and limitations
 * under the License.
 *
 ****************************************************************************/

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <errno.h>
#include <stdbool.h>
#include <stdint.h>

#include <nuttx/debug.h>
#include <nuttx/elf.h>

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static uint32_t p2_get32(uintptr_t address)
{
  FAR const uint8_t *bytes = (FAR const uint8_t *)address;

  return (uint32_t)bytes[0] |
         (uint32_t)bytes[1] << 8 |
         (uint32_t)bytes[2] << 16 |
         (uint32_t)bytes[3] << 24;
}

static void p2_put32(uintptr_t address, uint32_t value)
{
  FAR uint8_t *bytes = (FAR uint8_t *)address;

  bytes[0] = value;
  bytes[1] = value >> 8;
  bytes[2] = value >> 16;
  bytes[3] = value >> 24;

  /* P2 Hub writes and HUBEXEC instruction fetches are coherent.  Keep the
   * compiler from moving later execution ahead of the relocation writes.
   */

  __asm__ __volatile__("" : : : "memory");
}

static int p2_relocateadd(FAR const Elf32_Rela *rela,
                          FAR const Elf32_Sym *sym, uintptr_t address)
{
  uint32_t instruction;
  uint32_t augment;
  uint32_t value;
  unsigned int type;

  type = ELF32_R_TYPE(rela->r_info);
  if (type == R_P2_NONE)
    {
      return OK;
    }

  /* NuttX passes a NULL symbol for ELF symbol-table entry zero
   * (SHN_UNDEF with no name).  P2LLVM uses that entry for relocatable
   * immediate constants, where the ELF value is S(0) + A.  Rejecting the
   * nameless entry prevents otherwise valid modules containing NULL or
   * small integer constants from loading.
   */

  value = (sym == NULL ? 0 : sym->st_value) + rela->r_addend;

  switch (type)
    {
      case R_P2_32:
        p2_put32(address, value);
        break;

      case R_P2_PC32:
        p2_put32(address, value - address);
        break;

      case R_P2_PC20:
        value -= address;
        /* Fall through */

      case R_P2_20:
        instruction = p2_get32(address);
        instruction = (instruction & ~UINT32_C(0x000fffff)) |
                      ((instruction + value) & UINT32_C(0x000fffff));
        p2_put32(address, instruction);
        break;

      case R_P2_AUG20:
        /* This relocation also rewrites the preceding AUGS/AUGD word.
         * r_offset is relative to the destination section; checking the
         * absolute load address would allow an offset-zero relocation to
         * scribble on the allocation immediately before that section.
         */

        if (rela->r_offset < sizeof(uint32_t))
          {
            return -EINVAL;
          }

        instruction = p2_get32(address);
        augment = p2_get32(address - sizeof(uint32_t));
        augment &= ~UINT32_C(0x007fffff);
        instruction += value & UINT32_C(0x000001ff);
        augment |= value >> 9;
        p2_put32(address - sizeof(uint32_t), augment);
        p2_put32(address, instruction);
        break;

      case R_P2_COG9:
        /* COG9 maps the 0x200..0x3ff LUT instruction-address window
         * onto its 0x200..0x9fc, long-aligned Hub image.  The pinned lld
         * masks this conversion; validate first so an ordinary Hub symbol
         * cannot silently alias an unrelated LUT entry.
         */

        if (value < UINT32_C(0x00000200) ||
            value >= UINT32_C(0x00000a00) ||
            ((value - UINT32_C(0x00000200)) & 3) != 0)
          {
            return -ERANGE;
          }

        instruction = p2_get32(address);
        instruction += (((value - UINT32_C(0x00000200)) / 4) &
                        UINT32_C(0x000001ff)) + UINT32_C(0x00000200);
        p2_put32(address, instruction);
        break;

      case R_P2_PCCOG9:
      default:
        berr("ERROR: Unsupported P2 ELF relocation: %u\n", type);
        return -ENOTSUP;
    }

  return OK;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

bool up_checkarch(FAR const Elf32_Ehdr *ehdr)
{
  /* This draft port has only been implemented for NuttX ET_REL modules.
   * P2 has no PIC/dynamic-linking ABI here, and executable images use the
   * board's fixed Hub layout rather than the generic ELF loader.
   */

  if (ehdr->e_type != ET_REL)
    {
      berr("ERROR: P2 ELF loader requires ET_REL: e_type=%04x\n",
           ehdr->e_type);
      return false;
    }

  if (ehdr->e_machine != EM_P2)
    {
      berr("ERROR: Not for P2: e_machine=%04x\n", ehdr->e_machine);
      return false;
    }

  if (ehdr->e_ident[EI_CLASS] != ELFCLASS32)
    {
      berr("ERROR: P2 requires ELF32 objects\n");
      return false;
    }

  if (ehdr->e_ident[EI_DATA] != ELFDATA2LSB)
    {
      berr("ERROR: P2 requires little-endian objects\n");
      return false;
    }

  return true;
}

int up_relocate(FAR const Elf32_Rel *rel, FAR const Elf32_Sym *sym,
                uintptr_t address, FAR void *arch_data)
{
  unsigned int type = ELF32_R_TYPE(rel->r_info);

  (void)sym;
  (void)address;
  (void)arch_data;

  /* The pinned P2LLVM emits SHT_RELA for every P2 relocation.  Refuse REL
   * input rather than guessing an addend from an instruction pair.
   */

  if (type == R_P2_NONE)
    {
      return OK;
    }

  berr("ERROR: P2 ELF REL relocation is unsupported: %u\n", type);
  return -ENOTSUP;
}

int up_relocateadd(FAR const Elf32_Rela *rela, FAR const Elf32_Sym *sym,
                   uintptr_t address, FAR void *arch_data)
{
  (void)arch_data;
  return p2_relocateadd(rela, sym, address);
}
