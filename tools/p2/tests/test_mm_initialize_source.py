#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

"""Release-build boundary tests for the standard NuttX heap initializer."""

import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
SOURCE_PATH = ROOT / "mm/mm_heap/mm_initialize.c"


def function_source(source: str, signature: str) -> str:
    """Extract one complete C function, including its closing brace."""

    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0

    for offset in range(opening, len(source)):
        if source[offset] == "{":
            depth += 1
        elif source[offset] == "}":
            depth -= 1
            if depth == 0:
                return source[start : offset + 1]

    raise AssertionError(f"unterminated function: {signature}")


class MmInitializeSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")
        cls.addregion = function_source(cls.source, "void mm_addregion")
        cls.initialize = function_source(
            cls.source, "mm_initialize_heap(FAR const struct mm_heap_config_s"
        )

    def test_initial_context_and_region_are_validated_before_subtraction(self) -> None:
        align_check = self.initialize.index("if (adjustment > heapsize ||")
        align_subtract = self.initialize.index("heapsize -= adjustment;")
        context_check = self.initialize.index(
            "if (heapsize < sizeof(struct mm_heap_s)"
        )
        region_adjust = self.initialize.index("regionadjustment =")
        region_check = self.initialize.index(
            "if (regionadjustment > regionsize"
        )
        context_subtract = self.initialize.index(
            "heapsize -= sizeof(struct mm_heap_s);"
        )

        self.assertLess(align_check, align_subtract)
        self.assertLess(align_subtract, context_check)
        self.assertLess(context_check, region_adjust)
        self.assertLess(region_adjust, region_check)
        self.assertLess(region_check, context_subtract)
        self.assertIn("return NULL;", self.initialize[context_check:region_adjust])
        self.assertIn("if (heap->mm_heapsize == 0)", self.initialize)

    def test_addregion_validates_before_heap_metadata_is_modified(self) -> None:
        prepare = self.addregion.index(
            "adjustment = (-(heapaddr + 2 * MM_SIZEOF_ALLOCNODE))"
        )
        leading_check = self.addregion.index("if (adjustment > heapsize")
        register = self.addregion.index("kasan_register(")
        account = self.addregion.index("heap->mm_heapsize += heapsize;")
        end_check = self.addregion.index("if (heapend < heapbase")
        end_subtract = self.addregion.index("heapsize = heapend - heapbase;")

        self.assertLess(prepare, leading_check)
        self.assertLess(leading_check, register)
        self.assertLess(register, account)
        self.assertLess(end_check, end_subtract)
        self.assertIn("mm_unlock(heap);", self.addregion[leading_check:register])
        self.assertIn("MM_MIN_REGION_SIZE", self.addregion)

    def test_region_boundary_arithmetic_on_host(self) -> None:
        probe = """
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FAR
#define MM_SIZEOF_ALLOCNODE 8
#define MM_MIN_CHUNK 16
#define MM_ALIGN 8
#define MM_GRAN_MASK (MM_ALIGN - 1)
#define MM_ALIGN_UP(a) (((a) + MM_GRAN_MASK) & ~MM_GRAN_MASK)
#define MM_ALIGN_DOWN(a) ((a) & ~MM_GRAN_MASK)
#define MM_MIN_REGION_SIZE (2 * MM_SIZEOF_ALLOCNODE + MM_MIN_CHUNK)

static bool region_valid(uintptr_t heapaddr, size_t heapsize)
{
  uintptr_t heapbase;
  uintptr_t heapend;
  size_t adjustment;

  adjustment = (-(heapaddr + 2 * MM_SIZEOF_ALLOCNODE)) & MM_GRAN_MASK;
  if (adjustment > heapsize || heapaddr > UINTPTR_MAX - adjustment)
    {
      return false;
    }

  heapbase = heapaddr + adjustment;
  heapsize -= adjustment;
  if (heapsize < MM_MIN_REGION_SIZE ||
      heapsize > UINTPTR_MAX - heapbase)
    {
      return false;
    }

  heapend = MM_ALIGN_DOWN(heapbase + heapsize);
  return heapend >= heapbase &&
         heapend - heapbase >= MM_MIN_REGION_SIZE;
}

static bool initial_valid(uintptr_t heapaddr, size_t heapsize,
                          size_t contextsize)
{
  uintptr_t heap_adj;
  uintptr_t regionaddr;
  size_t adjustment;
  size_t regionadjustment;
  size_t regionsize;

  adjustment = (-heapaddr) & MM_GRAN_MASK;
  if (adjustment > heapsize || heapaddr > UINTPTR_MAX - adjustment)
    {
      return false;
    }

  heap_adj = heapaddr + adjustment;
  heapsize -= adjustment;
  if (heapsize < contextsize || heap_adj > UINTPTR_MAX - contextsize)
    {
      return false;
    }

  regionaddr = heap_adj + contextsize;
  regionsize = heapsize - contextsize;
  regionadjustment =
    (-(regionaddr + 2 * MM_SIZEOF_ALLOCNODE)) & MM_GRAN_MASK;
  return regionadjustment <= regionsize &&
         regionaddr <= UINTPTR_MAX - regionadjustment &&
         regionsize - regionadjustment >= MM_MIN_REGION_SIZE &&
         regionsize - regionadjustment <=
           UINTPTR_MAX - regionaddr - regionadjustment;
}

int main(void)
{
  assert(region_valid(0x1000, 32));
  assert(region_valid(0x1001, 39));
  assert(!region_valid(0x1001, 38));
  assert(!region_valid(0x1001, 6));
  assert(!region_valid(UINTPTR_MAX - 3, 64));
  assert(!region_valid(0x1000, UINTPTR_MAX));

  /* The field failure was 272 bytes for a 360-byte heap context. */

  assert(!initial_valid(0x1000, 272, 360));
  assert(initial_valid(0x1000, 392, 360));
  assert(!initial_valid(0x1000, 391, 360));
  assert(initial_valid(0x1001, 399, 360));
  assert(!initial_valid(0x1001, 398, 360));
  return 0;
}
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = pathlib.Path(tmpdir)
            source = tmp / "mm-region-boundary.c"
            executable = tmp / "mm-region-boundary"
            source.write_text(probe, encoding="utf-8")
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(source),
                    "-o",
                    str(executable),
                ],
                check=True,
                cwd=ROOT,
            )
            subprocess.run([str(executable)], check=True, cwd=ROOT)


if __name__ == "__main__":
    unittest.main()
