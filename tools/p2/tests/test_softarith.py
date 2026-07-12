#!/usr/bin/env python3
#
# SPDX-License-Identifier: Apache-2.0
#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.  The
# ASF licenses this file to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance with the
# License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the
# License for the specific language governing permissions and limitations
# under the License.

import ctypes
import os
import pathlib
import random
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SOURCE = ROOT / "arch" / "p2" / "src" / "common" / "p2_softarith.c"

HELPERS = {
    "__ashldi3",
    "__ashrdi3",
    "__divdi3",
    "__divmoddi4",
    "__divmodsi4",
    "__divsi3",
    "__lshrdi3",
    "__moddi3",
    "__modsi3",
    "__muldi3",
    "__mulsi3",
    "__udivdi3",
    "__udivmoddi4",
    "__udivmodsi4",
    "__udivsi3",
    "__umoddi3",
    "__umodsi3",
}

ABI_HEADER = r"""
#include <stdint.h>

int __mulsi3(int, int);
int __divsi3(int, int);
unsigned int __udivsi3(unsigned int, unsigned int);
int __modsi3(int, int);
unsigned int __umodsi3(unsigned int, unsigned int);
int __divmodsi4(int, int, int *);
unsigned int __udivmodsi4(unsigned int, unsigned int, unsigned int *);
long long __muldi3(long long, long long);
long long __divdi3(long long, long long);
unsigned long long __udivdi3(unsigned long long, unsigned long long);
long long __moddi3(long long, long long);
unsigned long long __umoddi3(unsigned long long, unsigned long long);
long long __divmoddi4(long long, long long, long long *);
unsigned long long __udivmoddi4(unsigned long long, unsigned long long,
                                unsigned long long *);
long long __ashldi3(long long, int);
long long __ashrdi3(long long, int);
long long __lshrdi3(long long, int);
"""


def signed(value, width):
    mask = (1 << width) - 1
    value &= mask
    if value & (1 << (width - 1)):
        return value - (1 << width)
    return value


def unsigned_divmod(numerator, denominator):
    if denominator == 0:
        return 0, numerator
    return divmod(numerator, denominator)


def signed_divmod(numerator, denominator, width):
    if denominator == 0:
        return 0, numerator

    quotient = abs(numerator) // abs(denominator)
    if (numerator < 0) != (denominator < 0):
        quotient = -quotient
    remainder = numerator - quotient * denominator
    return signed(quotient, width), signed(remainder, width)


class HostSoftArithmeticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        directory = pathlib.Path(cls.temporary.name)
        cls.header = directory / "softarith_abi.h"
        cls.library_path = directory / "libp2_softarith.so"
        cls.header.write_text(ABI_HEADER, encoding="utf-8")

        compiler = os.environ.get("CC", "cc")
        command = [
            compiler,
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wmissing-prototypes",
            "-fno-builtin",
            "-fPIC",
            "-I",
            str(ROOT / "include"),
            "-include",
            str(cls.header),
            "-shared",
            str(SOURCE),
            "-o",
            str(cls.library_path),
        ]
        subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
        cls.library = ctypes.CDLL(str(cls.library_path))
        cls.functions = {}

        cls.bind("__mulsi3", ctypes.c_int32, ctypes.c_int32, ctypes.c_int32)
        cls.bind("__divsi3", ctypes.c_int32, ctypes.c_int32, ctypes.c_int32)
        cls.bind("__modsi3", ctypes.c_int32, ctypes.c_int32, ctypes.c_int32)
        cls.bind("__udivsi3", ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32)
        cls.bind("__umodsi3", ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32)
        cls.bind(
            "__divmodsi4",
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_int32),
        )
        cls.bind(
            "__udivmodsi4",
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        )
        cls.bind("__muldi3", ctypes.c_int64, ctypes.c_int64, ctypes.c_int64)
        cls.bind("__divdi3", ctypes.c_int64, ctypes.c_int64, ctypes.c_int64)
        cls.bind("__moddi3", ctypes.c_int64, ctypes.c_int64, ctypes.c_int64)
        cls.bind("__udivdi3", ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint64)
        cls.bind("__umoddi3", ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint64)
        cls.bind(
            "__divmoddi4",
            ctypes.c_int64,
            ctypes.c_int64,
            ctypes.c_int64,
            ctypes.POINTER(ctypes.c_int64),
        )
        cls.bind(
            "__udivmoddi4",
            ctypes.c_uint64,
            ctypes.c_uint64,
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint64),
        )
        for name in ("__ashldi3", "__ashrdi3", "__lshrdi3"):
            cls.bind(name, ctypes.c_int64, ctypes.c_int64, ctypes.c_int)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def bind(cls, name, result_type, *argument_types):
        function = getattr(cls.library, name)
        function.restype = result_type
        function.argtypes = list(argument_types)
        cls.functions[name] = function

    def check_signed32(self, numerator, denominator):
        quotient, remainder = signed_divmod(numerator, denominator, 32)
        self.assertEqual(self.functions["__divsi3"](numerator, denominator), quotient)
        self.assertEqual(self.functions["__modsi3"](numerator, denominator), remainder)

        actual_remainder = ctypes.c_int32()
        actual_quotient = self.functions["__divmodsi4"](
            numerator, denominator, ctypes.byref(actual_remainder)
        )
        self.assertEqual(
            (actual_quotient, actual_remainder.value), (quotient, remainder)
        )

    def check_unsigned32(self, numerator, denominator):
        quotient, remainder = unsigned_divmod(numerator, denominator)
        self.assertEqual(self.functions["__udivsi3"](numerator, denominator), quotient)
        self.assertEqual(self.functions["__umodsi3"](numerator, denominator), remainder)

        actual_remainder = ctypes.c_uint32()
        actual_quotient = self.functions["__udivmodsi4"](
            numerator, denominator, ctypes.byref(actual_remainder)
        )
        self.assertEqual(
            (actual_quotient, actual_remainder.value), (quotient, remainder)
        )

    def check_signed64(self, numerator, denominator):
        quotient, remainder = signed_divmod(numerator, denominator, 64)
        self.assertEqual(self.functions["__divdi3"](numerator, denominator), quotient)
        self.assertEqual(self.functions["__moddi3"](numerator, denominator), remainder)

        actual_remainder = ctypes.c_int64()
        actual_quotient = self.functions["__divmoddi4"](
            numerator, denominator, ctypes.byref(actual_remainder)
        )
        self.assertEqual(
            (actual_quotient, actual_remainder.value), (quotient, remainder)
        )

    def check_unsigned64(self, numerator, denominator):
        quotient, remainder = unsigned_divmod(numerator, denominator)
        self.assertEqual(self.functions["__udivdi3"](numerator, denominator), quotient)
        self.assertEqual(self.functions["__umoddi3"](numerator, denominator), remainder)

        actual_remainder = ctypes.c_uint64()
        actual_quotient = self.functions["__udivmoddi4"](
            numerator, denominator, ctypes.byref(actual_remainder)
        )
        self.assertEqual(
            (actual_quotient, actual_remainder.value), (quotient, remainder)
        )

    def test_32_bit_boundaries(self):
        signed_values = [
            -(1 << 31),
            -(1 << 31) + 1,
            -65536,
            -2,
            -1,
            0,
            1,
            2,
            65535,
            (1 << 31) - 2,
            (1 << 31) - 1,
        ]
        unsigned_values = [
            0,
            1,
            2,
            3,
            0x7FFFFFFF,
            0x80000000,
            0xFFFFFFFE,
            0xFFFFFFFF,
        ]

        for left in signed_values:
            for right in signed_values:
                self.assertEqual(
                    self.functions["__mulsi3"](left, right),
                    signed(left * right, 32),
                )
                self.check_signed32(left, right)

        for numerator in unsigned_values:
            for denominator in unsigned_values:
                self.check_unsigned32(numerator, denominator)

    def test_32_bit_randomized(self):
        generator = random.Random(0x32325032)
        for _ in range(5000):
            signed_left = signed(generator.getrandbits(32), 32)
            signed_right = signed(generator.getrandbits(32), 32)
            unsigned_left = generator.getrandbits(32)
            unsigned_right = generator.getrandbits(32)

            self.assertEqual(
                self.functions["__mulsi3"](signed_left, signed_right),
                signed(signed_left * signed_right, 32),
            )
            self.check_signed32(signed_left, signed_right)
            self.check_unsigned32(unsigned_left, unsigned_right)

    def test_64_bit_boundaries(self):
        signed_values = [
            -(1 << 63),
            -(1 << 63) + 1,
            -(1 << 32),
            -2,
            -1,
            0,
            1,
            2,
            (1 << 32) - 1,
            (1 << 63) - 2,
            (1 << 63) - 1,
        ]
        unsigned_values = [
            0,
            1,
            2,
            3,
            0x7FFFFFFF,
            0x80000000,
            0xFFFFFFFF,
            0x100000000,
            0x7FFFFFFFFFFFFFFF,
            0x8000000000000000,
            0xFFFFFFFFFFFFFFFE,
            0xFFFFFFFFFFFFFFFF,
        ]

        for left in signed_values:
            for right in signed_values:
                self.assertEqual(
                    self.functions["__muldi3"](left, right),
                    signed(left * right, 64),
                )
                self.check_signed64(left, right)

        for numerator in unsigned_values:
            for denominator in unsigned_values:
                self.check_unsigned64(numerator, denominator)

    def test_64_bit_randomized(self):
        generator = random.Random(0x64645032)
        for _ in range(5000):
            signed_left = signed(generator.getrandbits(64), 64)
            signed_right = signed(generator.getrandbits(64), 64)
            unsigned_left = generator.getrandbits(64)
            unsigned_right = generator.getrandbits(64)

            self.assertEqual(
                self.functions["__muldi3"](signed_left, signed_right),
                signed(signed_left * signed_right, 64),
            )
            self.check_signed64(signed_left, signed_right)
            self.check_unsigned64(unsigned_left, unsigned_right)

    def test_64_bit_shifts(self):
        generator = random.Random(0x53485032)
        cases = [
            (value, amount)
            for value in (-(1 << 63), -2, -1, 0, 1, 2, (1 << 63) - 1)
            for amount in (-100, -1, 0, 1, 31, 32, 33, 63, 64, 100)
        ]
        cases.extend(
            (signed(generator.getrandbits(64), 64), generator.randrange(-8, 72))
            for _ in range(5000)
        )

        for value, amount in cases:
            if 0 <= amount < 64:
                left = signed(value << amount, 64)
                logical = signed((value & 0xFFFFFFFFFFFFFFFF) >> amount, 64)
                arithmetic = signed(value >> amount, 64)
            else:
                left = 0
                logical = 0
                arithmetic = -1 if value < 0 else 0

            self.assertEqual(self.functions["__ashldi3"](value, amount), left)
            self.assertEqual(self.functions["__lshrdi3"](value, amount), logical)
            self.assertEqual(self.functions["__ashrdi3"](value, amount), arithmetic)


class P2TargetSoftArithmeticTests(unittest.TestCase):
    def test_p2_object_is_q_free_and_has_only_hub_calls(self):
        default_root = ROOT.parent / ".p2-nuttx-cache" / "p2llvm" / "install"
        toolchain = pathlib.Path(os.environ.get("P2LLVM_ROOT", str(default_root)))
        tools = {
            name: toolchain / "bin" / name
            for name in ("clang", "llvm-nm", "llvm-objdump", "llvm-readobj")
        }
        missing = [str(path) for path in tools.values() if not path.is_file()]
        self.assertEqual(missing, [], "P2 toolchain is required: " + ", ".join(missing))

        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            header = directory / "softarith_abi.h"
            objfile = directory / "p2_softarith.o"
            header.write_text(ABI_HEADER, encoding="utf-8")

            command = [
                str(tools["clang"]),
                "--target=p2",
                "--sysroot=" + str(toolchain),
                "-std=c11",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-Wmissing-prototypes",
                "-ffreestanding",
                "-fno-builtin",
                "-fno-jump-tables",
                "-ffunction-sections",
                "-fdata-sections",
                "-I",
                str(ROOT / "include"),
                "-include",
                str(header),
                "-c",
                str(SOURCE),
                "-o",
                str(objfile),
            ]
            subprocess.run(
                command, cwd=ROOT, check=True, capture_output=True, text=True
            )

            undefined = subprocess.run(
                [str(tools["llvm-nm"]), "--undefined-only", str(objfile)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            defined = subprocess.run(
                [str(tools["llvm-nm"]), "-g", "--defined-only", str(objfile)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            disassembly = subprocess.run(
                [str(tools["llvm-objdump"]), "-dr", str(objfile)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            relocations = subprocess.run(
                [str(tools["llvm-readobj"]), "--relocations", str(objfile)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout

        exported = {line.split()[-1] for line in defined.splitlines() if line.split()}
        self.assertEqual(exported, HELPERS)
        self.assertEqual(undefined, "", "unresolved target helpers:\n" + undefined)
        self.assertIsNone(
            re.search(r"\b(?:q[a-z0-9_]*|getqx|getqy)\b", disassembly, re.IGNORECASE),
            "P2 CORDIC/Q instruction escaped into software arithmetic",
        )
        self.assertNotIn("R_P2_COG9", relocations)

        relocation_types = set(re.findall(r"\bR_P2_[A-Z0-9_]+\b", relocations))
        self.assertLessEqual(relocation_types, {"R_P2_20"})
        runtime_calls = re.findall(r"R_P2_[A-Z0-9_]+\s+(__\w+)", relocations)
        self.assertEqual(
            runtime_calls,
            [],
            "recursive compiler-runtime calls: " + ", ".join(runtime_calls),
        )


if __name__ == "__main__":
    unittest.main()
