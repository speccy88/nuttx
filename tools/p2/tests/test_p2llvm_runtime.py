#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

import hashlib
import importlib.util
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
RUNTIME_TOOL = ROOT / "tools" / "p2" / "p2llvm-runtime.py"
RUNTIME_PATCH = ROOT / "tools" / "p2" / "patches" / "p2llvm-python-runtime.patch"
LOCAL_BOOTSTRAP = ROOT / "tools" / "p2" / "bootstrap-local.sh"
CLOUD_BOOTSTRAP = ROOT / "tools" / "p2" / "bootstrap-cloud.sh"

SPEC = importlib.util.spec_from_file_location("p2llvm_runtime", RUNTIME_TOOL)
assert SPEC is not None and SPEC.loader is not None
RUNTIME = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNTIME)


def run(*arguments, cwd):
    return subprocess.run(
        arguments,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


class P2LLVMRuntimeTests(unittest.TestCase):
    def test_canonical_patch_has_exact_digest_and_path_contract(self):
        self.assertEqual(
            hashlib.sha256(RUNTIME_PATCH.read_bytes()).hexdigest(),
            RUNTIME.PATCH_SHA256,
        )
        RUNTIME.validate_patch(ROOT, RUNTIME_PATCH)

    def test_exact_source_state_application_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = pathlib.Path(directory)
            source = fixture / "source"
            source.mkdir()
            run("git", "init", "-q", cwd=source)
            run("git", "config", "user.name", "P2 Runtime Test", cwd=source)
            run("git", "config", "user.email", "p2-runtime@example.invalid", cwd=source)

            existing = RUNTIME.EXPECTED_PATCH_PATHS - RUNTIME.ADDED_PATCH_PATHS
            for relative in existing:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"baseline {relative}\n", encoding="utf-8")
            run("git", "add", ".", cwd=source)
            run("git", "commit", "-qm", "baseline", cwd=source)
            reference = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()

            for relative in existing:
                path = source / relative
                path.write_text(
                    path.read_text(encoding="utf-8") + f"patched {relative}\n",
                    encoding="utf-8",
                )
            for relative in RUNTIME.ADDED_PATCH_PATHS:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"new {relative}\n", encoding="utf-8")
                run("git", "add", "-N", "--", relative, cwd=source)

            patch = fixture / "runtime.patch"
            patch.write_bytes(
                subprocess.run(
                    ["git", "diff", "--binary", "HEAD"],
                    cwd=source,
                    check=True,
                    capture_output=True,
                ).stdout
            )
            patch_sha256 = hashlib.sha256(patch.read_bytes()).hexdigest()

            run("git", "restore", "--", *sorted(existing), cwd=source)
            run(
                "git",
                "reset",
                "--",
                *sorted(RUNTIME.ADDED_PATCH_PATHS),
                cwd=source,
            )
            for relative in RUNTIME.ADDED_PATCH_PATHS:
                (source / relative).unlink()

            self.assertTrue(
                RUNTIME.source_is_base(
                    source,
                    patch,
                    reference,
                    patch_sha256,
                    RUNTIME.EXPECTED_PATCH_PATHS,
                )
            )
            RUNTIME.apply_outer_patch(
                source,
                patch,
                reference,
                patch_sha256,
                RUNTIME.EXPECTED_PATCH_PATHS,
            )
            self.assertTrue(
                RUNTIME.source_is_patched(
                    source,
                    patch,
                    reference,
                    patch_sha256,
                    RUNTIME.EXPECTED_PATCH_PATHS,
                )
            )

            with (source / "build.py").open("a", encoding="utf-8") as stream:
                stream.write("tamper\n")
            self.assertFalse(
                RUNTIME.source_is_patched(
                    source,
                    patch,
                    reference,
                    patch_sha256,
                    RUNTIME.EXPECTED_PATCH_PATHS,
                )
            )
            with self.assertRaises(RUNTIME.ValidationError):
                RUNTIME.apply_outer_patch(
                    source,
                    patch,
                    reference,
                    patch_sha256,
                    RUNTIME.EXPECTED_PATCH_PATHS,
                )

    def test_archive_verifier_checks_path_members_symbols_and_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            archive = root / "libp2" / "lib" / "libcompiler_builtins.a"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"!<arch>\nfixture payload")

            tools = root / "bin"
            tools.mkdir()
            llvm_ar = tools / "llvm-ar"
            llvm_nm = tools / "llvm-nm"
            llvm_ar.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' truncdfsf2.c.obj fixdfdi.c.obj floatdidf.c.obj\n",
                encoding="utf-8",
            )
            llvm_nm.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' '00000000 T __truncdfsf2' "
                "'00000000 T __fixdfdi' '00000000 T __floatdidf'\n",
                encoding="utf-8",
            )
            llvm_ar.chmod(0o755)
            llvm_nm.chmod(0o755)

            verified, size, digest = RUNTIME.verify_archive(root)
            self.assertEqual(verified, archive.resolve())
            self.assertEqual(size, archive.stat().st_size)
            self.assertEqual(digest, hashlib.sha256(archive.read_bytes()).hexdigest())

            llvm_nm.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' '00000000 T __truncdfsf2' "
                "'00000000 T __fixdfdi'\n",
                encoding="utf-8",
            )
            llvm_nm.chmod(0o755)
            with self.assertRaisesRegex(
                RUNTIME.ValidationError, "does not export __floatdidf"
            ):
                RUNTIME.verify_archive(root)

    def test_bootstraps_apply_outer_before_nested_and_pin_archive(self):
        for bootstrap in (LOCAL_BOOTSTRAP, CLOUD_BOOTSTRAP):
            source = bootstrap.read_text(encoding="utf-8")
            for token in (
                "P2LLVM_RUNTIME_PATCH=$ROOT/tools/p2/patches/p2llvm-python-runtime.patch",
                "P2LLVM_RUNTIME_TOOL=$ROOT/tools/p2/p2llvm-runtime.py",
                '"$P2LLVM_ROOT/libp2/lib/libcompiler_builtins.a"',
                '"$P2LLVM_RUNTIME_PATCH"',
                "verify_p2llvm_runtime_source",
                "verify_p2llvm_runtime_archive",
            ):
                self.assertIn(token, source, f"{bootstrap.name}: missing {token}")

            main = source.rindex("ensure_p2llvm_checkout\n")
            outer = source.index("apply_p2llvm_outer_patch\n", main)
            nested = source.index("apply_p2llvm_patches\n", outer)
            self.assertLess(main, outer)
            self.assertLess(outer, nested)


if __name__ == "__main__":
    unittest.main()
