# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

import build_artifact
import flashboot_protocol
import flash_layout


ROOT = pathlib.Path(__file__).resolve().parents[3]


def make_build_artifact(root, image):
    root.mkdir()
    for name in build_artifact.PASS_REQUIRED_FILES:
        path = root / name
        if name == "nuttx.bin":
            path.write_bytes(image)
        elif name == "nuttx":
            path.write_bytes(b"ELF fixture")
        elif name == "config":
            path.write_text("CONFIG_P2_SYSCLK_HZ=180000000\n", encoding="utf-8")
        elif name == "toolchain.lock":
            path.write_text(
                "nuttx_commit={}\nnuttx_apps_commit={}\n".format("1" * 40, "2" * 40),
                encoding="utf-8",
            )
        elif name in ("nuttx-source-status.txt", "apps-source-status.txt"):
            path.write_text("", encoding="utf-8")
        else:
            path.write_text(name + "\n", encoding="utf-8")
    files = {
        path.name: {
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in root.iterdir()
        if path.is_file()
    }
    status = {
        "format": build_artifact.FORMAT,
        "status": "PASS",
        "exit_code": 0,
        "board": "p2-ec32mb",
        "profile": "flashboot",
        "started_utc": "2026-07-13T11:58:00.000Z",
        "ended_utc": "2026-07-13T11:59:00.000Z",
        "nuttx_branch": "codex/test",
        "nuttx_commit": "1" * 40,
        "nuttx_commit_after": "1" * 40,
        "apps_branch": "codex/test",
        "apps_commit": "2" * 40,
        "apps_commit_after": "2" * 40,
        "source_clean": True,
        "nuttx_source_clean": True,
        "apps_source_clean": True,
        "board_clock_hz": 180000000,
        "binary_sha256": files["nuttx.bin"]["sha256"],
        "elf_sha256": files["nuttx"]["sha256"],
        "files": files,
    }
    (root / "status.json").write_text(json.dumps(status), encoding="utf-8")
    return root


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
        self.assertEqual(flash_layout.image_plan(4).erase_end, 0x1000)
        self.assertEqual(flash_layout.image_plan(0x5000).erase_end, 0x10000)
        with self.assertRaisesRegex(ValueError, "four-byte aligned"):
            flash_layout.image_plan(3)

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
                'touch "$FAKE_LOADP2_INVOKED"\n'
                "exit 99\n",
                encoding="utf-8",
            )
            loader.chmod(0o755)
            image = temp / "image.bin"
            image.write_bytes(b"P2!!")
            image.with_suffix(".bin.json").write_text(
                json.dumps(flash_layout.image_manifest(image.read_bytes())),
                encoding="utf-8",
            )
            build = make_build_artifact(temp / "build", image.read_bytes())
            digest = hashlib.sha256(loader.read_bytes()).hexdigest()
            lock = temp / "toolchain.lock"
            lock.write_text(f"sha256={digest}  {loader}\n", encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(temp),
                    "LOADP2": str(loader),
                    "P2_TOOLCHAIN_LOCK": str(lock),
                    "P2_PYTHON": sys.executable,
                    "P2_HIL": "1",
                    "P2_ALLOW_RESET": "1",
                    "P2_ALLOW_FLASH_WRITE": "1",
                    "P2_ALLOW_FLASH_ERASE": "1",
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
                    "--build-artifact",
                    str(build),
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

            env["P2_ALLOW_FLASH_ERASE"] = "0"
            env["P2_ALLOW_SD_WRITE"] = "1"
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "tools/p2/flash.sh"),
                    "--execute",
                    "--port",
                    "/dev/not-a-real-p2",
                    "--image",
                    str(image),
                    "--build-artifact",
                    str(build),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("P2_ALLOW_FLASH_ERASE=1 is required", result.stderr)
            self.assertFalse(invoked.exists())

            env["P2_ALLOW_RESET"] = "0"
            env["P2_ALLOW_FLASH_ERASE"] = "1"
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "tools/p2/flash.sh"),
                    "--execute",
                    "--port",
                    "/dev/not-a-real-p2",
                    "--image",
                    str(image),
                    "--build-artifact",
                    str(build),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("P2_ALLOW_RESET=1 is required", result.stderr)
            self.assertFalse(invoked.exists())

    def test_flash_script_requires_exact_mkflash_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = pathlib.Path(directory)
            loader = temp / "loadp2"
            loader.write_text(
                "#!/bin/sh\n"
                "if [ \"${1:-}\" = '-?' ]; then\n"
                "  echo '[ -FLASH ] program application to SPI flash'\n"
                "fi\n",
                encoding="utf-8",
            )
            loader.chmod(0o755)
            image = temp / "image.bin"
            image.write_bytes(b"P2 manifest input!!!")
            digest = hashlib.sha256(loader.read_bytes()).hexdigest()
            lock = temp / "toolchain.lock"
            lock.write_text(f"sha256={digest}  {loader}\n", encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(temp),
                    "LOADP2": str(loader),
                    "P2_TOOLCHAIN_LOCK": str(lock),
                    "P2_PYTHON": sys.executable,
                }
            )
            command = [
                "bash",
                str(ROOT / "tools/p2/flash.sh"),
                "--port",
                "/dev/not-opened",
                "--image",
                str(image),
                "--build-artifact",
                str(temp / "missing-build"),
            ]
            missing = subprocess.run(
                command, cwd=ROOT, env=env, text=True, capture_output=True, check=False
            )
            self.assertEqual(missing.returncode, 2)
            self.assertIn("cannot read flash input manifest", missing.stderr)

            manifest = flash_layout.image_manifest(image.read_bytes())
            manifest["image_sha256"] = "0" * 64
            image.with_suffix(".bin.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            tampered = subprocess.run(
                command, cwd=ROOT, env=env, text=True, capture_output=True, check=False
            )
            self.assertEqual(tampered.returncode, 2)
            self.assertIn("image_sha256 mismatch", tampered.stderr)

    def test_flash_script_executes_verified_sealed_loader_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = pathlib.Path(directory)
            source = temp / "source"
            source.mkdir()
            home = temp / "home"
            home.mkdir()
            helper_bin = temp / "bin"
            helper_bin.mkdir()
            record = temp / "loader-record.txt"

            loader = source / "loadp2"
            loader.write_text(
                "#!/bin/sh\n"
                "if [ \"${1:-}\" = '-?' ]; then\n"
                "  echo '[ -FLASH ] program application to SPI flash'\n"
                "  exit 0\n"
                "fi\n"
                'printf \'%s\\n\' "$0" "$@" > "$FAKE_LOADP2_RECORD"\n'
                "exit 0\n",
                encoding="utf-8",
            )
            loader.chmod(0o755)
            for name, script in {
                "timeout": '#!/bin/sh\nshift\nexec "$@"\n',
                "flock": "#!/bin/sh\nexit 0\n",
                "lsof": "#!/bin/sh\nexit 1\n",
                "sleep": "#!/bin/sh\nexit 0\n",
            }.items():
                path = helper_bin / name
                path.write_text(script, encoding="utf-8")
                path.chmod(0o755)

            image = source / "image.bin"
            image.write_bytes(b"P2!!")
            image.with_suffix(".bin.json").write_text(
                json.dumps(flash_layout.image_manifest(image.read_bytes())),
                encoding="utf-8",
            )
            build = make_build_artifact(source / "build", image.read_bytes())
            digest = hashlib.sha256(loader.read_bytes()).hexdigest()
            lock = source / "toolchain.lock"
            lock.write_text("sha256={}  {}\n".format(digest, loader), encoding="utf-8")
            output = temp / "flash-program"
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "PATH": str(helper_bin) + os.pathsep + env["PATH"],
                    "LOADP2": str(loader),
                    "P2_TOOLCHAIN_LOCK": str(lock),
                    "P2_PYTHON": sys.executable,
                    "P2_HIL": "1",
                    "P2_ALLOW_RESET": "1",
                    "P2_ALLOW_FLASH_WRITE": "1",
                    "P2_ALLOW_FLASH_ERASE": "1",
                    "P2_ALLOW_SD_WRITE": "1",
                    "P2_FLASH_SETTLE_SECONDS": "3",
                    "P2_LOCK_FILE": str(temp / "board.lock"),
                    "FAKE_LOADP2_RECORD": str(record),
                }
            )
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "tools/p2/flash.sh"),
                    "--execute",
                    "--port",
                    "/dev/null",
                    "--image",
                    str(image),
                    "--build-artifact",
                    str(build),
                    "--artifact-dir",
                    str(output),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            sealed_loader = (output / "inputs/loadp2").resolve()
            invocation = record.read_text(encoding="utf-8").splitlines()
            self.assertEqual(invocation[0], str(sealed_loader))
            self.assertNotEqual(invocation[0], str(loader))
            command = json.loads((output / "command.json").read_text(encoding="utf-8"))
            self.assertEqual(command["argv"][0], str(sealed_loader))
            self.assertEqual(
                hashlib.sha256(sealed_loader.read_bytes()).hexdigest(), digest
            )
            validated = flashboot_protocol.load_program_artifact(output)
            self.assertEqual(validated.port, "/dev/null")


if __name__ == "__main__":
    unittest.main()
