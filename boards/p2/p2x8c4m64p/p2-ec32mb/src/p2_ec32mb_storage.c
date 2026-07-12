#include <nuttx/config.h>
#include <nuttx/spi/spi.h>
#include <nuttx/mtd/mtd.h>
#include <nuttx/mmcsd.h>
#include <errno.h>
enum p2_storage_state { P2_STORAGE_IDLE, P2_STORAGE_FLASH_SELECTED, P2_STORAGE_SD_SELECTED, P2_STORAGE_RECOVERY };
static enum p2_storage_state g_state;
int p2_storage_select_flash(void){if(g_state==P2_STORAGE_SD_SELECTED)return -EBUSY; g_state=P2_STORAGE_FLASH_SELECTED; return 0;}
int p2_storage_select_sd(void){if(g_state==P2_STORAGE_FLASH_SELECTED)return -EBUSY; g_state=P2_STORAGE_SD_SELECTED; return 0;}
void p2_storage_release(void){g_state=P2_STORAGE_IDLE;}
struct spi_dev_s *p2_spiflash_spi_initialize(void){return NULL; /* DRAFTED: explicit unavailable until lower-half vtable is wired */}
struct spi_dev_s *p2_sdspi_initialize(void){return NULL; /* DRAFTED: explicit unavailable until lower-half vtable is wired */}
int p2_w25_initialize(void){return -ENOSYS;}
int p2_mmcsd_initialize(void){return -ENOSYS;}
