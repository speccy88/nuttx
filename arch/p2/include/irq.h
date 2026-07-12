#ifndef __ARCH_P2_INCLUDE_IRQ_H
#define __ARCH_P2_INCLUDE_IRQ_H
#include <nuttx/irq.h>
#include <arch/types.h>
#define P2_IRQ_TIMER0 0
#define P2_IRQ_UART0  1
#define P2_IRQ_NIRQS  16
#define NR_IRQS P2_IRQ_NIRQS
#ifndef __ASSEMBLY__
extern volatile xcpt_reg_t *g_current_regs;
static inline xcpt_reg_t *up_current_regs(void){return (xcpt_reg_t *)g_current_regs;}
static inline void up_set_current_regs(xcpt_reg_t *regs){g_current_regs=regs;}
#endif
#endif
