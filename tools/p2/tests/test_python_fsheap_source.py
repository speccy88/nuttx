#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

"""Source checks for the P2 Python file-system heap contract."""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
BOARD = ROOT / "boards/p2/p2x8c4m64p/p2-ec32mb"


class PythonFsHeapSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kconfig = (ROOT / "fs/Kconfig").read_text(encoding="utf-8")
        cls.makefile = (ROOT / "fs/Makefile").read_text(encoding="utf-8")
        cls.heap = (ROOT / "fs/fs_heap.c").read_text(encoding="utf-8")
        cls.heap_header = (ROOT / "fs/fs_heap.h").read_text(encoding="utf-8")
        cls.profile = (BOARD / "configs/python/defconfig").read_text(encoding="utf-8")
        cls.startup = (ROOT / "sched/init/nx_start.c").read_text(encoding="utf-8")
        cls.xmem = (BOARD / "src/p2_ec32mb_xmem.c").read_text(encoding="utf-8")

    def test_generic_user_heap_buffer_is_explicit_and_flat_only(self) -> None:
        start = self.kconfig.index("config FS_HEAP_USER_BUFFER")
        end = self.kconfig.index("config FS_HEAPBUF_SECTION", start)
        block = self.kconfig[start:end]

        self.assertIn("depends on FS_HEAPSIZE > 0", block)
        self.assertIn("depends on BUILD_FLAT && MM_KERNEL_HEAP", block)
        self.assertIn("kumm instead of kmm", block)
        self.assertIn(
            "depends on FS_HEAPSIZE > 0 && !FS_HEAP_USER_BUFFER",
            self.kconfig,
        )
        self.assertIn("ifneq ($(CONFIG_FS_HEAPBUF_SECTION),)", self.makefile)

    def test_allocator_uses_kumm_only_for_the_opt_in_path(self) -> None:
        section = self.heap.index("#ifdef FS_HEAPBUF_SECTION")
        user = self.heap.index("#elif defined(CONFIG_FS_HEAP_USER_BUFFER)", section)
        fallback = self.heap.index("#else", user)
        end = self.heap.index("#endif", fallback)

        self.assertIn("kumm_malloc(CONFIG_FS_HEAPSIZE)", self.heap[user:fallback])
        self.assertIn("kmm_malloc(CONFIG_FS_HEAPSIZE)", self.heap[fallback:end])
        self.assertIn("if (buf == NULL)", self.heap[end:])
        self.assertIn("PANIC();", self.heap[end:])

    def test_memalign_allocation_size_attribute_names_the_size_argument(self) -> None:
        self.assertIn(
            "fs_heap_memalign(size_t alignment, size_t size) malloc_like1(2);",
            self.heap_header,
        )
        self.assertNotIn("fs_heap_memalign(size_t alignment, size_t size) malloc_like1(3);", self.heap_header)

    def test_python_profile_reserves_one_mib_psram_fs_pool(self) -> None:
        for setting in (
            "CONFIG_BUILD_FLAT=y",
            "CONFIG_FS_HEAPSIZE=1048576",
            "CONFIG_FS_HEAP_USER_BUFFER=y",
            "CONFIG_MM_KERNEL_HEAP=y",
            "CONFIG_MM_REGIONS=2",
            "CONFIG_NFILE_DESCRIPTORS_PER_BLOCK=8",
            "CONFIG_P2_EC32MB_PSRAM_UNIFIED=y",
        ):
            self.assertIn(setting, self.profile)

        self.assertNotIn("CONFIG_NFILE_DESCRIPTORS=", self.profile)
        self.assertNotIn("CONFIG_FS_HEAPBUF_SECTION=", self.profile)

    def test_p2_adds_psram_to_kumm_before_fs_initialization(self) -> None:
        extra_heaps = self.startup.index("up_extraheaps_init();")
        fs_init = self.startup.index("fs_initialize();", extra_heaps)
        self.assertLess(extra_heaps, fs_init)

        psram = self.xmem.index("ret = p2_psram_initialize();")
        add_region = self.xmem.index("kumm_addregion(", psram)
        self.assertLess(psram, add_region)
        self.assertIn(
            "P2_PSRAM_UNIFIED_BASE +\n"
            "                 CONFIG_P2_EC32MB_PSRAM_UNIFIED_RESERVE_SIZE",
            self.xmem[add_region:],
        )


if __name__ == "__main__":
    unittest.main()
