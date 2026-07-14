/* SPDX-License-Identifier: Apache-2.0 */

#include <stdint.h>
#include <string.h>

#include <arch/board/p2_ec32mb_bank.h>

#include "p2bank_logic.h"

#define CHECK(condition) do { if (!(condition)) return __LINE__; } while (0)

static uint32_t test_crc32(const uint8_t *buffer, size_t length)
{
  uint32_t state = UINT32_MAX;
  size_t offset;

  for (offset = 0; offset < length; offset++)
    {
      state = p2_bank_crc32_byte(state, buffer[offset]);
    }

  return state ^ UINT32_MAX;
}

int main(void)
{
  static const uint8_t check[] = "123456789";
  struct p2_bank_handoff_s handoff;
  uint32_t saved_crc;

  CHECK(test_crc32(check, sizeof(check) - 1) == UINT32_C(0xcbf43926));
  CHECK(P2_BANK_PSRAM_STAGE_ADDRESS + P2_BANK_HUB_IMAGE_LIMIT ==
        UINT32_C(33554432));
  CHECK(P2_BANK_HANDOFF_ADDRESS + sizeof(handoff) <= UINT32_C(0x80000));

  memset(&handoff, 0, sizeof(handoff));
  handoff.magic = P2_BANK_HANDOFF_MAGIC;
  handoff.version = P2_BANK_HANDOFF_VERSION;
  handoff.header_size = sizeof(handoff);
  handoff.bank_size = P2_BANK_HUB_IMAGE_LIMIT;
  handoff.bank_crc32 = UINT32_C(0x12345678);
  strcpy(handoff.script_path, "/mnt/sd/berry-p2/widgets.be");
  handoff.handoff_crc32 = p2_bank_handoff_crc32(&handoff);
  CHECK(p2_bank_handoff_valid(&handoff));

  saved_crc = handoff.handoff_crc32;
  handoff.script_path[8] ^= 1;
  CHECK(!p2_bank_handoff_valid(&handoff));
  handoff.script_path[8] ^= 1;
  handoff.handoff_crc32 = saved_crc;
  CHECK(p2_bank_handoff_valid(&handoff));

  memset(handoff.script_path, 'x', sizeof(handoff.script_path));
  handoff.handoff_crc32 = p2_bank_handoff_crc32(&handoff);
  CHECK(!p2_bank_handoff_valid(&handoff));

  CHECK(p2bank_path_safe("/mnt/flash/banks/berry.bin",
                         "/mnt/flash/", 255));
  CHECK(p2bank_path_safe("/mnt/sd/berry-p2/lvgl widgets.be",
                         "/mnt/sd/", 191));
  CHECK(!p2bank_path_safe("/mnt/flash/", "/mnt/flash/", 255));
  CHECK(!p2bank_path_safe("/mnt/sd//widget.be", "/mnt/sd/", 191));
  CHECK(!p2bank_path_safe("/mnt/sd/./widget.be", "/mnt/sd/", 191));
  CHECK(!p2bank_path_safe("/mnt/sd/../widget.be", "/mnt/sd/", 191));
  CHECK(!p2bank_path_safe("/mnt/sd/widget.be/", "/mnt/sd/", 191));
  CHECK(!p2bank_path_safe("/mnt/sd/a\\b.be", "/mnt/sd/", 191));
  CHECK(!p2bank_path_safe("/mnt/flash/banks/berry.bin",
                          "/mnt/sd/", 191));

  return 0;
}
