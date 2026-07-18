#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0

import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "tools" / "p2" / "check-unified-memory-codegen.py"
SPEC = importlib.util.spec_from_file_location(
    "check_unified_memory_codegen", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
CODEGEN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CODEGEN)


def assembly_fixture(overrides=None):
    overrides = overrides or {}
    bodies = {}
    for function, helper in CODEGEN.EXPECTED_FUNCTIONS.items():
        bodies[function] = f"        calla #\\{helper}\n        reta\n"
    for function in CODEGEN.HUB_FUNCTIONS:
        bodies[function] = "        rdlong r0, ptra\n        reta\n"
    bodies.update(overrides)
    return "".join(
        f"{function}:\n{body}.size {function}, .-{function}\n"
        for function, body in bodies.items()
    )


class UnifiedMemoryCodegenTests(unittest.TestCase):
    def test_verifier_accepts_helper_lowering_and_native_hub_accesses(self):
        self.assertEqual(
            CODEGEN.verify_enabled_assembly(assembly_fixture(), "fixture"),
            len(CODEGEN.EXPECTED_FUNCTIONS) + len(CODEGEN.HUB_FUNCTIONS),
        )

    def test_verifier_rejects_native_dynamic_access(self):
        broken = assembly_fixture(
            {"p2_probe_dynamic_load32": "        rdlong r0, r0\n        reta\n"}
        )
        with self.assertRaisesRegex(CODEGEN.CodegenError, "helper mismatch"):
            CODEGEN.verify_enabled_assembly(broken, "fixture")

    def test_verifier_rejects_helper_for_proven_hub_object(self):
        broken = assembly_fixture(
            {
                "p2_probe_hub_global_load":
                    "        calla #\\__p2_xmem_load32\n        reta\n"
            }
        )
        with self.assertRaisesRegex(CODEGEN.CodegenError, "incorrectly calls"):
            CODEGEN.verify_enabled_assembly(broken, "fixture")

    def test_default_disabled_verifier_rejects_helper_reference(self):
        CODEGEN.verify_disabled_assembly("rdlong r0, r0", "fixture")
        with self.assertRaisesRegex(CODEGEN.CodegenError, "default-disabled"):
            CODEGEN.verify_disabled_assembly(
                "calla #\\__p2_xmem_load32", "fixture"
            )

    def test_provenance_verifier_requires_helpers_for_every_escape(self):
        def provenance_fixture(native_function=None):
            return "".join(
                f"{function}:\n"
                + (
                    "        rdbyte r0, r0\n"
                    if function == native_function
                    else f"        calla #\\{helper}\n"
                )
                + "        reta\n"
                + f".size {function}, .-{function}\n"
                for function, helper in CODEGEN.PROVENANCE_FUNCTIONS.items()
            )

        assembly = provenance_fixture()
        self.assertEqual(
            CODEGEN.verify_provenance_assembly(assembly),
            len(CODEGEN.PROVENANCE_FUNCTIONS),
        )

        for function in CODEGEN.PROVENANCE_FUNCTIONS:
            with self.subTest(function=function):
                with self.assertRaisesRegex(
                    CODEGEN.CodegenError, f"provenance escape in {function}"
                ):
                    CODEGEN.verify_provenance_assembly(
                        provenance_fixture(native_function=function)
                    )

    def test_negative_probe_diagnostics_must_be_deliberate(self):
        CODEGEN.verify_rejection_diagnostic(
            "P2 unified memory rejects dynamic atomicrmw", "atomic operation"
        )
        CODEGEN.verify_rejection_diagnostic(
            "P2 unified memory rejects cmpxchg", "compare exchange"
        )
        CODEGEN.verify_rejection_diagnostic(
            "P2 unified memory rejects an atomic load", "atomic load"
        )
        CODEGEN.verify_rejection_diagnostic(
            "P2 unified memory rejects an atomic store", "atomic store"
        )
        CODEGEN.verify_rejection_diagnostic(
            "P2 unified memory rejects an inline assembly pointer", "inline asm"
        )
        with self.assertRaisesRegex(
            CODEGEN.CodegenError, "without an explicit"
        ):
            CODEGEN.verify_rejection_diagnostic(
                "fatal error: cannot select instruction", "atomicrmw"
            )

    def test_compile_command_uses_explicit_llvm_switch_only_when_enabled(self):
        enabled = CODEGEN.compiler_command(
            pathlib.Path("clang"),
            pathlib.Path("probe.c"),
            pathlib.Path("probe.s"),
            "O2",
            True,
        )
        disabled = CODEGEN.compiler_command(
            pathlib.Path("clang"),
            pathlib.Path("probe.c"),
            pathlib.Path("probe.s"),
            "O2",
            False,
        )
        self.assertIn("-mllvm", enabled)
        self.assertIn("-p2-unified-memory", enabled)
        self.assertIn("-fno-builtin", enabled)
        self.assertIn("-fno-builtin", disabled)
        self.assertNotIn("-mllvm", disabled)
        self.assertNotIn("-p2-unified-memory", disabled)

    def test_probe_covers_all_scalar_widths_bulk_ops_and_hub_provenance(self):
        source = CODEGEN.SOURCE.read_text()
        for function in (
            *CODEGEN.EXPECTED_FUNCTIONS,
            *CODEGEN.HUB_FUNCTIONS,
        ):
            self.assertIn(function, source)
        for builtin in ("__builtin_memcpy", "__builtin_memmove", "__builtin_memset"):
            self.assertIn(builtin, source)
        for function in (
            "p2_probe_dynamic_libc_memcpy",
            "p2_probe_dynamic_libc_memmove",
            "p2_probe_dynamic_libc_memset",
        ):
            self.assertIn(function, source)
        self.assertIn("volatile p2_probe_u32_t slot", source)
        self.assertIn("g_p2_probe_hub_word", source)
        for probe in (
            *CODEGEN.NEGATIVE_C_PROBES.values(),
            *CODEGEN.NEGATIVE_IR_PROBES.values(),
            CODEGEN.PROVENANCE_IR_PROBE,
        ):
            self.assertTrue(probe.is_file(), probe)

        provenance = CODEGEN.PROVENANCE_IR_PROBE.read_text()
        for token in (
            "ptrtoint",
            "inttoptr",
            "getelementptr i8",
            "alias i8",
            "268435456",
            *CODEGEN.PROVENANCE_FUNCTIONS,
        ):
            self.assertIn(token, provenance)


if __name__ == "__main__":
    unittest.main()
