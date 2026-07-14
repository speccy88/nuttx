/****************************************************************************
 * arch/p2/include/elf.h
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

#ifndef __ARCH_P2_INCLUDE_ELF_H
#define __ARCH_P2_INCLUDE_ELF_H

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

/* P2LLVM uses the provisional e_machine value 300 (0x12c). */

#define EM_P2             300
#define EM_ARCH           EM_P2
#define EF_FLAG           0
#define ARCH_ELFDATA      1

/* Keep these values synchronized with
 * llvm/include/llvm/BinaryFormat/ELFRelocs/P2.def in the pinned P2LLVM.
 */

#define R_P2_NONE         0
#define R_P2_32           1
#define R_P2_PC32         2
#define R_P2_20           3
#define R_P2_PC20         4
#define R_P2_AUG20        5
#define R_P2_COG9         6
#define R_P2_PCCOG9       7

/****************************************************************************
 * Public Types
 ****************************************************************************/

#ifndef __ASSEMBLY__

/* P2 relocations do not need cross-entry state.  Defining a real type keeps
 * the generic ELF binder's architecture-data interface well formed.
 */

struct arch_elfdata_s
{
  unsigned int reserved;
};

typedef struct arch_elfdata_s arch_elfdata_t;

#endif /* __ASSEMBLY__ */
#endif /* __ARCH_P2_INCLUDE_ELF_H */
