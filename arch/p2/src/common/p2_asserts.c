#include <nuttx/config.h>
#include <stdint.h>
#include <arch/context.h>
_Static_assert(P2_XCPT_REGS == 36, "P2 context register count");
_Static_assert(P2_REG_PTRA * P2_REG_BYTES == 128, "PTRA offset");
_Static_assert(P2_REG_PC * P2_REG_BYTES == 132, "PC offset");
