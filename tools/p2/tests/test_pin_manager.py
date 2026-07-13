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
import errno
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
SOURCE_DIR = (
    ROOT / "boards" / "p2" / "p2x8c4m64p" / "p2-ec32mb" / "src"
)
SOURCE = SOURCE_DIR / "p2_ec32mb_pins.c"

ROLE_NONE = 0
ROLE_BOARD_LED = 1
ROLE_PSRAM = 2
ROLE_STORAGE = 3
ROLE_CONSOLE = 4

OWNER_NONE = 0
OWNER_BOARD_LED = 1
OWNER_PSRAM = 2
OWNER_STORAGE = 3
OWNER_CONSOLE = 4
OWNER_GPIO = 5
OWNER_UART = 6

DIRECTION_DISABLED = 0
DIRECTION_INPUT = 1
DIRECTION_OUTPUT = 2

DRIVE_FLOAT = 0
DRIVE_PUSH_PULL = 1

EVENT_NONE = 0
EVENT_SE2 = 2

SAFE_FLOAT = 0
SAFE_LOW = 1

COG_NONE = 0xFF


class PinConfig(ctypes.Structure):
    _fields_ = [
        ("direction", ctypes.c_uint8),
        ("drive", ctypes.c_uint8),
        ("event", ctypes.c_uint8),
        ("safe", ctypes.c_uint8),
        ("smartpin_mode", ctypes.c_uint32),
    ]


class PinState(ctypes.Structure):
    _fields_ = [
        ("pin", ctypes.c_uint8),
        ("reserved_role", ctypes.c_uint8),
        ("owner", ctypes.c_uint8),
        ("owning_cog", ctypes.c_uint8),
        ("refs", ctypes.c_uint16),
        ("direction", ctypes.c_uint8),
        ("drive", ctypes.c_uint8),
        ("event", ctypes.c_uint8),
        ("safe", ctypes.c_uint8),
        ("smartpin_mode", ctypes.c_uint32),
    ]


class TargetPinManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.library_path = pathlib.Path(cls.temporary.name) / "libp2pins.so"
        compiler = os.environ.get("CC", "cc")
        command = [
            compiler,
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wmissing-prototypes",
            "-DP2_PIN_MANAGER_HOST_TEST=1",
            "-DCONFIG_ARCH_LEDS=1",
            "-fPIC",
            "-shared",
            "-I",
            str(SOURCE_DIR),
            str(SOURCE),
            "-o",
            str(cls.library_path),
        ]
        subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
        cls.library = ctypes.CDLL(str(cls.library_path))

        cls.library.p2_pin_initialize.restype = ctypes.c_int
        cls.library.p2_pin_reserved_role.argtypes = [ctypes.c_uint]
        cls.library.p2_pin_reserved_role.restype = ctypes.c_int
        cls.library.p2_pin_claim.argtypes = [ctypes.c_uint, ctypes.c_int]
        cls.library.p2_pin_claim.restype = ctypes.c_int
        cls.library.p2_pin_configure.argtypes = [
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.POINTER(PinConfig),
        ]
        cls.library.p2_pin_configure.restype = ctypes.c_int
        cls.library.p2_pin_release.argtypes = [ctypes.c_uint, ctypes.c_int]
        cls.library.p2_pin_release.restype = ctypes.c_int
        cls.library.p2_pin_transfer_claims.argtypes = [
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        cls.library.p2_pin_transfer_claims.restype = ctypes.c_int
        cls.safe_callback_type = ctypes.CFUNCTYPE(None)
        cls.library.p2_pin_stop_and_forget_cog.argtypes = [
            ctypes.c_uint,
            ctypes.c_int,
            cls.safe_callback_type,
        ]
        cls.library.p2_pin_stop_and_forget_cog.restype = ctypes.c_int
        cls.library.p2_pin_get_state.argtypes = [
            ctypes.c_uint,
            ctypes.POINTER(PinState),
        ]
        cls.library.p2_pin_get_state.restype = ctypes.c_int
        cls.library.p2_pin_test_reset.restype = None
        cls.library.p2_pin_test_set_cog.argtypes = [ctypes.c_uint]
        cls.library.p2_pin_test_set_cog.restype = None
        cls.library.p2_pin_test_safe_apply_count.restype = ctypes.c_uint
        cls.library.p2_pin_test_cog_stop_count.restype = ctypes.c_uint

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.library.p2_pin_test_reset()

    def state(self, pin):
        state = PinState()
        self.assertEqual(self.library.p2_pin_get_state(pin, ctypes.byref(state)), 0)
        return state

    def initialize(self):
        self.assertEqual(self.library.p2_pin_initialize(), 0)
        self.assertEqual(self.library.p2_pin_initialize(), 0)

    def test_initialization_and_physical_reservations(self):
        state = PinState()
        self.assertEqual(
            self.library.p2_pin_get_state(0, ctypes.byref(state)), -errno.EAGAIN
        )
        self.initialize()

        expected = {
            0: ROLE_NONE,
            38: ROLE_BOARD_LED,
            39: ROLE_BOARD_LED,
            40: ROLE_PSRAM,
            57: ROLE_PSRAM,
            58: ROLE_STORAGE,
            61: ROLE_STORAGE,
            62: ROLE_CONSOLE,
            63: ROLE_CONSOLE,
        }
        for pin, role in expected.items():
            self.assertEqual(self.library.p2_pin_reserved_role(pin), role)
            state = self.state(pin)
            self.assertEqual(state.pin, pin)
            self.assertEqual(state.reserved_role, role)
            self.assertEqual(state.owner, OWNER_NONE)
            self.assertEqual(state.owning_cog, COG_NONE)

        self.assertEqual(self.library.p2_pin_reserved_role(64), -errno.EINVAL)

    def test_reserved_roles_only_accept_matching_owner(self):
        self.initialize()
        cases = [
            (38, OWNER_BOARD_LED),
            (40, OWNER_PSRAM),
            (58, OWNER_STORAGE),
            (62, OWNER_CONSOLE),
        ]
        for pin, owner in cases:
            self.assertEqual(
                self.library.p2_pin_claim(pin, OWNER_GPIO), -errno.EBUSY
            )
            self.assertEqual(self.library.p2_pin_claim(pin, owner), 0)
            self.assertEqual(self.library.p2_pin_release(pin, owner), 0)

    def test_owner_cog_conflicts_and_reference_counts(self):
        self.initialize()
        self.assertEqual(self.library.p2_pin_claim(0, OWNER_GPIO), 0)
        self.assertEqual(self.library.p2_pin_claim(0, OWNER_GPIO), 0)
        self.assertEqual(self.state(0).refs, 2)
        self.assertEqual(self.library.p2_pin_claim(0, OWNER_UART), -errno.EBUSY)

        self.library.p2_pin_test_set_cog(1)
        self.assertEqual(self.library.p2_pin_claim(0, OWNER_GPIO), -errno.EBUSY)
        self.assertEqual(self.library.p2_pin_release(0, OWNER_GPIO), -errno.EPERM)

        self.library.p2_pin_test_set_cog(0)
        self.assertEqual(self.library.p2_pin_release(0, OWNER_GPIO), 0)
        self.assertEqual(self.state(0).refs, 1)
        self.assertEqual(self.library.p2_pin_test_safe_apply_count(), 0)
        self.assertEqual(self.library.p2_pin_release(0, OWNER_GPIO), 0)
        self.assertEqual(self.library.p2_pin_test_safe_apply_count(), 1)
        state = self.state(0)
        self.assertEqual(state.owner, OWNER_NONE)
        self.assertEqual(state.refs, 0)
        self.assertEqual(state.direction, DIRECTION_DISABLED)

    def test_configuration_and_event_allocation(self):
        self.initialize()
        self.assertEqual(self.library.p2_pin_claim(0, OWNER_GPIO), 0)
        self.assertEqual(self.library.p2_pin_claim(1, OWNER_UART), 0)

        config = PinConfig(
            DIRECTION_OUTPUT,
            DRIVE_PUSH_PULL,
            EVENT_SE2,
            SAFE_LOW,
            0x10,
        )
        self.assertEqual(
            self.library.p2_pin_configure(0, OWNER_GPIO, ctypes.byref(config)), 0
        )
        state = self.state(0)
        self.assertEqual(state.direction, DIRECTION_OUTPUT)
        self.assertEqual(state.drive, DRIVE_PUSH_PULL)
        self.assertEqual(state.event, EVENT_SE2)
        self.assertEqual(state.safe, SAFE_LOW)
        self.assertEqual(state.smartpin_mode, 0x10)

        conflict = PinConfig(
            DIRECTION_INPUT,
            DRIVE_FLOAT,
            EVENT_SE2,
            SAFE_FLOAT,
            0,
        )
        self.assertEqual(
            self.library.p2_pin_configure(1, OWNER_UART, ctypes.byref(conflict)),
            -errno.EBUSY,
        )

        self.library.p2_pin_test_set_cog(1)
        self.assertEqual(self.library.p2_pin_claim(2, OWNER_UART), 0)
        self.assertEqual(
            self.library.p2_pin_configure(2, OWNER_UART, ctypes.byref(conflict)),
            0,
        )

    def test_claims_transfer_then_stop_and_forget_as_one_transaction(self):
        self.initialize()
        self.assertEqual(self.library.p2_pin_claim(40, OWNER_PSRAM), 0)
        self.assertEqual(self.library.p2_pin_claim(41, OWNER_PSRAM), 0)
        self.assertEqual(
            self.library.p2_pin_transfer_claims(OWNER_PSRAM, 1, 3),
            -errno.ENOENT,
        )
        self.assertEqual(self.state(40).owning_cog, 0)
        self.assertEqual(self.state(41).owning_cog, 0)
        self.assertEqual(
            self.library.p2_pin_transfer_claims(OWNER_PSRAM, 1, 2), 2
        )
        self.assertEqual(self.state(40).owning_cog, 1)
        self.assertEqual(self.state(41).owning_cog, 1)

        callbacks = []

        @self.safe_callback_type
        def make_safe():
            callbacks.append("safe")

        self.assertEqual(
            self.library.p2_pin_stop_and_forget_cog(
                1, OWNER_PSRAM, make_safe
            ),
            2,
        )
        self.assertEqual(callbacks, ["safe"])
        self.assertEqual(self.library.p2_pin_test_cog_stop_count(), 1)
        self.assertEqual(self.library.p2_pin_test_safe_apply_count(), 0)
        self.assertEqual(self.state(40).owner, OWNER_NONE)
        self.assertEqual(self.state(41).owner, OWNER_NONE)
        self.assertEqual(self.library.p2_pin_claim(40, OWNER_PSRAM), 0)

    def test_cog_transfer_and_stop_reject_live_or_invalid_identity(self):
        self.initialize()
        callback = self.safe_callback_type(lambda: None)
        self.assertEqual(
            self.library.p2_pin_transfer_claims(OWNER_PSRAM, 0, 1),
            -errno.EINVAL,
        )
        self.assertEqual(
            self.library.p2_pin_transfer_claims(OWNER_PSRAM, 8, 1),
            -errno.EINVAL,
        )
        self.assertEqual(
            self.library.p2_pin_stop_and_forget_cog(
                0, OWNER_PSRAM, callback
            ),
            -errno.EINVAL,
        )
        self.assertEqual(
            self.library.p2_pin_stop_and_forget_cog(
                8, OWNER_PSRAM, callback
            ),
            -errno.EINVAL,
        )
        self.assertEqual(
            self.library.p2_pin_stop_and_forget_cog(
                1, OWNER_NONE, callback
            ),
            -errno.EINVAL,
        )

    def test_invalid_requests_fail_closed(self):
        self.initialize()
        self.assertEqual(self.library.p2_pin_claim(64, OWNER_GPIO), -errno.EINVAL)
        self.assertEqual(self.library.p2_pin_claim(0, OWNER_NONE), -errno.EINVAL)
        self.assertEqual(self.library.p2_pin_release(0, OWNER_GPIO), -errno.EPERM)

        self.assertEqual(self.library.p2_pin_claim(0, OWNER_GPIO), 0)
        invalid_mode = PinConfig(
            DIRECTION_INPUT,
            DRIVE_FLOAT,
            EVENT_NONE,
            SAFE_FLOAT,
            3,
        )
        self.assertEqual(
            self.library.p2_pin_configure(
                0, OWNER_GPIO, ctypes.byref(invalid_mode)
            ),
            -errno.EINVAL,
        )

    def test_reference_count_overflow_is_rejected(self):
        self.initialize()
        for _ in range(0xFFFF):
            self.assertEqual(self.library.p2_pin_claim(0, OWNER_GPIO), 0)
        self.assertEqual(self.state(0).refs, 0xFFFF)
        self.assertEqual(
            self.library.p2_pin_claim(0, OWNER_GPIO), -errno.EOVERFLOW
        )


if __name__ == "__main__":
    unittest.main()
