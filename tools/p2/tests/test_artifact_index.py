#!/usr/bin/env python3

import importlib.util
import json
import pathlib
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "artifact_index.py"
SPEC = importlib.util.spec_from_file_location("p2_artifact_index", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
artifact_index = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(artifact_index)


class ArtifactIndexTests(unittest.TestCase):
    def add_run(self, root, name, status):
        directory = root / name
        directory.mkdir()
        (directory / "status.json").write_text(
            json.dumps(status), encoding="utf-8"
        )

    def test_indexes_only_top_level_status_bundles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.add_run(root, "20260713T000000.000000Z-hello", {
                "status": "PASS",
                "cycles_passed": 10,
                "cycles_requested": 10,
                "started_utc": "2026-07-13T00:00:00Z",
                "ended_utc": "2026-07-13T00:00:10Z",
            })
            nested = root / "20260713T000000.000000Z-hello" / "cycle-001"
            nested.mkdir()
            (nested / "status.json").write_text(
                '{"status":"PASS"}', encoding="utf-8"
            )

            index = artifact_index.build_index(
                root, generated_utc="2026-07-13T01:00:00Z"
            )

            self.assertEqual(index["schema"], artifact_index.SCHEMA)
            self.assertEqual(index["run_count"], 1)
            self.assertEqual(index["runs"][0]["kind"], "hello")
            self.assertEqual(index["runs"][0]["cycles"], {
                "passed": 10,
                "requested": 10,
            })
            self.assertEqual(len(index["runs"][0]["status_sha256"]), 64)

    def test_retains_failures_and_selects_latest_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.add_run(root, "20260713T000000.000000Z-nsh", {
                "status": "FAIL",
                "protocol": "nsh",
            })
            self.add_run(root, "20260713T000001.000000Z-nsh", {
                "status": "PASS",
                "protocol": "nsh",
            })

            index = artifact_index.build_index(root, generated_utc="fixed")

            self.assertEqual(index["status_counts"], {"FAIL": 1, "PASS": 1})
            self.assertEqual(index["latest_pass_by_kind"]["nsh"],
                             "20260713T000001.000000Z-nsh")
            markdown = artifact_index.render_markdown(index)
            self.assertIn("**FAIL**", markdown)
            self.assertIn("**PASS**", markdown)

    def test_rejects_malformed_top_level_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.add_run(root, "bad", {"ended_utc": "never"})
            with self.assertRaises(artifact_index.ArtifactError):
                artifact_index.scan_artifacts(root)


if __name__ == "__main__":
    unittest.main()
