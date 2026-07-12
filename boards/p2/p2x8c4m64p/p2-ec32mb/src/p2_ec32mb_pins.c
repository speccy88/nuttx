#include <errno.h>
#include "p2_ec32mb_pins.h"
static struct p2_pin_state g_pins[P2_PIN_COUNT];
int p2_pin_reserved_owner(unsigned int pin){if(pin>=P2_PIN_COUNT)return -EINVAL; if(pin>=40&&pin<=57)return P2_PIN_PSRAM; if(pin>=58&&pin<=61)return P2_PIN_STORAGE; if(pin>=62&&pin<=63)return P2_PIN_CONSOLE; if(pin==38||pin==39)return P2_PIN_BOARD_LED; return P2_PIN_FREE;}
int p2_pin_claim(unsigned int pin, enum p2_pin_owner owner){int r=p2_pin_reserved_owner(pin); if(r<0)return r; if(r!=P2_PIN_FREE && r!=owner)return -EBUSY; if(g_pins[pin].owner && g_pins[pin].owner!=owner)return -EBUSY; g_pins[pin].owner=owner; g_pins[pin].refs++; return 0;}
int p2_pin_release(unsigned int pin, enum p2_pin_owner owner){if(pin>=P2_PIN_COUNT)return -EINVAL; if(g_pins[pin].owner!=owner || !g_pins[pin].refs)return -EPERM; if(--g_pins[pin].refs==0)g_pins[pin].owner=P2_PIN_FREE; return 0;}
