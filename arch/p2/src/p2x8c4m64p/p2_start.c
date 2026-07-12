#include <nuttx/config.h>
#include <nuttx/init.h>
void __start(void) noreturn_function;
void __start(void){nx_start(); for(;;);}
