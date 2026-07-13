import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

import hil
import storage_plan


def load_wrapper(filename, name):
    path = pathlib.Path(__file__).parents[1] / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StoragePlanTests(unittest.TestCase):
    def environment(self):
        return {
            "P2_HIL": "1",
            "P2_ALLOW_FLASH_WRITE": "1",
            "P2_ALLOW_FLASH_ERASE": "1",
            "P2_ALLOW_SD_DESTRUCTIVE": "1",
        }

    def test_dry_run_and_each_destructive_gate_fail_before_hil(self):
        env = self.environment()
        self.assertIn(
            "DRY-RUN",
            storage_plan.validate_execution_gates(False, ("flash-write",), env),
        )

        disabled = dict(env, P2_HIL="0")
        self.assertIn(
            "P2_HIL",
            storage_plan.validate_execution_gates(True, ("flash-write",), disabled),
        )
        no_write = dict(env, P2_ALLOW_FLASH_WRITE="0")
        self.assertIn(
            "P2_ALLOW_FLASH_WRITE",
            storage_plan.validate_execution_gates(True, ("probe",), no_write),
        )
        no_erase = dict(env, P2_ALLOW_FLASH_ERASE="0")
        self.assertIn(
            "P2_ALLOW_FLASH_ERASE",
            storage_plan.validate_execution_gates(
                True, ("flash-write",), no_erase
            ),
        )
        no_sd = dict(env, P2_ALLOW_SD_DESTRUCTIVE="0")
        self.assertIn(
            "P2_ALLOW_SD_DESTRUCTIVE",
            storage_plan.validate_execution_gates(True, ("sd-write",), no_sd),
        )

    def test_alternate_requires_both_flash_and_sd_destructive_gates(self):
        env = self.environment()
        self.assertIsNone(
            storage_plan.validate_execution_gates(True, ("alternate",), env)
        )
        self.assertIn(
            "P2_ALLOW_FLASH_ERASE",
            storage_plan.validate_execution_gates(
                True,
                ("alternate",),
                dict(env, P2_ALLOW_FLASH_ERASE="0"),
            ),
        )
        self.assertIn(
            "P2_ALLOW_SD_DESTRUCTIVE",
            storage_plan.validate_execution_gates(
                True,
                ("alternate",),
                dict(env, P2_ALLOW_SD_DESTRUCTIVE="0"),
            ),
        )

    def test_plan_uses_one_fresh_load_reset_artifact_per_action(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = pathlib.Path(directory) / "flash-plan"
            with mock.patch.object(
                storage_plan, "stage_boot_crc32", return_value="89ABCDEF"
            ), mock.patch.object(
                storage_plan.hil, "main", return_value=0
            ) as main:
                rc = storage_plan.run_plan(
                    kind="flashfs",
                    actions=("probe", "flash-write", "flash-verify"),
                    sequence="1234ABCD",
                    artifact_dir=str(artifact),
                    image="/tmp/nuttx",
                    port="/dev/fake-p2",
                    no_build=False,
                    timeout=600,
                    execute=True,
                    environment=self.environment(),
                )

            self.assertEqual(rc, hil.EXIT_OK)
            self.assertEqual(main.call_count, 3)
            calls = [call.args[0] for call in main.call_args_list]
            self.assertIn("--build-standalone", calls[0])
            self.assertNotIn("--build-standalone", calls[1])
            self.assertNotIn("--build-standalone", calls[2])
            self.assertEqual(
                [args[args.index("--storage-action") + 1] for args in calls],
                ["probe", "flash-write", "flash-verify"],
            )
            self.assertNotIn("--storage-sequence", calls[0])
            self.assertIn("1234ABCD", calls[1])
            self.assertIn("1234ABCD", calls[2])
            status = json.loads((artifact / "status.json").read_text())
            self.assertEqual(status["status"], "PASS")
            self.assertEqual(status["actions_passed"], 3)
            self.assertFalse(status["automatic_format"])
            self.assertIn("fresh loadp2", status["persistence_boundary"])

    def test_plan_stops_and_records_the_exact_failed_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = pathlib.Path(directory) / "sd-plan"
            with mock.patch.object(
                storage_plan, "stage_boot_crc32", return_value="89ABCDEF"
            ), mock.patch.object(
                storage_plan.hil, "main", side_effect=(0, hil.EXIT_HIL_FAILURE)
            ) as main:
                rc = storage_plan.run_plan(
                    kind="sd",
                    actions=("probe", "sd-write", "sd-verify"),
                    sequence="1234ABCD",
                    artifact_dir=str(artifact),
                    image=None,
                    port=None,
                    no_build=True,
                    timeout=600,
                    execute=True,
                    environment=self.environment(),
                )
            self.assertEqual(rc, hil.EXIT_HIL_FAILURE)
            self.assertEqual(main.call_count, 2)
            status = json.loads((artifact / "status.json").read_text())
            self.assertEqual(status["failure_action"], "sd-write")
            self.assertEqual(status["actions_passed"], 1)

    def test_interrupted_reset_elapsed_uses_preserved_ready_and_boot_times(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            arm = root / "01-flash-interrupt-arm" / "cycle-001"
            verify = root / "02-flash-interrupt-verify" / "cycle-001"
            arm.mkdir(parents=True)
            verify.mkdir(parents=True)
            (arm / "metadata.json").write_text(
                json.dumps({"started_utc": "2026-07-13T12:00:00.000Z"})
            )
            (arm / "markers.json").write_text(
                json.dumps(
                    {
                        "observed_after_start_seconds": {
                            "P2STORAGE:READY:POWER-CUT=FLASH:SEQUENCE=1234ABCE": 2.5
                        }
                    }
                )
            )
            (verify / "metadata.json").write_text(
                json.dumps({"started_utc": "2026-07-13T12:00:04.000Z"})
            )
            (verify / "markers.json").write_text(
                json.dumps(
                    {"observed_after_start_seconds": {"P2BOOT:ENTRY": 0.125}}
                )
            )
            self.assertEqual(
                storage_plan.interrupt_reset_elapsed(arm.parent, verify.parent),
                1.625,
            )

    def test_flash_wrapper_never_formats_without_explicit_flag(self):
        wrapper = load_wrapper("test-flashfs.py", "p2_test_flashfs")
        with mock.patch.object(
            wrapper.storage_plan, "run_plan", return_value=17
        ) as run_plan:
            rc = wrapper.main(["--sequence", "1234ABCD"])
        self.assertEqual(rc, 17)
        self.assertEqual(
            run_plan.call_args.kwargs["actions"],
            [
                "probe",
                "flash-write",
                "flash-verify",
                "flash-cycle",
                "flash-full",
                "flash-interrupt-arm",
                "flash-interrupt-verify",
            ],
        )

        with mock.patch.object(
            wrapper.storage_plan, "run_plan", return_value=19
        ) as run_plan:
            rc = wrapper.main(["--format", "--sequence", "1234ABCD"])
        self.assertEqual(rc, 19)
        self.assertEqual(
            run_plan.call_args.kwargs["actions"],
            [
                "probe",
                "flash-format",
                "flash-write",
                "flash-verify",
                "flash-cycle",
                "flash-full",
                "flash-interrupt-arm",
                "flash-interrupt-verify",
            ],
        )

    def test_sd_wrapper_locks_persistence_delete_and_1000_bus_stage(self):
        wrapper = load_wrapper("test-sd.py", "p2_test_sd")
        with mock.patch.object(
            wrapper.storage_plan, "run_plan", return_value=23
        ) as run_plan:
            rc = wrapper.main(["--format", "--sequence", "1234ABCD"])
        self.assertEqual(rc, 23)
        self.assertEqual(
            run_plan.call_args.kwargs["actions"],
            [
                "probe",
                "sd-format",
                "sd-write",
                "sd-verify",
                "sd-rename-delete",
                "sd-stress",
                "alternate",
            ],
        )


if __name__ == "__main__":
    unittest.main()
