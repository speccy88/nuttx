#ifndef __ARCH_P2_SRC_COMMON_P2_CLOCK_H
#define __ARCH_P2_SRC_COMMON_P2_CLOCK_H
#include <stdint.h>
static inline uint32_t p2_baud_ticks(uint32_t sysclk, uint32_t baud){return baud? (sysclk + baud/2u)/baud : 0u;}
static inline uint32_t p2_tick_cycles(uint32_t sysclk, uint32_t tick_hz){return tick_hz? (sysclk + tick_hz/2u)/tick_hz : 0u;}
static inline uint32_t p2_counter_delta(uint32_t now, uint32_t then){return now - then;}
#endif
