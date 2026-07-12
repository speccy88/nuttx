#include <nuttx/config.h>
#include <nuttx/board.h>
void board_autoled_initialize(void){}
void board_autoled_on(int led){(void)led;}
void board_autoled_off(int led){(void)led;}
int board_late_initialize(void){return 0;}
void board_initialize(void){}
