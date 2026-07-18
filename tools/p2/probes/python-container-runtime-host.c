/****************************************************************************
 * tools/p2/probes/python-container-runtime-host.c
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Host harness for arch/p2/src/common/p2_python_container.c.
 ****************************************************************************/

/****************************************************************************
 * Included Files
 ****************************************************************************/

#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <arch/overlay.h>
#include <arch/python_container.h>

/****************************************************************************
 * Pre-processor Definitions
 ****************************************************************************/

#define PROBE_MAX_GROUPS 32
#define PROBE_HUB_SIZE   (256 * 1024)

/****************************************************************************
 * Private Types
 ****************************************************************************/

struct probe_target_s
{
  struct p2_python_container_memory_s memory;
};

/****************************************************************************
 * Private Data
 ****************************************************************************/

static struct p2_overlay_group_s g_probe_groups[PROBE_MAX_GROUPS];
static struct p2_python_container_s g_probe_container;
static p2_overlay_loader_t g_probe_loader;
static void *g_probe_loader_arg;
static uint8_t g_probe_hub[PROBE_HUB_SIZE];
static uint32_t g_probe_load_address;
static uint32_t g_probe_group_count;
static int g_probe_corrupt_copy;
static uint64_t g_probe_backing_address;

static struct probe_target_s g_probe_target;

/****************************************************************************
 * Private Functions
 ****************************************************************************/

static int probe_target_read(void *arg, uint64_t address, void *buffer,
                             size_t size)
{
  struct probe_target_s *target = arg;

  if ((uintptr_t)buffer == g_probe_load_address)
    {
      if (size > sizeof(g_probe_hub))
        {
          return -ERANGE;
        }

      return p2_python_container_memory_read(&target->memory, address,
                                             g_probe_hub, size);
    }

  return p2_python_container_memory_read(&target->memory, address, buffer,
                                         size);
}

static int probe_target_write(void *arg, uint64_t address,
                              const void *buffer, size_t size)
{
  struct probe_target_s *target = arg;
  int ret = p2_python_container_memory_write(&target->memory, address,
                                             buffer, size);

  if (ret == 0 && g_probe_corrupt_copy != 0 &&
      address >= g_probe_backing_address && size != 0)
    {
      uint64_t offset = address - target->memory.address;

      target->memory.data[offset] ^= UINT8_C(0x80);
      g_probe_corrupt_copy = 0;
    }

  return ret;
}

/****************************************************************************
 * Public Functions
 ****************************************************************************/

int p2_overlay_install_groups(const struct p2_overlay_group_s *groups,
                              size_t count, uintptr_t tagged_base,
                              size_t backing_size)
{
  size_t index;

  if (groups == NULL || count < 2 || count > PROBE_MAX_GROUPS)
    {
      return -EINVAL;
    }

  for (index = 0; index < count; index++)
    {
      if (index != 0 &&
          (groups[index].source > backing_size ||
           groups[index].image_size >
             backing_size - groups[index].source))
        {
          return -ERANGE;
        }

      g_probe_groups[index] = groups[index];
      if (index != 0)
        {
          g_probe_groups[index].source += tagged_base;
        }
    }

  g_probe_group_count = count;
  return 0;
}

int p2_overlay_relocate_groups(uintptr_t tagged_base, size_t backing_size)
{
  (void)tagged_base;
  (void)backing_size;
  return -ENOSYS;
}

int p2_overlay_register_loader(p2_overlay_loader_t loader, void *arg)
{
  if (loader == NULL || g_probe_group_count < 2)
    {
      return -EINVAL;
    }

  g_probe_loader = loader;
  g_probe_loader_arg = arg;
  return 0;
}

int p2_overlay_last_error(void)
{
  return 0;
}

int p2_probe_validate(const uint8_t *data, size_t size,
                      const uint8_t fingerprint[32], uint32_t load_address,
                      uint32_t slot_size,
                      struct p2_python_container_info_s *info)
{
  struct p2_python_container_memory_s memory;
  struct p2_python_container_source_s source;
  struct p2_python_container_contract_s contract;

  memory.data = (uint8_t *)(uintptr_t)data;
  memory.address = 0;
  memory.size = size;
  source.read = p2_python_container_memory_read;
  source.arg = &memory;
  source.size = size;
  memcpy(contract.build_fingerprint, fingerprint,
         sizeof(contract.build_fingerprint));
  contract.overlay_load_address = load_address;
  contract.overlay_slot_size = slot_size;
  return p2_python_container_validate(&source, &contract, info);
}

int p2_probe_initialize(const uint8_t *data, size_t size,
                        const uint8_t fingerprint[32],
                        uint32_t load_address, uint32_t slot_size,
                        uint8_t *psram, size_t psram_size,
                        uint32_t backing_offset, size_t workspace_count,
                        int corrupt_copy)
{
  struct p2_python_container_memory_s source_memory;
  struct p2_python_container_config_s config;

  memset(&g_probe_container, 0, sizeof(g_probe_container));
  memset(g_probe_groups, 0, sizeof(g_probe_groups));
  memset(g_probe_hub, 0, sizeof(g_probe_hub));
  g_probe_loader = NULL;
  g_probe_loader_arg = NULL;
  g_probe_group_count = 0;
  g_probe_load_address = load_address;
  g_probe_corrupt_copy = corrupt_copy;
  g_probe_backing_address = UINT64_C(0x10000000) + backing_offset;

  source_memory.data = (uint8_t *)(uintptr_t)data;
  source_memory.address = 0;
  source_memory.size = size;
  memset(&config, 0, sizeof(config));
  config.source.read = p2_python_container_memory_read;
  config.source.arg = &source_memory;
  config.source.size = size;
  g_probe_target.memory.data = psram;
  g_probe_target.memory.address = UINT64_C(0x10000000);
  g_probe_target.memory.size = psram_size;
  config.target.read = probe_target_read;
  config.target.write = probe_target_write;
  config.target.zero = p2_python_container_memory_zero;
  config.target.arg = &g_probe_target;
  memcpy(config.contract.build_fingerprint, fingerprint,
         sizeof(config.contract.build_fingerprint));
  config.contract.overlay_load_address = load_address;
  config.contract.overlay_slot_size = slot_size;
  config.backing_address = (uintptr_t)g_probe_backing_address;
  config.backing_capacity = psram_size - backing_offset;
  config.group_workspace = g_probe_groups;
  config.group_workspace_count = workspace_count;
  return p2_python_container_initialize(&g_probe_container, &config);
}

int p2_probe_load_group(uint32_t group)
{
  if (g_probe_loader == NULL || group >= g_probe_group_count)
    {
      return -EINVAL;
    }

  return g_probe_loader(g_probe_loader_arg, group,
                        g_probe_groups[group].source,
                        (void *)(uintptr_t)g_probe_load_address,
                        g_probe_groups[group].image_size);
}

const uint8_t *p2_probe_hub(void)
{
  return g_probe_hub;
}

uintptr_t p2_probe_romfs_address(void)
{
  const void *address;
  size_t size;

  if (p2_python_container_get_stdlib(&g_probe_container, &address,
                                     &size) < 0)
    {
      return 0;
    }

  return (uintptr_t)address;
}

size_t p2_probe_romfs_size(void)
{
  const void *address;
  size_t size;

  if (p2_python_container_get_stdlib(&g_probe_container, &address,
                                     &size) < 0)
    {
      return 0;
    }

  return size;
}
