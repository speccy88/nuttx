#include <nuttx/config.h>
#include <nuttx/serial/serial.h>
#include <errno.h>
#include "p2_clock.h"
uint32_t p2_console_baud_ticks(void){return p2_baud_ticks(CONFIG_P2_SYSCLK_HZ, CONFIG_UART0_BAUD);}
#ifdef USE_SERIALDRIVER
int up_putc(int ch);
void p2_serialinit(void){}
#endif
