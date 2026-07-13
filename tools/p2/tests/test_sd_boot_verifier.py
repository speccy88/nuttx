# SPDX-License-Identifier: Apache-2.0

import datetime
import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools/p2/verify-sd-boot.py"


def load_module():
    spec = importlib.util.spec_from_file_location("p2_verify_sd_boot", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify_sd_boot = load_module()


BOOT_TEXT = (
    "P2BOOT:ENTRY\r\n"
    "P2BOOT:DATA=OK\r\n"
    "P2BOOT:BSS=OK\r\n"
    "P2BOOT:NX_START\r\n"
    "P2STORAGE:W25=UNAVAILABLE:CHECK_FLASH_SWITCH\r\n"
    "P2STORAGE:MMCSD_FREQUENCY ID=400000 TRANSFER=2000000\r\n"
    "P2STORAGE:MMCSD=/dev/mmcsd0\r\n"
    "P2FLASHBOOT:SMARTFS=UNAVAILABLE:CHECK_FLASH_SWITCH\r\n"
    "P2SHOWCASE:READY:BOARD=p2-ec32mb:RUN=p2help\r\n"
    "NuttShell (NSH) NuttX-12.x\r\n"
    "nsh> "
).encode("ascii")


class ManualClock:
    def __init__(self):
        self.value = 0.0
        self.epoch = datetime.datetime(2026, 7, 13, tzinfo=datetime.timezone.utc)

    def monotonic(self):
        return self.value

    def sleep(self, duration):
        self.value += max(0.0, duration)

    def utc_now(self):
        return self.epoch + datetime.timedelta(seconds=self.value)


class FakeSerial:
    def __init__(self, events):
        self.events = list(events)
        self.is_open = True
        self.dtr = True
        self.dtr_transitions = []
        self.input_flushes = 0
        self.writes = []

    def __setattr__(self, name, value):
        if name == "dtr" and "dtr_transitions" in self.__dict__:
            self.dtr_transitions.append(value)
        object.__setattr__(self, name, value)

    def reset_input_buffer(self):
        self.input_flushes += 1

    def read(self, size):
        del size
        if self.events:
            return self.events.pop(0)
        return b""

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def close(self):
        self.is_open = False


class FakeLock:
    def __init__(self, path, timeout=0.0):
        self.path = path
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None


class SdBootVerifierTests(unittest.TestCase):
    def fixture(self, root):
        image = root / "_BOOT_P2.BIX"
        image.write_bytes(b"showcase raw image")
        artifact = root / "write"
        artifact.mkdir()
        status = {
            "action": "sd-boot-write",
            "status": "PASS",
            "boot_status": "UNVERIFIED",
            "output_filename": "_BOOT_P2.BIX",
            "fragmentation_verified": False,
            "port": "/dev/fake-p2",
            "image_size": image.stat().st_size,
            "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "writer_sha256": "1" * 64,
            "loadp2_sha256": "2" * 64,
        }
        (artifact / "status.json").write_text(json.dumps(status), encoding="utf-8")
        return image, artifact

    def arguments(
        self,
        image,
        write,
        output=None,
        execute=False,
        confirm=False,
        manual_reset=False,
    ):
        values = [
            "--board",
            "p2-ec32mb",
            "--port",
            "/dev/fake-p2",
            "--image",
            str(image),
            "--write-artifact",
            str(write),
        ]
        if output is not None:
            values.extend(("--artifact-dir", str(output)))
        if execute:
            values.append("--execute")
        if confirm:
            values.append("--confirm-sd-only")
        if manual_reset:
            values.append("--manual-reset")
        return values

    def run_hil(self, arguments, serial, clock, environment=None):
        factory_calls = []

        def factory(**kwargs):
            factory_calls.append(kwargs)
            return serial

        rc = verify_sd_boot.main(
            arguments,
            environment=environment or {},
            serial_factory=factory,
            lock_factory=FakeLock,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            utc_now=clock.utc_now,
        )
        return rc, factory_calls

    def test_dry_run_validates_chain_without_opening_serial_or_creating_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            image, write = self.fixture(root)
            output = root / "verify"
            serial = FakeSerial([BOOT_TEXT])
            clock = ManualClock()
            rc, calls = self.run_hil(
                self.arguments(image, write, output), serial, clock
            )
            self.assertEqual(rc, verify_sd_boot.EXIT_OK)
            self.assertEqual(calls, [])
            self.assertFalse(output.exists())

    def test_real_serial_open_supplies_port_for_the_single_dtr_reset_edge(self):
        instances = []

        class DeferredSerial:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.port = kwargs["port"]
                self.dtr = True
                self.is_open = True
                instances.append(self)

            def close(self):
                self.is_open = False

        serial_module = types.SimpleNamespace(Serial=DeferredSerial)
        with mock.patch.dict(sys.modules, {"serial": serial_module}):
            connection = verify_sd_boot.open_serial(
                "/dev/fake-p2", 230400, 0.1
            )

        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].kwargs["port"], "/dev/fake-p2")
        self.assertEqual(connection.port, "/dev/fake-p2")
        self.assertTrue(connection.dtr)
        self.assertTrue(connection.is_open)

    def test_execute_requires_explicit_sd_only_confirmation_before_serial_open(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            image, write = self.fixture(root)
            serial = FakeSerial([BOOT_TEXT])
            rc, calls = self.run_hil(
                self.arguments(image, write, root / "verify", execute=True),
                serial,
                ManualClock(),
                {"P2_HIL": "1", "P2_ALLOW_RESET": "1"},
            )
            self.assertEqual(rc, verify_sd_boot.EXIT_SAFETY)
            self.assertEqual(calls, [])

    def test_sd_only_reset_pass_records_zero_tx_and_physical_contiguity_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            image, write = self.fixture(root)
            output = root / "verify"
            serial = FakeSerial([BOOT_TEXT[:31], BOOT_TEXT[31:]])
            clock = ManualClock()
            rc, calls = self.run_hil(
                self.arguments(
                    image, write, output, execute=True, confirm=True
                ),
                serial,
                clock,
                {"P2_HIL": "1", "P2_ALLOW_RESET": "1"},
            )
            self.assertEqual(rc, verify_sd_boot.EXIT_OK)
            self.assertEqual(len(calls), 1)
            self.assertEqual(serial.writes, [])
            self.assertEqual(serial.dtr_transitions, [False])
            self.assertEqual(serial.input_flushes, 1)
            status = json.loads((output / "status.json").read_text())
            self.assertEqual(status["status"], "PASS")
            self.assertEqual(status["boot_status"], "PASS")
            self.assertTrue(status["fragmentation_verified"])
            self.assertFalse(status["loader_downloaded"])
            self.assertEqual(status["serial_tx_bytes"], 0)
            self.assertEqual(
                status["reset_method"], "DTR_SINGLE_EDGE_AFTER_QUIESCE"
            )
            self.assertEqual(status["pre_reset_quiesce_seconds"], 10.0)
            self.assertTrue(status["pre_reset_input_flushed"])
            self.assertEqual(
                status["switch_confirmation"],
                {"FLASH": "OFF", "up": "OFF", "down": "ON"},
            )
            markers = json.loads((output / "markers.json").read_text())
            self.assertTrue(markers["complete"])
            self.assertIn(
                "P2SHOWCASE:READY:BOARD=p2-ec32mb:RUN=p2help",
                markers["found"],
            )
            self.assertIn(
                "P2STORAGE:W25=UNAVAILABLE:CHECK_FLASH_SWITCH",
                markers["found"],
            )
            self.assertIn("P2STORAGE:MMCSD=/dev/mmcsd0", markers["found"])
            self.assertIn(
                "P2FLASHBOOT:SMARTFS=UNAVAILABLE:CHECK_FLASH_SWITCH",
                markers["found"],
            )
            self.assertEqual(markers["loader_signatures"], [])
            self.assertEqual((output / "console.raw").read_bytes(), BOOT_TEXT)

    def test_sd_only_boot_without_flash_switch_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            image, write = self.fixture(root)
            output = root / "verify"
            missing_switch = BOOT_TEXT.replace(
                b"P2STORAGE:W25=UNAVAILABLE:CHECK_FLASH_SWITCH\r\n", b""
            )
            serial = FakeSerial([missing_switch])
            clock = ManualClock()

            def advancing_read(size):
                clock.sleep(0.2)
                return FakeSerial.read(serial, size)

            serial.read = advancing_read
            rc, _ = self.run_hil(
                self.arguments(
                    image, write, output, execute=True, confirm=True
                )
                + ["--boot-timeout", "0.5"],
                serial,
                clock,
                {"P2_HIL": "1", "P2_ALLOW_RESET": "1"},
            )
            self.assertEqual(rc, verify_sd_boot.EXIT_HIL_FAILED)
            markers = json.loads((output / "markers.json").read_text())
            self.assertIn(
                "P2STORAGE:W25=UNAVAILABLE:CHECK_FLASH_SWITCH",
                markers["missing"],
            )

    def test_each_sd_only_storage_marker_is_mandatory(self):
        text = BOOT_TEXT.decode("ascii")
        for label, pattern in verify_sd_boot.SD_ONLY_MARKERS:
            with self.subTest(label=label):
                match = pattern.search(text)
                self.assertIsNotNone(match)
                without_marker = text[: match.start()] + text[match.end() :]
                result = verify_sd_boot.marker_status(
                    without_marker, "p2-ec32mb"
                )
                self.assertFalse(result["complete"])
                self.assertIn(label, result["missing"])

    def test_sd_only_storage_markers_must_be_in_order(self):
        frequency = (
            "P2STORAGE:MMCSD_FREQUENCY ID=400000 TRANSFER=2000000\r\n"
        )
        device = "P2STORAGE:MMCSD=/dev/mmcsd0\r\n"
        swapped = BOOT_TEXT.decode("ascii").replace(
            frequency + device, device + frequency
        )
        result = verify_sd_boot.marker_status(swapped, "p2-ec32mb")
        self.assertFalse(result["complete"])
        self.assertFalse(result["order_valid"])
        self.assertIn("boot markers are out of order", result["errors"])

    def test_sd_only_markers_cover_the_p2_ec_showcase_profile(self):
        text = BOOT_TEXT.decode("ascii").replace(
            "P2SHOWCASE:READY:BOARD=p2-ec32mb:RUN=p2help",
            "P2SHOWCASE:READY:BOARD=p2-ec:RUN=p2help",
        )
        result = verify_sd_boot.marker_status(text, "p2-ec")
        self.assertTrue(result["complete"])

    def test_generic_boot_without_selected_showcase_marker_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            image, write = self.fixture(root)
            output = root / "verify"
            generic = BOOT_TEXT.replace(
                b"P2SHOWCASE:READY:BOARD=p2-ec32mb:RUN=p2help\r\n", b""
            )
            serial = FakeSerial([generic])
            clock = ManualClock()

            def advancing_read(size):
                clock.sleep(0.2)
                return FakeSerial.read(serial, size)

            serial.read = advancing_read
            rc, _ = self.run_hil(
                self.arguments(
                    image, write, output, execute=True, confirm=True
                )
                + ["--boot-timeout", "0.5"],
                serial,
                clock,
                {"P2_HIL": "1", "P2_ALLOW_RESET": "1"},
            )
            self.assertEqual(rc, verify_sd_boot.EXIT_HIL_FAILED)
            markers = json.loads((output / "markers.json").read_text())
            self.assertIn(
                "P2SHOWCASE:READY:BOARD=p2-ec32mb:RUN=p2help",
                markers["missing"],
            )

    def test_incomplete_boot_does_not_claim_contiguity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            image, write = self.fixture(root)
            output = root / "verify"
            serial = FakeSerial([b"P2BOOT:ENTRY\r\n"])
            clock = ManualClock()

            def advancing_read(size):
                clock.sleep(0.2)
                return FakeSerial.read(serial, size)

            serial.read = advancing_read
            rc, _ = self.run_hil(
                self.arguments(
                    image, write, output, execute=True, confirm=True
                )
                + ["--boot-timeout", "0.5"],
                serial,
                clock,
                {"P2_HIL": "1", "P2_ALLOW_RESET": "1"},
            )
            self.assertEqual(rc, verify_sd_boot.EXIT_HIL_FAILED)
            status = json.loads((output / "status.json").read_text())
            self.assertEqual(status["status"], "FAIL")
            self.assertFalse(status["fragmentation_verified"])
            self.assertEqual(serial.writes, [])

    def test_manual_reset_mode_never_toggles_dtr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            image, write = self.fixture(root)
            output = root / "verify"
            serial = FakeSerial([BOOT_TEXT])
            clock = ManualClock()
            rc, _ = self.run_hil(
                self.arguments(
                    image,
                    write,
                    output,
                    execute=True,
                    confirm=True,
                    manual_reset=True,
                ),
                serial,
                clock,
                {"P2_HIL": "1", "P2_ALLOW_RESET": "1"},
            )
            self.assertEqual(rc, verify_sd_boot.EXIT_OK)
            self.assertEqual(serial.dtr_transitions, [])
            self.assertEqual(serial.writes, [])
            status = json.loads((output / "status.json").read_text())
            self.assertEqual(
                status["reset_method"],
                "USER_PHYSICAL_RESET_AFTER_SERIAL_QUIESCE",
            )

    def test_image_hash_mismatch_is_rejected_before_serial_open(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            image, write = self.fixture(root)
            image.write_bytes(b"tampered raw image")
            serial = FakeSerial([BOOT_TEXT])
            rc, calls = self.run_hil(
                self.arguments(image, write), serial, ManualClock()
            )
            self.assertEqual(rc, verify_sd_boot.EXIT_SAFETY)
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
