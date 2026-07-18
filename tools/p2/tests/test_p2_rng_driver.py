#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

"""Host behavioral test for the real P2 RNG read and BLAKE2s paths."""

from __future__ import annotations

import hashlib
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
RNG_SOURCE = ROOT / "arch/p2/src/common/p2_rng.c"
BLAKE2S_SOURCE = ROOT / "crypto/blake2s.c"

CONFIG_HEADER = r"""
#ifndef __NUTTX_CONFIG_H
#define __NUTTX_CONFIG_H
#include <stddef.h>
#define CONFIG_DEV_RANDOM 1
#define CONFIG_DEV_URANDOM 1
#define CONFIG_DEV_URANDOM_ARCH 1
#define CONFIG_P2_RNG_BLAKE2S 1
#define FAR
#define UNUSED(value) ((void)(value))
#define explicit_bzero p2_test_explicit_bzero
void p2_test_explicit_bzero(void *memory, size_t size);
#endif
"""

SYS_PARAM_HEADER = r"""
#ifndef __SYS_PARAM_H
#define __SYS_PARAM_H
#define nitems(array) (sizeof(array) / sizeof((array)[0]))
#endif
"""

DRIVERS_HEADER = r"""
#ifndef __NUTTX_DRIVERS_DRIVERS_H
#define __NUTTX_DRIVERS_DRIVERS_H
#include <sys/types.h>
struct file_operations;
int register_driver(const char *path,
                    const struct file_operations *operations,
                    mode_t mode, void *private_data);
#endif
"""

FS_HEADER = r"""
#ifndef __NUTTX_FS_FS_H
#define __NUTTX_FS_FS_H
#include <stddef.h>
#include <sys/types.h>
struct file
{
  int unused;
};
struct file_operations
{
  int (*open)(struct file *filep);
  int (*close)(struct file *filep);
  ssize_t (*read)(struct file *filep, char *buffer, size_t buflen);
};
#endif
"""

DEBUG_HEADER = r"""
#ifndef __NUTTX_DEBUG_H
#define __NUTTX_DEBUG_H
#include <assert.h>
#define DEBUGASSERT(condition) assert(condition)
#endif
"""

KMALLOC_HEADER = r"""
#ifndef __NUTTX_KMALLOC_H
#define __NUTTX_KMALLOC_H
#include <stddef.h>
void *kmm_malloc(size_t size);
void kmm_free(void *memory);
#endif
"""


def c_bytes(data: bytes) -> str:
    return ", ".join(f"0x{value:02x}" for value in data)


class P2RngDriverTests(unittest.TestCase):
    def test_real_read_path_conditions_full_and_partial_blocks(self) -> None:
        if sys.byteorder != "little":
            self.skipTest("P2 and its GETRND word stream are little-endian")
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("host C compiler is unavailable")

        first = hashlib.blake2s(struct.pack("<16I", *range(16))).digest()
        second = hashlib.blake2s(struct.pack("<16I", *range(16, 32))).digest()
        expected = first + second[:5]
        harness = f"""

static uint32_t g_next_word;
static unsigned int g_registrations;

uint32_t test_getrnd(void)
{{
  return g_next_word++;
}}

void p2_test_explicit_bzero(void *memory, size_t size)
{{
  volatile unsigned char *cursor = memory;
  while (size-- > 0)
    {{
      *cursor++ = 0;
    }}
}}

void *kmm_malloc(size_t size)
{{
  return malloc(size);
}}

void kmm_free(void *memory)
{{
  free(memory);
}}

int register_driver(const char *path,
                    const struct file_operations *operations,
                    mode_t mode, void *private_data)
{{
  assert(path != NULL);
  assert(operations == &g_p2_rng_operations);
  assert(operations->read == p2_rng_read);
  assert(mode == 0444);
  assert(private_data == NULL);
  g_registrations++;
  return 0;
}}

int main(void)
{{
  static const unsigned char expected[37] = {{ {c_bytes(expected)} }};
  struct file filep = {{0}};
  unsigned char guarded[39];
  ssize_t result;

  memset(guarded, 0xa5, sizeof(guarded));
  result = p2_rng_read(&filep, (char *)&guarded[1], 37);
  assert(result == 37);
  assert(g_next_word == 32);
  assert(guarded[0] == 0xa5 && guarded[38] == 0xa5);
  assert(memcmp(&guarded[1], expected, sizeof(expected)) == 0);

  result = p2_rng_read(&filep, (char *)&guarded[1], 0);
  assert(result == 0);
  assert(g_next_word == 32);

  devrandom_register();
  devurandom_register();
  assert(g_registrations == 2);
  puts("PASS: P2 GETRND words are BLAKE2s-conditioned and chunked safely");
  return 0;
}}
"""

        source = RNG_SOURCE.read_text(encoding="utf-8")
        assembly = '__asm__ __volatile__("getrnd %0" : "=r" (value));'
        self.assertEqual(source.count(assembly), 1)
        source = source.replace(assembly, "value = test_getrnd();")
        source = (
            "#include <assert.h>\n"
            "#include <stdint.h>\n"
            "#include <stdio.h>\n"
            "#include <stdlib.h>\n"
            "uint32_t test_getrnd(void);\n"
            + source
            + harness
        )

        with tempfile.TemporaryDirectory() as temporary:
            build = pathlib.Path(temporary)
            include = build / "include"
            (include / "nuttx/crypto").mkdir(parents=True)
            (include / "nuttx/drivers").mkdir(parents=True)
            (include / "nuttx/fs").mkdir(parents=True)
            (include / "sys").mkdir(parents=True)
            (include / "nuttx/config.h").write_text(
                textwrap.dedent(CONFIG_HEADER), encoding="utf-8"
            )
            (include / "nuttx/debug.h").write_text(
                textwrap.dedent(DEBUG_HEADER), encoding="utf-8"
            )
            (include / "nuttx/kmalloc.h").write_text(
                textwrap.dedent(KMALLOC_HEADER), encoding="utf-8"
            )
            (include / "nuttx/drivers/drivers.h").write_text(
                textwrap.dedent(DRIVERS_HEADER), encoding="utf-8"
            )
            (include / "nuttx/fs/fs.h").write_text(
                textwrap.dedent(FS_HEADER), encoding="utf-8"
            )
            (include / "sys/param.h").write_text(
                textwrap.dedent(SYS_PARAM_HEADER), encoding="utf-8"
            )
            shutil.copyfile(
                ROOT / "include/nuttx/crypto/blake2s.h",
                include / "nuttx/crypto/blake2s.h",
            )
            transformed = build / "p2_rng_host.c"
            transformed.write_text(source, encoding="utf-8")
            executable = build / "p2_rng_host"
            compiled = subprocess.run(
                [
                    compiler,
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    f"-I{include}",
                    str(transformed),
                    str(BLAKE2S_SOURCE),
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

        self.assertIn("PASS: P2 GETRND", result.stdout)


if __name__ == "__main__":
    unittest.main()
