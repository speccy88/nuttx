/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec32mb/src/p2_ec32mb_userleds.c
 *
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#include <stdbool.h>
#include <stdint.h>

#include <arch/board/board.h>

/****************************************************************************
 * Private Data
 ****************************************************************************/

static const uint8_t g_ledpins[BOARD_NLEDS] =
{
  BOARD_LED0_PIN,
  BOARD_LED1_PIN,
};

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static void p2_userled_write(unsigned int pin, bool on)
{
  if (on)
    {
      __asm__ __volatile__("drvh %0" : : "ri" (pin));
    }
  else
    {
      __asm__ __volatile__("drvl %0" : : "ri" (pin));
    }
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

uint32_t board_userled_initialize(void)
{
  unsigned int led;

  for (led = 0; led < BOARD_NLEDS; led++)
    {
      p2_userled_write(g_ledpins[led], false);
    }

  return BOARD_NLEDS;
}

void board_userled(int led, bool ledon)
{
  if ((unsigned int)led < BOARD_NLEDS)
    {
      p2_userled_write(g_ledpins[led], ledon);
    }
}

void board_userled_all(uint32_t ledset)
{
  board_userled(BOARD_LED0, (ledset & BOARD_LED0_BIT) != 0);
  board_userled(BOARD_LED1, (ledset & BOARD_LED1_BIT) != 0);
}
