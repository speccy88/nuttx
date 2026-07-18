#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import pathlib
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
RAMDISK_SOURCE = ROOT / "drivers/misc/ramdisk.c"


CONFIG_HEADER = """\
#ifndef __NUTTX_CONFIG_H
#define __NUTTX_CONFIG_H
#define CONFIG_DEBUG_FEATURES 1
#define FAR
#define OK 0
#endif
"""


DEBUG_HEADER = """\
#ifndef __NUTTX_DEBUG_H
#define __NUTTX_DEBUG_H
#include <assert.h>
#define DEBUGASSERT(c) assert(c)
#define finfo(...) ((void)0)
#define ferr(...) ((void)0)
#endif
"""


KMALLOC_HEADER = """\
#ifndef __NUTTX_KMALLOC_H
#define __NUTTX_KMALLOC_H
#include <stddef.h>
void *kmm_zalloc(size_t size);
void kmm_free(void *memory);
#endif
"""


FS_HEADER = """\
#ifndef __NUTTX_FS_FS_H
#define __NUTTX_FS_FS_H
#include <stdbool.h>
#include <stdint.h>
#include <sys/types.h>

#define BIOC_XIPBASE 0x1234

struct inode
{
  void *i_private;
};

struct geometry
{
  bool geo_available;
  bool geo_mediachanged;
  bool geo_writeenabled;
  blkcnt_t geo_nsectors;
  uint16_t geo_sectorsize;
};

struct block_operations
{
  int (*open)(struct inode *inode);
  int (*close)(struct inode *inode);
  ssize_t (*read)(struct inode *inode, unsigned char *buffer,
                  blkcnt_t start_sector, unsigned int nsectors);
  ssize_t (*write)(struct inode *inode, const unsigned char *buffer,
                   blkcnt_t start_sector, unsigned int nsectors);
  int (*geometry)(struct inode *inode, struct geometry *geometry);
  int (*ioctl)(struct inode *inode, int cmd, unsigned long arg);
  int (*unlink)(struct inode *inode);
};

int register_blockdriver(const char *path,
                         const struct block_operations *operations,
                         mode_t mode, void *private_data);
#endif
"""


HARNESS_SOURCE = r"""\
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <nuttx/drivers/ramdisk.h>
#include <nuttx/fs/fs.h>
#include <nuttx/kmalloc.h>

static const struct block_operations *g_operations;
static struct inode g_inode;
static void *g_backing;
static void *g_private_data;
static int g_backing_frees;
static int g_private_frees;

void *kmm_zalloc(size_t size)
{
  return calloc(1, size);
}

void kmm_free(void *memory)
{
  if (memory == g_backing)
    {
      g_backing_frees++;
      return;
    }

  if (memory == g_private_data)
    {
      g_private_frees++;
      free(memory);
      return;
    }

  assert(!"unexpected kmm_free target");
}

int register_blockdriver(const char *path,
                         const struct block_operations *operations,
                         mode_t mode, void *private_data)
{
  assert(strcmp(path, "/dev/ram7") == 0);
  assert(mode == 0);
  assert(operations != NULL);
  assert(private_data != NULL);

  g_operations = operations;
  g_private_data = private_data;
  g_inode.i_private = private_data;
  return 0;
}

int main(void)
{
  static uint8_t backing[16] =
  {
    0x10, 0x11, 0x12, 0x13,
    0x20, 0x21, 0x22, 0x23,
    0x30, 0x31, 0x32, 0x33,
    0x40, 0x41, 0x42, 0x43
  };
  uint8_t output[4] = {0};
  void *xipbase = (void *)(uintptr_t)0xdeadbeef;
  int ret;

  g_backing = backing;
  ret = ramdisk_register(7, backing, 4, 4, RDFLAG_NO_XIP);
  assert(ret == 0);
  assert(g_operations != NULL);

  ret = g_operations->ioctl(&g_inode, BIOC_XIPBASE,
                            (unsigned long)(uintptr_t)&xipbase);
  assert(ret == -ENOTTY);
  assert(xipbase == NULL);

  ret = (int)g_operations->read(&g_inode, output, 2, 1);
  assert(ret == 1);
  assert(memcmp(output, &backing[8], sizeof(output)) == 0);

  ret = g_operations->unlink(&g_inode);
  assert(ret == 0);
  assert(g_backing_frees == 0);
  assert(g_private_frees == 1);

  puts("PASS: RDFLAG_NO_XIP uses buffered reads without freeing backing");
  return 0;
}
"""


class RamdiskNoXipTests(unittest.TestCase):
    def test_no_xip_ioctl_read_and_unlink_contract(self):
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("host C compiler is unavailable")

        with tempfile.TemporaryDirectory() as temporary:
            build = pathlib.Path(temporary)
            include = build / "include" / "nuttx"
            (include / "fs").mkdir(parents=True)
            (include / "drivers").mkdir()

            (include / "config.h").write_text(CONFIG_HEADER, encoding="utf-8")
            (include / "debug.h").write_text(DEBUG_HEADER, encoding="utf-8")
            (include / "kmalloc.h").write_text(KMALLOC_HEADER, encoding="utf-8")
            (include / "fs" / "fs.h").write_text(FS_HEADER, encoding="utf-8")
            shutil.copyfile(
                ROOT / "include/nuttx/drivers/ramdisk.h",
                include / "drivers" / "ramdisk.h",
            )

            harness = build / "ramdisk_no_xip_harness.c"
            harness.write_text(
                textwrap.dedent(HARNESS_SOURCE), encoding="utf-8"
            )
            executable = build / "ramdisk_no_xip_test"

            compiled = subprocess.run(
                [
                    compiler,
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    f"-I{build / 'include'}",
                    str(RAMDISK_SOURCE),
                    str(harness),
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(
                [str(executable)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

        self.assertIn("PASS: RDFLAG_NO_XIP", result.stdout)


if __name__ == "__main__":
    unittest.main()
