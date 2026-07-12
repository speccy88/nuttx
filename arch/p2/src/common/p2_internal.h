#ifndef __ARCH_P2_SRC_COMMON_P2_INTERNAL_H
#define __ARCH_P2_SRC_COMMON_P2_INTERNAL_H
#include <stdint.h>
#define P2_HUB_RAM_BASE 0x00000000u
#define P2_HUB_RAM_SIZE (512u * 1024u)
#define P2_INITIAL_STACK_SIZE 4096u
#ifndef __ASSEMBLY__
uintptr_t p2_getsp(void);
void p2_lowputc(int ch);
#endif
#endif
