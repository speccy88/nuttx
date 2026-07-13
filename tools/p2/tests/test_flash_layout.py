# SPDX-License-Identifier: Apache-2.0

import hashlib
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "lib"))

import flash_layout


ROOT = pathlib.Path(__file__).resolve().parents[3]


class FlashLayoutTests(unittest.TestCase):
    def test_maximum_image_derives_512k_boot_partition(self):
        plan = flash_layout.image_plan(flash_layout.HUB_RAM)
        self.assertEqual(plan.payload_offset, 0x90)
        self.assertEqual(plan.payload_end, 0x7C090)
        self.assertEqual(plan.program_end, 0x7C100)
        self.assertEqual(plan.erase_end, 0x80000)
        self.assertEqual(flash_layout.BOOT_SIZE, 0x80000)

    def test_four_byte_image_uses_embedded_payload_and_minimum_window(self):
        plan = flash_layout.image_plan(4)
        self.assertEqual(plan.image_padded_size, 4)
        self.assertEqual(plan.payload_offset, 0x90)
        self.assertEqual(plan.payload_end, 0x94)
        self.assertEqual(plan.program_end, 0x400)
        self.assertEqual(plan.erase_end, 0x1000)

    def test_generated_consumers_are_current(self):
        for path, expected in flash_layout.generated_files(ROOT).items():
            self.assertEqual(path.read_text(encoding="utf-8"), expected)

    def test_small_and_large_erase_ranges(self):
        self.assertEqual(flash_layout.image_plan(1).erase_end, 0x1000)
        self.assertEqual(flash_layout.image_plan(0x5000).erase_end, 0x10000)

    def test_flash_execute_requires_sd_write_gate_before_serial_open(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = pathlib.Path(directory)
            loader = temp / "loadp2"
            invoked = temp / "loadp2-invoked"
            loader.write_text(
                "#!/bin/sh\n"
                "if [ \"${1:-}\" = '-?' ]; then\n"
                "  echo '[ -FLASH ] program application to SPI flash'\n"
                "  exit 0\n"
                "fi\n"
                "touch \"$FAKE_LOADP2_INVOKED\"\n"
                "exit 99\n",
                encoding="utf-8",
            )
            loader.chmod(0o755)
            image = temp / "image.bin"
            image.write_bytes(b"P2!!")
            digest = hashlib.sha256(loader.read_bytes()).hexdigest()
            lock = temp / "toolchain.lock"
            lock.write_text(
                f"sha256={digest}  {loader}\n", encoding="utf-8"
            )
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(temp),
                    "LOADP2": str(loader),
                    "P2_TOOLCHAIN_LOCK": str(lock),
                    "P2_PYTHON": sys.executable,
                    "P2_HIL": "1",
                    "P2_ALLOW_FLASH_WRITE": "1",
                    "P2_ALLOW_SD_WRITE": "0",
                    "FAKE_LOADP2_INVOKED": str(invoked),
                }
            )
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "tools/p2/flash.sh"),
                    "--execute",
                    "--port",
                    "/dev/not-a-real-p2",
                    "--image",
                    str(image),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("P2_ALLOW_SD_WRITE=1 is required", result.stderr)
            self.assertFalse(invoked.exists())


if __name__ == "__main__":
    unittest.main()
