# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import os
import pathlib
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest


TOOLS = pathlib.Path(__file__).resolve().parents[1]
ROOT = TOOLS.parents[1]
sys.path.insert(0, str(TOOLS))

import build_artifact  # noqa: E402
import release_bundle  # noqa: E402


def make_build(root: pathlib.Path, board: str, marker: bytes) -> pathlib.Path:
    root.mkdir()
    elf = bytearray(20)
    elf[:7] = b"\x7fELF\x01\x01\x01"
    struct.pack_into("<H", elf, 18, release_bundle.P2_ELF_MACHINE)
    for name in build_artifact.PASS_REQUIRED_FILES:
        path = root / name
        if name == "nuttx":
            path.write_bytes(elf)
        elif name == "nuttx.bin":
            path.write_bytes(marker * 4)
        elif name == "config":
            path.write_text(
                'CONFIG_ARCH="p2"\n' 'CONFIG_ARCH_BOARD="{}"\n'.format(board)
                + "CONFIG_BUILD_FLAT=y\n"
                + "CONFIG_P2_SYSCLK_HZ=180000000\n",
                encoding="utf-8",
            )
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
        "board": board,
        "profile": "showcase",
        "started_utc": "2026-07-13T16:00:00Z",
        "ended_utc": "2026-07-13T16:01:00Z",
        "nuttx_branch": "codex/test",
        "nuttx_commit": "1" * 40,
        "nuttx_commit_after": "1" * 40,
        "apps_branch": "codex/test-apps",
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


def make_loader(path: pathlib.Path) -> pathlib.Path:
    path.write_bytes(
        struct.pack("<II", 0xFEEDFACF, 0x0100000C)
        + b"\x00" * 24
        + b"loadp2 version 0.078\x00"
        + b"program application to SPI flash\x00"
        + b"In -CHIP mode\x00"
        + b"@ADDR=file\x00"
    )
    path.chmod(0o755)
    return path


def rewrite_build_commits(root: pathlib.Path, nuttx: str, apps: str) -> None:
    lock = root / "toolchain.lock"
    lock.write_text(
        "nuttx_commit={}\nnuttx_apps_commit={}\n".format(nuttx, apps),
        encoding="utf-8",
    )
    status_path = root / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status.update(
        {
            "nuttx_commit": nuttx,
            "nuttx_commit_after": nuttx,
            "apps_commit": apps,
            "apps_commit_after": apps,
        }
    )
    status["files"]["toolchain.lock"] = {
        "size": lock.stat().st_size,
        "sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
    }
    status_path.write_text(json.dumps(status), encoding="utf-8")


class ReleaseFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.temp = pathlib.Path(self.temporary.name)
        self.ec32mb = make_build(self.temp / "ec32mb-build", "p2-ec32mb", b"E")
        self.ec_revd = make_build(self.temp / "ec-revd-build", "p2-ec", b"D")
        self.loader = make_loader(self.temp / "loadp2")
        self.license = self.temp / "License.txt"
        self.license.write_text(
            "MIT License\n\nloadp2\n\nSDCARD writer\n",
            encoding="utf-8",
        )
        self.sd_writer = self.temp / "P2ES_sdcard.bin"
        self.sd_writer.write_bytes(b"fixture P2 SD writer")
        self.sd_writer_sha256 = hashlib.sha256(self.sd_writer.read_bytes()).hexdigest()
        self.hil = self.temp / "ec32mb-hil"
        self.hil.mkdir()
        hil_logs = {
            "console.raw": b"P2BOOT:ENTRY\r\nP2SHOWCASE:PASS\r\n",
            "console.normalized.log": b"[fixture] P2SHOWCASE:PASS\n",
            "commands.jsonl": b'{"hex":"03","stage":"Ctrl-C"}\n',
        }
        for name, data in hil_logs.items():
            (self.hil / name).write_bytes(data)
        ec32_status_sha = hashlib.sha256(
            (self.ec32mb / "status.json").read_bytes()
        ).hexdigest()
        ec32_elf_sha = hashlib.sha256((self.ec32mb / "nuttx").read_bytes()).hexdigest()
        ec32_raw_sha = hashlib.sha256(
            (self.ec32mb / "nuttx.bin").read_bytes()
        ).hexdigest()
        (self.hil / "status.json").write_text(
            json.dumps(
                {
                    "format": release_bundle.SHOWCASE_HIL_FORMAT,
                    "status": "PASS",
                    "exit_code": 0,
                    "board": "p2-ec32mb",
                    "profile": "showcase",
                    "smp_enabled": False,
                    "single_serial_owner": True,
                    "serial_processes_started": 1,
                    "intentionally_terminated": True,
                    "storage_actions": ["probe"],
                    "destructive_storage_actions": [],
                    "gates": {
                        "P2_HIL": True,
                        "P2_ALLOW_RESET": True,
                        "P2_ALLOW_LOOPBACK_TESTS": True,
                        "P2_ALLOW_PSRAM_WRITE": True,
                    },
                    "build": {
                        "board": "p2-ec32mb",
                        "profile": "showcase",
                        "source_clean": True,
                        "status_sha256": ec32_status_sha,
                        "elf_sha256": ec32_elf_sha,
                        "binary_sha256": ec32_raw_sha,
                        "raw_binary_sha256": ec32_raw_sha,
                        "nuttx_commit": "1" * 40,
                        "apps_commit": "2" * 40,
                    },
                    "stages": [
                        {"name": name, "status": "PASS"}
                        for name in release_bundle.REQUIRED_SHOWCASE_HIL_STAGES
                    ],
                    "raw_serial_bytes": len(hil_logs["console.raw"]),
                    "raw_serial_sha256": hashlib.sha256(
                        hil_logs["console.raw"]
                    ).hexdigest(),
                    "normalized_serial_sha256": hashlib.sha256(
                        hil_logs["console.normalized.log"]
                    ).hexdigest(),
                    "command_transcript_sha256": hashlib.sha256(
                        hil_logs["commands.jsonl"]
                    ).hexdigest(),
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def package(self, output: pathlib.Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                str(TOOLS / "release_bundle.py"),
                "package",
                "--ec32mb-build-artifact",
                str(self.ec32mb),
                "--ec-revd-build-artifact",
                str(self.ec_revd),
                "--loadp2",
                str(self.loader),
                "--loadp2-license",
                str(self.license),
                "--sd-writer",
                str(self.sd_writer),
                "--sd-writer-sha256",
                self.sd_writer_sha256,
                "--ec32mb-evidence",
                str(self.hil),
                "--ec32mb-hardware-status",
                "HIL-VERIFIED",
                "--output",
                str(output),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )


class ReleaseBundleTests(ReleaseFixture, unittest.TestCase):
    def test_package_rejects_loader_without_explicit_address_filespec(self):
        data = self.loader.read_bytes().replace(b"@ADDR=file", b"@ADDR+file")
        self.loader.write_bytes(data)
        result = self.package(self.temp / "release-plus-only-loader")
        self.assertEqual(result.returncode, 2)
        self.assertIn("explicit-address loading support", result.stderr)

    def test_package_contains_exact_dual_board_install_assets(self):
        output = self.temp / "release"
        result = self.package(output)
        self.assertEqual(result.returncode, 0, result.stderr)
        prefix = release_bundle.DEFAULT_PREFIX
        ec32_stem = prefix + "-p2-ec32mb-revb"
        revd_stem = prefix + "-p2-ec-revd"
        required = {
            "_BOOT_P2.BIX",
            ec32_stem + "-ram.elf",
            ec32_stem + "-flash.bin",
            ec32_stem + "-flash.bin.json",
            ec32_stem + "-_BOOT_P2.BIX",
            ec32_stem + ".config",
            revd_stem + "-ram.elf",
            revd_stem + "-flash.bin",
            revd_stem + "-flash.bin.json",
            revd_stem + "-_BOOT_P2.BIX",
            revd_stem + ".config",
            "loadp2-0.078-macos-arm64",
            "loadp2-LICENSE.txt",
            "P2ES_sdcard.bin",
            "SHA256SUMS.txt",
            "release-manifest.json",
            "install-p2.sh",
            "verify-release.py",
            prefix + "-evidence.tar.gz",
            prefix + "-bundle-macos-arm64.tar.gz",
        }
        self.assertEqual(
            {path.name for path in output.iterdir() if path.is_file()},
            required,
        )
        self.assertEqual(
            (output / "_BOOT_P2.BIX").read_bytes(),
            (output / (ec32_stem + "-flash.bin")).read_bytes(),
        )
        self.assertNotEqual(
            (output / (ec32_stem + "-flash.bin")).read_bytes(),
            (output / (revd_stem + "-flash.bin")).read_bytes(),
        )
        manifest = json.loads(
            (output / "release-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["default_sd_boot_board"], "p2-ec32mb")
        self.assertEqual(
            manifest["boards"]["p2-ec32mb"]["hardware_status"],
            "HIL-VERIFIED",
        )
        self.assertEqual(
            manifest["boards"]["p2-ec"]["hardware_status"],
            "HIL-REQUIRED",
        )
        verified = subprocess.run(
            [sys.executable, str(output / "verify-release.py"), "verify", str(output)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)

        bundle = output / (prefix + "-bundle-macos-arm64.tar.gz")
        with tarfile.open(bundle, "r:gz") as archive:
            names = set(archive.getnames())
        self.assertIn(prefix + "/boards/p2-ec32mb-revb/_BOOT_P2.BIX", names)
        self.assertIn(prefix + "/boards/p2-ec-revd/_BOOT_P2.BIX", names)

    def test_extracted_bundle_verifies_and_is_reproducible(self):
        first = self.temp / "release-one"
        second = self.temp / "release-two"
        self.assertEqual(self.package(first).returncode, 0)
        self.assertEqual(self.package(second).returncode, 0)
        prefix = release_bundle.DEFAULT_PREFIX
        for suffix in ("-evidence.tar.gz", "-bundle-macos-arm64.tar.gz"):
            self.assertEqual(
                hashlib.sha256((first / (prefix + suffix)).read_bytes()).hexdigest(),
                hashlib.sha256((second / (prefix + suffix)).read_bytes()).hexdigest(),
            )
        with tarfile.open(
            first / (prefix + "-bundle-macos-arm64.tar.gz"), "r:gz"
        ) as archive:
            archive.extractall(self.temp / "extracted", filter="data")
        extracted = self.temp / "extracted" / prefix
        verified = subprocess.run(
            [
                sys.executable,
                str(extracted / "verify-release.py"),
                "verify",
                str(extracted),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_tamper_is_rejected(self):
        output = self.temp / "release"
        self.assertEqual(self.package(output).returncode, 0)
        (output / "_BOOT_P2.BIX").write_bytes(b"tampered")
        result = subprocess.run(
            [sys.executable, str(output / "verify-release.py"), "verify", str(output)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("SHA-256 mismatch", result.stderr)

    def test_hil_claim_requires_ec32mb_evidence(self):
        output = self.temp / "release"
        command = [
            sys.executable,
            str(TOOLS / "release_bundle.py"),
            "package",
            "--ec32mb-build-artifact",
            str(self.ec32mb),
            "--ec-revd-build-artifact",
            str(self.ec_revd),
            "--loadp2",
            str(self.loader),
            "--loadp2-license",
            str(self.license),
            "--sd-writer",
            str(self.sd_writer),
            "--sd-writer-sha256",
            self.sd_writer_sha256,
            "--ec32mb-hardware-status",
            "HIL-VERIFIED",
            "--output",
            str(output),
        ]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires hardware evidence", result.stderr)
        self.assertFalse(output.exists())

    def test_hil_claim_rejects_arbitrary_pass_json_with_image_digest(self):
        (self.hil / "status.json").write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "image_sha256": hashlib.sha256(
                        (self.ec32mb / "nuttx.bin").read_bytes()
                    ).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        result = self.package(self.temp / "release-arbitrary-hil")
        self.assertEqual(result.returncode, 2)
        self.assertIn(release_bundle.SHOWCASE_HIL_FORMAT, result.stderr)

    def test_hil_claim_requires_every_stage_and_untampered_logs(self):
        status_path = self.hil / "status.json"
        original = json.loads(status_path.read_text(encoding="utf-8"))
        without_edge = dict(original)
        without_edge["stages"] = [
            stage for stage in original["stages"] if stage["name"] != "p2smartpins edge"
        ]
        status_path.write_text(json.dumps(without_edge), encoding="utf-8")
        missing = self.package(self.temp / "release-missing-stage")
        self.assertEqual(missing.returncode, 2)
        self.assertIn("required showcase HIL stage", missing.stderr)

        without_psram = json.loads(json.dumps(original))
        without_psram["gates"]["P2_ALLOW_PSRAM_WRITE"] = False
        without_psram["stages"] = [
            stage
            for stage in without_psram["stages"]
            if stage["name"] != "optional p2psram volatile write/read proof"
        ]
        status_path.write_text(json.dumps(without_psram), encoding="utf-8")
        no_psram = self.package(self.temp / "release-missing-psram")
        self.assertEqual(no_psram.returncode, 2)
        self.assertIn("safety gates are incomplete", no_psram.stderr)

        status_path.write_text(json.dumps(original), encoding="utf-8")
        (self.hil / "console.raw").write_bytes(b"tampered console")
        tampered = self.package(self.temp / "release-tampered-hil-log")
        self.assertEqual(tampered.returncode, 2)
        self.assertIn("console.raw SHA-256 mismatch", tampered.stderr)

    def test_dual_board_build_commits_must_match(self):
        original_status = (self.ec_revd / "status.json").read_bytes()
        original_lock = (self.ec_revd / "toolchain.lock").read_bytes()
        for label, nuttx, apps in (
            ("nuttx", "3" * 40, "2" * 40),
            ("apps", "1" * 40, "4" * 40),
        ):
            with self.subTest(label=label):
                rewrite_build_commits(self.ec_revd, nuttx, apps)
                result = self.package(self.temp / ("release-" + label))
                self.assertEqual(result.returncode, 2)
                self.assertIn("commit", result.stderr.lower())
                (self.ec_revd / "status.json").write_bytes(original_status)
                (self.ec_revd / "toolchain.lock").write_bytes(original_lock)

    def test_verifier_rejects_manifest_commit_divergence(self):
        output = self.temp / "release-verify-commit"
        self.assertEqual(self.package(output).returncode, 0)
        manifest_path = output / "release-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["boards"]["p2-ec"]["apps_commit"] = "4" * 40
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        release_bundle._write_checksums(output)
        verified = subprocess.run(
            [sys.executable, str(output / "verify-release.py"), "verify", str(output)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(verified.returncode, 2)
        self.assertIn("apps commits do not match", verified.stderr)


class ReleaseInstallerTests(ReleaseFixture, unittest.TestCase):
    def test_installer_requires_explicit_board_and_defaults_to_dry_run(self):
        output = self.temp / "release"
        self.assertEqual(self.package(output).returncode, 0)
        installer = output / "install-p2.sh"
        missing = subprocess.run(
            [str(installer), "ram", "--port", "/dev/not-p2"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(missing.returncode, 2)
        self.assertIn("--board must be", missing.stderr)

        for board, slug in (
            ("p2-ec32mb", "p2-ec32mb-revb"),
            ("p2-ec", "p2-ec-revd"),
        ):
            with self.subTest(board=board):
                result = subprocess.run(
                    [str(installer), "ram", "--board", board, "--port", "/dev/not-p2"],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("selected_board=" + board, result.stdout)
                self.assertIn(slug + "-ram.elf", result.stdout)
                self.assertRegex(result.stdout, r"-ram\.elf -t ")
                self.assertIn("DRY-RUN", result.stdout)
                sd = subprocess.run(
                    [str(installer), "sd", "--board", board, "--port", "/dev/not-p2"],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(sd.returncode, 0, sd.stderr)
                self.assertIn("P2ES_sdcard.bin", sd.stdout)
                self.assertIn(slug + "-_BOOT_P2.BIX", sd.stdout)
                self.assertIn("-CHIP", sd.stdout)
                self.assertIn("@8000=", sd.stdout)
                self.assertNotIn("@8000+", sd.stdout)
                self.assertIn(
                    "staged_payload_format=le32-image-size+image+zero-pad-to-4",
                    sd.stdout,
                )
                self.assertIn("BOOT-UNVERIFIED", sd.stdout)

    def test_sd_installer_stages_exact_length_prefixed_image_and_cleans_it(self):
        output = self.temp / "release"
        self.assertEqual(self.package(output).returncode, 0)
        python_wrapper = self.temp / "staging-python-wrapper"
        python_wrapper.write_text(
            "#!/bin/sh\n"
            'if [ "$2" = run ]; then\n'
            "  for last do :; done\n"
            '  staged=${{last#*,@8000=}}\n'
            '  if [ "$staged" = "$last" ]; then\n'
            "    echo 'ERROR: missing @8000= staged payload'\n"
            "    exit 3\n"
            "  fi\n"
            '  "{}" - "$staged" <<\'PY\'\n'
            "import pathlib\n"
            "import sys\n"
            'print("STAGED_HEX=" + pathlib.Path(sys.argv[1]).read_bytes().hex())\n'
            "PY\n"
            "  exit 0\n"
            "fi\n"
            'exec "{}" "$@"\n'.format(sys.executable, sys.executable),
            encoding="utf-8",
        )
        python_wrapper.chmod(0o755)
        fake_bin = self.temp / "staging-fake-bin"
        fake_bin.mkdir()
        fake_lsof = fake_bin / "lsof"
        fake_lsof.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        fake_lsof.chmod(0o755)
        stage_tmp = self.temp / "stage-tmp"
        stage_tmp.mkdir()
        env = os.environ.copy()
        env.update(
            {
                "PATH": str(fake_bin) + os.pathsep + env["PATH"],
                "TMPDIR": str(stage_tmp),
                "P2_PYTHON": str(python_wrapper),
                "P2_HIL": "1",
                "P2_ALLOW_RESET": "1",
                "P2_ALLOW_SD_WRITE": "1",
                "P2_ALLOW_SD_DESTRUCTIVE": "1",
                "P2_ALLOW_TEST_HOST": "1",
                "P2_LOCK_DIR": str(self.temp / "sd-stage.lock"),
            }
        )
        result = subprocess.run(
            [
                str(output / "install-p2.sh"),
                "sd",
                "--board",
                "p2-ec32mb",
                "--port",
                "/dev/null",
                "--execute",
            ],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("STAGED_HEX=0400000045454545", result.stdout)
        self.assertIn("PASS: writer recreated", result.stdout)
        self.assertEqual(list(stage_tmp.iterdir()), [])

    def test_flash_execute_requires_shared_sd_gate_before_device_open(self):
        output = self.temp / "release"
        self.assertEqual(self.package(output).returncode, 0)
        env = os.environ.copy()
        env.update(
            {
                "P2_HIL": "1",
                "P2_ALLOW_RESET": "1",
                "P2_ALLOW_FLASH_WRITE": "1",
                "P2_ALLOW_FLASH_ERASE": "1",
                "P2_ALLOW_SD_WRITE": "0",
            }
        )
        result = subprocess.run(
            [
                str(output / "install-p2.sh"),
                "flash",
                "--board",
                "p2-ec32mb",
                "--port",
                "/dev/not-p2",
                "--execute",
            ],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("P2_ALLOW_SD_WRITE=1 is required", result.stderr)

    def test_sd_execute_rejects_error_text_when_runner_exits_zero(self):
        output = self.temp / "release"
        self.assertEqual(self.package(output).returncode, 0)
        python_wrapper = self.temp / "python-wrapper"
        python_wrapper.write_text(
            "#!/bin/sh\n"
            'if [ "$2" = run ]; then\n'
            "  echo 'SD Updater'\n"
            "  echo 'ERROR: fixture receive script failed'\n"
            "  exit 0\n"
            "fi\n"
            'exec "{}" "$@"\n'.format(sys.executable),
            encoding="utf-8",
        )
        python_wrapper.chmod(0o755)
        fake_bin = self.temp / "fake-bin"
        fake_bin.mkdir()
        fake_lsof = fake_bin / "lsof"
        fake_lsof.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        fake_lsof.chmod(0o755)
        env = os.environ.copy()
        env.update(
            {
                "PATH": str(fake_bin) + os.pathsep + env["PATH"],
                "P2_PYTHON": str(python_wrapper),
                "P2_HIL": "1",
                "P2_ALLOW_RESET": "1",
                "P2_ALLOW_SD_WRITE": "1",
                "P2_ALLOW_SD_DESTRUCTIVE": "1",
                "P2_ALLOW_TEST_HOST": "1",
                "P2_LOCK_DIR": str(self.temp / "sd-error.lock"),
            }
        )
        result = subprocess.run(
            [
                str(output / "install-p2.sh"),
                "sd",
                "--board",
                "p2-ec32mb",
                "--port",
                "/dev/null",
                "--execute",
            ],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("ERROR: fixture receive script failed", result.stdout)
        self.assertIn("refusing PASS even if loadp2 exited zero", result.stderr)
        self.assertNotIn("PASS: writer recreated", result.stdout)


if __name__ == "__main__":
    unittest.main()
