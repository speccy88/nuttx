# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools/p2/write-sd-boot.sh"


class SdBootWriterTests(unittest.TestCase):
    def fixture(self, root):
        loader = root / "loadp2"
        loader.write_text(
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = '-?' ]; then\n"
            "  echo 'In -CHIP mode, filespec may contain multiple files'\n"
            "  echo '@ADDR=file1,@ADDR+file2'\n"
            "  exit 0\n"
            "fi\n"
            "touch \"$FAKE_LOADP2_INVOKED\"\n"
            "printf '%s\\n' \"$@\" > \"$FAKE_LOADP2_ARGS\"\n"
            "exit \"${FAKE_LOADP2_RESULT:-0}\"\n",
            encoding="utf-8",
        )
        loader.chmod(0o755)
        writer = root / "P2ES_sdcard.bin"
        writer.write_bytes(b"writer fixture")
        image = root / "_BOOT_P2.BIX"
        image.write_bytes(b"P2 raw image")
        return loader, writer, image

    def run_script(self, root, extra=(), environment=None):
        loader, writer, image = self.fixture(root)
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(root),
                "P2_HIL_ENV_FILE": "/dev/null",
                "FAKE_LOADP2_INVOKED": str(root / "invoked"),
                "FAKE_LOADP2_ARGS": str(root / "args"),
            }
        )
        if environment:
            env.update(environment)
        command = [
            "bash",
            str(SCRIPT),
            "--port",
            "/dev/not-a-real-p2",
            "--image",
            str(image),
            "--writer",
            str(writer),
            "--writer-sha256",
            hashlib.sha256(writer.read_bytes()).hexdigest(),
            "--loadp2",
            str(loader),
            "--loadp2-sha256",
            hashlib.sha256(loader.read_bytes()).hexdigest(),
            *extra,
        ]
        return subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_dry_run_builds_exact_size_prefixed_chip_command_without_opening_serial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            result = self.run_script(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("DRY-RUN", result.stdout)
            self.assertIn("BOOT-UNVERIFIED", result.stdout)
            self.assertIn("-CHIP", result.stdout)
            self.assertIn("-PATCH", result.stdout)
            self.assertIn("-ZERO", result.stdout)
            self.assertIn("@8000+", result.stdout)
            self.assertIn("_BOOT_P2.BIX...OK", result.stdout)
            self.assertFalse((root / "invoked").exists())

    def test_execute_requires_every_destructive_gate_before_serial_validation(self):
        gates = {
            "P2_HIL": "1",
            "P2_ALLOW_RESET": "1",
            "P2_ALLOW_SD_WRITE": "1",
            "P2_ALLOW_SD_DESTRUCTIVE": "1",
        }
        expected = (
            ("P2_HIL", "P2_HIL=1"),
            ("P2_ALLOW_RESET", "P2_ALLOW_RESET=1"),
            ("P2_ALLOW_SD_WRITE", "P2_ALLOW_SD_WRITE=1"),
            ("P2_ALLOW_SD_DESTRUCTIVE", "P2_ALLOW_SD_DESTRUCTIVE=1"),
        )
        for disabled, message in expected:
            with self.subTest(disabled=disabled), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                environment = dict(gates, **{disabled: "0"})
                result = self.run_script(root, ("--execute",), environment)
                self.assertEqual(result.returncode, 2)
                self.assertIn(message, result.stderr)
                self.assertFalse((root / "invoked").exists())

    def test_rejects_elf_and_writer_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            loader, writer, image = self.fixture(root)
            image.write_bytes(b"\x7fELFfixture")
            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--port",
                    "/dev/fake",
                    "--image",
                    str(image),
                    "--writer",
                    str(writer),
                    "--writer-sha256",
                    hashlib.sha256(writer.read_bytes()).hexdigest(),
                    "--loadp2",
                    str(loader),
                    "--loadp2-sha256",
                    hashlib.sha256(loader.read_bytes()).hexdigest(),
                ],
                cwd=ROOT,
                env=dict(
                    os.environ,
                    HOME=str(root),
                    P2_HIL_ENV_FILE="/dev/null",
                ),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("raw P2 binary, not ELF", result.stderr)

            image.write_bytes(b"raw image")
            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--port",
                    "/dev/fake",
                    "--image",
                    str(image),
                    "--writer",
                    str(writer),
                    "--writer-sha256",
                    "0" * 64,
                    "--loadp2",
                    str(loader),
                    "--loadp2-sha256",
                    hashlib.sha256(loader.read_bytes()).hexdigest(),
                ],
                cwd=ROOT,
                env=dict(
                    os.environ,
                    HOME=str(root),
                    P2_HIL_ENV_FILE="/dev/null",
                ),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("writer SHA-256", result.stderr)

    def test_execute_records_write_pass_but_not_boot_or_contiguity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            fake_lsof = root / "lsof"
            fake_lsof.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_lsof.chmod(0o755)
            artifact = root / "artifact"
            gates = {
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "P2_HIL": "1",
                "P2_ALLOW_RESET": "1",
                "P2_ALLOW_SD_WRITE": "1",
                "P2_ALLOW_SD_DESTRUCTIVE": "1",
            }
            result = self.run_script(
                root,
                (
                    "--execute",
                    "--port",
                    "/dev/null",
                    "--artifact-dir",
                    str(artifact),
                ),
                gates,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("SD-WRITE-PASS", result.stdout)
            self.assertIn("BOOT-UNVERIFIED", result.stdout)
            status = json.loads((artifact / "status.json").read_text())
            self.assertEqual(status["status"], "PASS")
            self.assertEqual(status["boot_status"], "UNVERIFIED")
            self.assertFalse(status["fragmentation_verified"])
            arguments = (root / "args").read_text().splitlines()
            self.assertIn("-CHIP", arguments)
            self.assertIn("-PATCH", arguments)
            self.assertTrue(any(value.startswith("@0=") for value in arguments))

    def test_receive_script_error_is_a_write_failure_even_if_loadp2_exits_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            fake_lsof = root / "lsof"
            fake_lsof.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_lsof.chmod(0o755)
            artifact = root / "artifact"
            gates = {
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "P2_HIL": "1",
                "P2_ALLOW_RESET": "1",
                "P2_ALLOW_SD_WRITE": "1",
                "P2_ALLOW_SD_DESTRUCTIVE": "1",
                "FAKE_LOADP2_RESULT": "0",
            }
            loader, writer, image = self.fixture(root)
            loader.write_text(
                loader.read_text(encoding="utf-8").replace(
                    'touch "$FAKE_LOADP2_INVOKED"',
                    'echo "ERROR: timeout waiting for string"\n'
                    'touch "$FAKE_LOADP2_INVOKED"',
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--port",
                    "/dev/null",
                    "--image",
                    str(image),
                    "--writer",
                    str(writer),
                    "--writer-sha256",
                    hashlib.sha256(writer.read_bytes()).hexdigest(),
                    "--loadp2",
                    str(loader),
                    "--loadp2-sha256",
                    hashlib.sha256(loader.read_bytes()).hexdigest(),
                    "--artifact-dir",
                    str(artifact),
                    "--execute",
                ],
                cwd=ROOT,
                env=dict(
                    os.environ,
                    HOME=str(root),
                    P2_HIL_ENV_FILE="/dev/null",
                    FAKE_LOADP2_INVOKED=str(root / "invoked"),
                    FAKE_LOADP2_ARGS=str(root / "args"),
                    **gates,
                ),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("receive script reported an error", result.stderr)
            status = json.loads((artifact / "status.json").read_text())
            self.assertEqual(status["status"], "FAIL")
            self.assertEqual(status["boot_status"], "UNVERIFIED")

    def test_tee_failure_cannot_be_recorded_as_a_successful_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            fake_lsof = root / "lsof"
            fake_lsof.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_lsof.chmod(0o755)
            fake_tee = root / "tee"
            fake_tee.write_text(
                "#!/bin/sh\ncat >/dev/null\nexit 1\n", encoding="utf-8"
            )
            fake_tee.chmod(0o755)
            artifact = root / "artifact"
            gates = {
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "P2_HIL": "1",
                "P2_ALLOW_RESET": "1",
                "P2_ALLOW_SD_WRITE": "1",
                "P2_ALLOW_SD_DESTRUCTIVE": "1",
            }
            result = self.run_script(
                root,
                (
                    "--execute",
                    "--port",
                    "/dev/null",
                    "--artifact-dir",
                    str(artifact),
                ),
                gates,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("could not preserve loadp2 output", result.stderr)
            status = json.loads((artifact / "status.json").read_text())
            self.assertEqual(status["status"], "FAIL")
            self.assertEqual(status["exit_code"], 2)

    def test_enforces_the_writer_staging_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            loader, writer, image = self.fixture(root)
            image.write_bytes(b"x" * (0x80000 - 0x8004 + 1))
            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "--port",
                    "/dev/fake",
                    "--image",
                    str(image),
                    "--writer",
                    str(writer),
                    "--writer-sha256",
                    hashlib.sha256(writer.read_bytes()).hexdigest(),
                    "--loadp2",
                    str(loader),
                    "--loadp2-sha256",
                    hashlib.sha256(loader.read_bytes()).hexdigest(),
                ],
                cwd=ROOT,
                env=dict(
                    os.environ,
                    HOME=str(root),
                    P2_HIL_ENV_FILE="/dev/null",
                ),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("writer limit is 491516 bytes", result.stderr)


if __name__ == "__main__":
    unittest.main()
