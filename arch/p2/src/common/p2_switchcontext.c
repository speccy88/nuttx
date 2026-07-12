#include <nuttx/config.h>
#include <nuttx/sched.h>
#include <nuttx/panic.h>
#include <arch/context.h>
#include <arch/types.h>
#include <errno.h>
#include <string.h>

void up_switch_context(struct tcb_s *tcb, struct tcb_s *rtcb)
{
  (void)tcb;
  (void)rtcb;
  PANIC();
}

void up_fullcontextrestore(void *restoreregs) noreturn_function;
void up_fullcontextrestore(void *restoreregs)
{
  (void)restoreregs;
  PANIC();
  for (; ; );
}

int up_saveusercontext(void *saveregs)
{
  (void)saveregs;
  return -ENOSYS;
}

void up_copyfullstate(void *dest, const void *src)
{
  memcpy(dest, src, sizeof(xcpt_reg_t) * P2_XCPT_REGS);
}

void up_schedule_sigaction(struct tcb_s *tcb, sig_deliver_t sigdeliver)
{
  (void)tcb;
  (void)sigdeliver;
  PANIC();
}
