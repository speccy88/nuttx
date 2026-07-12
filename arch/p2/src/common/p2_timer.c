#include <nuttx/config.h>
#include <nuttx/arch.h>
#include <errno.h>

#include "p2_clock.h"

uint32_t p2_timer_interval(void)
{
  return p2_tick_cycles(CONFIG_P2_SYSCLK_HZ, CLOCKS_PER_SEC);
}

int up_timer_initialize(void)
{
  /* BLOCKED/HIL-REQUIRED: a successful return would falsely claim that the
   * periodic counter interrupt is armed.  Keep this explicit until the PASM2
   * interrupt source and return path are verified on hardware.
   */

  return -ENOSYS;
}
