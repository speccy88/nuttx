/****************************************************************************
 * boards/p2/p2x8c4m64p/p2-ec/include/board.h
 *
 * SPDX-License-Identifier: Apache-2.0
 ****************************************************************************/

#ifndef __BOARDS_P2_P2X8C4M64P_P2_EC_INCLUDE_BOARD_H
#define __BOARDS_P2_P2X8C4M64P_P2_EC_INCLUDE_BOARD_H

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <nuttx/config.h>

#ifndef __ASSEMBLY__
#  include <stdint.h>
#  include <nuttx/compiler.h>
#endif

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define BOARD_XTAL_FREQUENCY        20000000
#define BOARD_SYSCLK_FREQUENCY      180000000
#define BOARD_UART0_BAUD            230400
#define BOARD_CONSOLE_TX_PIN        62
#define BOARD_CONSOLE_RX_PIN        63

/* The P2-EC Rev D guide specifies a high-impedance Schmitt buffer whose
 * corresponding LED is illuminated when P56 or P57 is driven high.
 */

#define BOARD_LED0_PIN              56
#define BOARD_LED1_PIN              57
#define BOARD_NLEDS                 2
#define BOARD_LED0                  0
#define BOARD_LED1                  1
#define BOARD_LED0_BIT              (1u << BOARD_LED0)
#define BOARD_LED1_BIT              (1u << BOARD_LED1)

#define BOARD_FLASH_MISO_PIN        58
#define BOARD_FLASH_MOSI_PIN        59
#define BOARD_FLASH_CLK_PIN         60
#define BOARD_FLASH_CS_PIN          61
#define BOARD_SD_MISO_PIN           58
#define BOARD_SD_MOSI_PIN           59
#define BOARD_SD_CS_PIN             60
#define BOARD_SD_CLK_PIN            61

#define LED_STARTED                 0
#define LED_HEAPALLOCATE            1
#define LED_IRQSENABLED             2
#define LED_STACKCREATED            3
#define LED_INIRQ                   4
#define LED_SIGNAL                  5
#define LED_ASSERTION               6
#define LED_PANIC                   7
#define LED_IDLE                    8
#define LED_NVALUES                 9

#ifndef __ASSEMBLY__

/****************************************************************************
 * Public Types
 ****************************************************************************/

struct spi_dev_s;

#ifdef CONFIG_MTD_W25
struct p2_w25_info_s
{
  uint32_t raw_blocksize;
  uint32_t raw_erasesize;
  uint32_t raw_neraseblocks;
  uint32_t data_firstblock;
  uint32_t data_nblocks;
  uint32_t data_neraseblocks;
  uint32_t boot_crc32;
  uint32_t probe_frequency;
  uint32_t active_frequency;
  uint8_t jedec[3];
};
#endif

/****************************************************************************
 * Public Function Prototypes
 ****************************************************************************/

#ifdef CONFIG_P2_STORAGE
void p2_storage_board_initialize(void);
FAR struct spi_dev_s *p2_spiflash_spi_initialize(void);
FAR struct spi_dev_s *p2_sdspi_initialize(void);
#  ifdef CONFIG_MTD_W25
int p2_w25_initialize(void);
int p2_w25_get_info(FAR struct p2_w25_info_s *info);
#  endif
#  ifdef CONFIG_MMCSD_SPI
int p2_mmcsd_initialize(void);
#  endif
#endif

#endif /* __ASSEMBLY__ */
#endif /* __BOARDS_P2_P2X8C4M64P_P2_EC_INCLUDE_BOARD_H */
