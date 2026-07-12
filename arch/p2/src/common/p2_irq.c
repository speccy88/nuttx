#include <nuttx/config.h>
#include <nuttx/irq.h>
#include <nuttx/panic.h>
#include <arch/irq.h>
#include <errno.h>

volatile xcpt_reg_t *g_current_regs;

static volatile irqstate_t g_irqstate = 1;

irqstate_t up_irq_save(void)
{
  irqstate_t flags = g_irqstate;

  g_irqstate = 1;
  return flags;
}

void up_irq_restore(irqstate_t flags)
{
  g_irqstate = flags;
}

void up_irq_enable(void)
{
  /* DRAFTED: do not claim real P2 interrupt enable before PASM2 entry/exit
   * is verified.  Keeping the shadow state explicit prevents unconditional
   * enable behaviour while still allowing source-level review.
   */

  g_irqstate = 0;
}

void up_irq_disable(void)
{
  g_irqstate = 1;
}

int up_irqinitialize(void)
{
  g_current_regs = NULL;
  up_irq_disable();

  /* Not OK: the real interrupt controller entry/return path is not wired. */

  return -ENOSYS;
}

void up_disable_irq(int irq)
{
  (void)irq;
}

void up_enable_irq(int irq)
{
  (void)irq;
  PANIC();
}

int up_prioritize_irq(int irq, int priority)
{
  (void)irq;
  (void)priority;
  return -ENOSYS;
}

int p2_dispatch_irq(int irq, xcpt_reg_t *regs)
{
  return irq_dispatch(irq, regs);
}
