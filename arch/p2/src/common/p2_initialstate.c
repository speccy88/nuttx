#include <nuttx/config.h>
#include <nuttx/sched.h>
#include <arch/context.h>
void up_initial_state(struct tcb_s *tcb)
{
  xcpt_reg_t *regs = (xcpt_reg_t *)tcb->xcp.regs;
  for (int i = 0; i < P2_XCPT_REGS; i++) regs[i] = 0;
  regs[P2_REG_PC] = (xcpt_reg_t)tcb->start;
  regs[P2_REG_PTRA] = (xcpt_reg_t)tcb->stack_base_ptr;
}
