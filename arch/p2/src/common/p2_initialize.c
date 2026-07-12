#include <nuttx/config.h>
#include <nuttx/init.h>
#include <nuttx/board.h>
#include <nuttx/panic.h>

void up_initialize(void)
{
#ifdef CONFIG_ARCH_LEDS
  board_autoled_initialize();
#endif
}

void up_idle(void)
{
  __asm__ __volatile__("nop");
}

void p2_lowputc(int ch)
{
  /* DRAFTED: no serial pin writes are attempted in cloud. */

  (void)ch;
}

void up_putc(int ch)
{
  p2_lowputc(ch);
}
