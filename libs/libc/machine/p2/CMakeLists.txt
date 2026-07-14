# ##############################################################################
# libs/libc/machine/p2/CMakeLists.txt
#
# SPDX-License-Identifier: Apache-2.0
# ##############################################################################

if(CONFIG_LIBC_ARCH_ELF)
  target_sources(c PRIVATE arch_elf.c)
endif()
