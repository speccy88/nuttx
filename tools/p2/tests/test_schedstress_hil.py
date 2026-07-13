#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/p2"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import hil
import test_hil as helpers
from test_schedstress_protocol import complete_log


class SchedulerStressHilTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = pathlib.Path(self.temp.name)
        self.repo = self.directory / "nuttx"
        self.apps = self.directory / "apps"
        self.repo.mkdir()

        self.loadp2 = self.directory / "loadp2"
        self.loadp2.write_bytes(b"fake pinned loader\n")
        self.loadp2.chmod(0o755)
        self.image = self.repo / "nuttx"
        self.image.write_bytes(b"\x7fELF" + b"schedstress" * 20)
        self.lock = self.directory / "toolchain.lock"
        load_sha = hashlib.sha256(self.loadp2.read_bytes()).hexdigest()
        self.lock.write_text(
            "sha256={}  {}\n".format(load_sha, self.loadp2),
            encoding="utf-8",
        )
        self.clock = helpers.ManualClock()

        real_profile = (
            ROOT
            / "boards/p2/p2x8c4m64p/p2-ec32mb/configs/schedstress/defconfig"
        ).read_text(encoding="utf-8")
        profile = (
            self.repo
            / "boards/p2/p2x8c4m64p/p2-ec32mb/configs/schedstress/defconfig"
        )
        profile.parent.mkdir(parents=True)
        profile.write_text(real_profile, encoding="utf-8")
        self.profile = profile
        (self.repo / ".config").write_text(real_profile, encoding="utf-8")
        (self.repo / "System.map").write_text(
            "00000000 T p2schedstress_main\n", encoding="utf-8"
        )

        tools = self.repo / "tools/p2"
        tools.mkdir(parents=True)
        for name in ("hil.py", "test-schedstress.py", "schedstress_protocol.py"):
            (tools / name).write_bytes((ROOT / "tools/p2" / name).read_bytes())

        app = self.apps / "testing/p2schedstress"
        app.mkdir(parents=True)
        real_app = ROOT.parent / "apps/testing/p2schedstress"
        for name in (
            "CMakeLists.txt",
            "Kconfig",
            "Make.defs",
            "Makefile",
            "p2schedstress_main.c",
        ):
            (app / name).write_bytes((real_app / name).read_bytes())

    def tearDown(self):
        self.temp.cleanup()

    def env(self):
        return {
            "P2_HIL": "1",
            "P2_PORT": "/dev/fake-p2",
            "P2_RESET_METHOD": "loadp2",
            "P2_LOADER_BAUD": "2000000",
            "P2_CONSOLE_BAUD": "230400",
            "P2_LOCK_FILE": str(self.directory / "board.lock"),
            "P2_TOOLCHAIN_LOCK": str(self.lock),
            "LOADP2": str(self.loadp2),
        }

    def invoke(self, name, output):
        session = helpers.FakeSession(
            self.clock, [output.encode("ascii")]
        )
        factory = helpers.SessionFactory([session])
        board_lock = helpers.RecordingLock()
        artifact = self.directory / name
        argv = [
            "--execute",
            "--protocol",
            "schedstress",
            "--image",
            str(self.image),
            "--artifact-dir",
            str(artifact),
            "--timeout",
            "600",
        ]
        with mock.patch.object(hil, "REPO_ROOT", self.repo), mock.patch.object(
            hil, "SCHEDSTRESS_PROFILE_PATH", self.profile
        ):
            rc = hil.main(
                argv,
                env=self.env(),
                process_factory=factory,
                monotonic=self.clock.monotonic,
                utc_now=self.clock.utc_now,
                lock_factory=board_lock.factory,
                owner_probe=lambda port: (),
                port_validator=lambda port: port == "/dev/fake-p2",
            )
        return rc, artifact, session, factory

    def test_wrapper_locks_one_cycle_ten_minute_timeout_and_build(self):
        wrapper_path = ROOT / "tools/p2/test-schedstress.py"
        spec = importlib.util.spec_from_file_location(
            "p2_test_schedstress", wrapper_path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        wrapper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wrapper)

        with mock.patch.object(wrapper.hil, "main", return_value=47) as main:
            rc = wrapper.main(
                ["--execute", "--cycles", "99", "--timeout", "1"]
            )

        self.assertEqual(rc, 47)
        self.assertEqual(
            main.call_args.args[0],
            [
                "--execute",
                "--protocol",
                "schedstress",
                "--cycles",
                "1",
                "--timeout",
                "600",
                "--build-standalone",
            ],
        )

    def test_build_runner_selects_exact_schedstress_profile(self):
        result = mock.Mock(returncode=0)
        with mock.patch.object(hil.subprocess, "run", return_value=result) as run:
            rc = hil.default_build_runner("schedstress")

        self.assertEqual(rc, 0)
        self.assertEqual(
            run.call_args.args[0],
            [str(ROOT / "tools/p2/build.sh"), "schedstress"],
        )

    def test_config_validation_rejects_profile_or_flat_up_drift(self):
        values = hil.read_kconfig(self.repo / ".config")
        with mock.patch.object(hil, "SCHEDSTRESS_PROFILE_PATH", self.profile):
            hil.validate_schedstress_config(values)
            for name, changed in (
                ("CONFIG_RAM_SIZE", "1048576"),
                ("CONFIG_RR_INTERVAL", "20"),
                ("CONFIG_TESTING_P2SCHEDSTRESS_PRIORITY", "119"),
                ("CONFIG_SMP", "y"),
                ("CONFIG_PRIORITY_INHERITANCE", "n"),
            ):
                with self.subTest(name=name):
                    drifted = dict(values)
                    drifted[name] = changed
                    with self.assertRaises(hil.SafetyError):
                        hil.validate_schedstress_config(drifted)

    def test_full_hil_path_seals_inputs_and_writes_strict_artifacts(self):
        rc, artifact, session, factory = self.invoke(
            "schedstress-pass", complete_log()
        )

        self.assertEqual(rc, hil.EXIT_OK)
        self.assertEqual(session.writes, [])
        self.assertNotIn("-e", factory.commands[0])
        self.assertTrue(session.terminated)
        status = json.loads((artifact / "status.json").read_text())
        self.assertEqual(status["status"], "PASS")
        self.assertEqual(status["cycles_passed"], 1)
        self.assertEqual(status["scheduler_event_total"], 1_004_078)
        self.assertEqual(status["heap_concurrency_allocations"], 512)
        self.assertFalse(
            status["heap_concurrency_counted_in_scheduler_total"]
        )
        config_sha = hashlib.sha256(
            (self.repo / ".config").read_bytes()
        ).hexdigest()
        self.assertEqual(status["schedstress_config_sha256"], config_sha)

        preserved = set(status["preserved_inputs"])
        for name in (
            ".config",
            "defconfig",
            "p2schedstress_main.c",
            "schedstress_protocol.py",
            "test-schedstress.py",
        ):
            self.assertIn("inputs/" + name, preserved)
        self.assertEqual(
            status["preserved_input_sha256"]["inputs/.config"], config_sha
        )

        markers = json.loads(
            (artifact / "cycle-001/markers.json").read_text()
        )
        protocol = markers["schedstress_protocol"]
        self.assertTrue(protocol["complete"])
        self.assertEqual(protocol["values"]["total_events"], 1_004_078)
        self.assertEqual(
            protocol["values"]["heap_concurrency_count"], 512
        )
        cycle_status = json.loads(
            (artifact / "cycle-001/status.json").read_text()
        )
        self.assertEqual(cycle_status["status"], "PASS")
        self.assertTrue(cycle_status["intentionally_terminated"])

    def test_wrong_count_failure_marker_and_missing_concurrency_fail(self):
        cases = (
            (
                "wrong-total",
                complete_log().replace(
                    "P2SCHED:TOTAL:PASS:COUNT=1004078",
                    "P2SCHED:TOTAL:PASS:COUNT=1004077",
                ),
            ),
            (
                "target-fail",
                complete_log().replace(
                    "P2SCHED:TOTAL:PASS:COUNT=1004078",
                    "P2SCHED:FAIL:TOTAL:CODE=-5\r\n"
                    "P2SCHED:TOTAL:PASS:COUNT=1004078",
                ),
            ),
            (
                "missing-concurrency",
                complete_log().replace(
                    "P2SCHED:HEAP_CONCURRENCY:PASS:COUNT=512\r\n", ""
                ),
            ),
        )
        for name, output in cases:
            with self.subTest(name=name):
                rc, artifact, _session, _factory = self.invoke(name, output)
                self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
                status = json.loads(
                    (artifact / "cycle-001/status.json").read_text()
                )
                self.assertEqual(status["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
