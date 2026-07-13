# SPDX-License-Identifier: Apache-2.0

import importlib.util
import os
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "tools" / "p2" / "compare64_codegen.py"
SPEC = importlib.util.spec_from_file_location("compare64_codegen", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
COMPARE64 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPARE64)


class Compare64CodegenTests(unittest.TestCase):
    def test_high_first_model_matches_boundary_semantics(self):
        self.assertEqual(COMPARE64.verify_boundary_semantics(), 41472)

    def test_p2_codegen_at_all_optimization_levels(self):
        default_root = ROOT.parent / ".p2-nuttx-cache" / "p2llvm" / "install"
        toolchain = pathlib.Path(os.environ.get("P2LLVM_ROOT", str(default_root)))
        self.assertEqual(COMPARE64.compile_and_verify(toolchain), 41472)


if __name__ == "__main__":
    unittest.main()
