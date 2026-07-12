#ifndef P2_EC32MB_PINS_H
#define P2_EC32MB_PINS_H
#include <stdint.h>
#define P2_PIN_COUNT 64
enum p2_pin_owner { P2_PIN_FREE=0, P2_PIN_BOARD_LED, P2_PIN_PSRAM, P2_PIN_STORAGE, P2_PIN_CONSOLE, P2_PIN_GPIO };
struct p2_pin_state { uint8_t owner; uint8_t refs; uint8_t direction; uint32_t mode; };
int p2_pin_claim(unsigned int pin, enum p2_pin_owner owner);
int p2_pin_release(unsigned int pin, enum p2_pin_owner owner);
int p2_pin_reserved_owner(unsigned int pin);
#endif
