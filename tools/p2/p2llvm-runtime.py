#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Apply and verify the pinned outer-tree P2LLVM runtime patch."""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import subprocess
import sys
import tempfile

PATCH_SHA256 = "69ec3b1f9157df1b8f5a79400b33a767941c6dd27c0da97e33bcb852842a6108"
EXPECTED_PATCH_PATHS = frozenset(
    {
        "build.py",
        "libp2/lib/builtins/CMakeLists.txt",
        "libp2/lib/builtins/floatdidf.c",
        "libp2/lib/builtins/floatunsidf.c",
        "libp2/lib/builtins/fp_add_impl.inc",
        "libp2/lib/builtins/fp_fixint_impl.inc",
        "libp2/lib/builtins/fp_mul_impl.inc",
        "libp2/lib/builtins/int_lib.h",
        "libp2/lib/builtins/memcpy.c",
        "libp2/lib/builtins/memmove.c",
        "libp2/lib/builtins/memset.c",
        "libp2/lib/builtins/powf.c",
        "libp2/lib/builtins/sqrtf.c",
        "libp2/lib/builtins/tests/runtime_builtins_test.c",
        "libp2/lib/builtins/tests/test_runtime_builtins.py",
        "libp2/lib/builtins/udivmoddi4.c",
    }
)
ADDED_PATCH_PATHS = frozenset(
    {
        "libp2/lib/builtins/tests/runtime_builtins_test.c",
        "libp2/lib/builtins/tests/test_runtime_builtins.py",
    }
)
RUNTIME_SYMBOLS = (
    "__truncdfsf2",
    "__fixdfdi",
    "__floatdidf",
    "__floatunsidf",
    "__adddf3",
    "__muldf3",
)
RUNTIME_MEMBERS = (
    "truncdfsf2.c.obj",
    "fixdfdi.c.obj",
    "floatdidf.c.obj",
    "floatunsidf.c.obj",
    "adddf3.c.obj",
    "muldf3.c.obj",
)
FORBIDDEN_RUNTIME_SECTIONS = frozenset({"lut", ".lut", ".p2.lut"})


class ValidationError(RuntimeError):
    """The source patch or installed runtime failed a required invariant."""


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(
    source: pathlib.Path,
    *arguments: str,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(source), *arguments],
        capture_output=True,
        text=True,
        env=env,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git failed"
        raise ValidationError(detail)
    return result


def lines(output: str) -> set[str]:
    return {line for line in output.splitlines() if line}


def validate_patch(
    source: pathlib.Path,
    patch: pathlib.Path,
    expected_sha256: str = PATCH_SHA256,
    expected_paths: frozenset[str] = EXPECTED_PATCH_PATHS,
) -> None:
    source = source.resolve()
    patch = patch.resolve()
    if not patch.is_file():
        raise ValidationError(f"outer runtime patch is missing: {patch}")

    actual_sha256 = sha256(patch)
    if actual_sha256 != expected_sha256:
        raise ValidationError(
            f"outer runtime patch SHA-256 is {actual_sha256}; "
            f"expected {expected_sha256}"
        )

    result = git(source, "apply", "--numstat", "--unidiff-zero", str(patch))
    paths = set()
    for record in result.stdout.splitlines():
        fields = record.split("\t", 2)
        if len(fields) != 3:
            raise ValidationError(f"malformed patch numstat record: {record!r}")
        path = fields[2]
        if (
            pathlib.PurePosixPath(path).is_absolute()
            or ".." in pathlib.PurePosixPath(path).parts
        ):
            raise ValidationError(f"unsafe path in outer runtime patch: {path}")
        paths.add(path)

    if paths != expected_paths:
        raise ValidationError(
            "outer runtime patch path set differs from the pinned contract"
        )


def source_head(source: pathlib.Path) -> str:
    return git(source, "rev-parse", "HEAD").stdout.strip()


def source_changes(source: pathlib.Path) -> tuple[set[str], set[str], bool]:
    tracked = lines(
        git(
            source,
            "diff",
            "--name-only",
            "--ignore-submodules=dirty",
            "--",
        ).stdout
    )
    untracked = lines(git(source, "ls-files", "--others", "--exclude-standard").stdout)
    staged = (
        git(
            source,
            "diff",
            "--cached",
            "--quiet",
            "--ignore-submodules=dirty",
            "--",
            check=False,
        ).returncode
        != 0
    )
    return tracked, untracked, staged


def source_is_base(
    source: pathlib.Path,
    patch: pathlib.Path,
    reference: str,
    expected_sha256: str = PATCH_SHA256,
    expected_paths: frozenset[str] = EXPECTED_PATCH_PATHS,
) -> bool:
    source = source.resolve()
    patch = patch.resolve()
    validate_patch(source, patch, expected_sha256, expected_paths)
    if source_head(source) != reference:
        return False
    tracked, untracked, staged = source_changes(source)
    return not tracked and not untracked and not staged


def source_is_patched(
    source: pathlib.Path,
    patch: pathlib.Path,
    reference: str,
    expected_sha256: str = PATCH_SHA256,
    expected_paths: frozenset[str] = EXPECTED_PATCH_PATHS,
) -> bool:
    source = source.resolve()
    patch = patch.resolve()
    validate_patch(source, patch, expected_sha256, expected_paths)
    if source_head(source) != reference:
        return False

    tracked, untracked, staged = source_changes(source)
    if staged or (tracked | untracked) != expected_paths:
        return False
    if untracked != (expected_paths & ADDED_PATCH_PATHS):
        return False

    with tempfile.TemporaryDirectory(prefix="p2llvm-runtime-index-") as directory:
        temporary = pathlib.Path(directory)
        index = temporary / "index"
        objects = temporary / "objects"
        objects.mkdir()
        source_objects = pathlib.Path(
            git(source, "rev-parse", "--git-path", "objects").stdout.strip()
        )
        if not source_objects.is_absolute():
            source_objects = source / source_objects
        environment = dict(os.environ)
        environment["GIT_INDEX_FILE"] = str(index)
        environment["GIT_OBJECT_DIRECTORY"] = str(objects)
        environment["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = str(source_objects.resolve())
        git(source, "read-tree", "HEAD", env=environment)
        git(source, "add", "-A", "--", *sorted(expected_paths), env=environment)
        reverse = (
            "apply",
            "--cached",
            "--reverse",
            "--unidiff-zero",
            str(patch),
        )
        if git(source, *reverse, env=environment, check=False).returncode != 0:
            return False
        return (
            git(
                source,
                "diff",
                "--cached",
                "--quiet",
                "HEAD",
                "--",
                env=environment,
                check=False,
            ).returncode
            == 0
        )


def apply_outer_patch(
    source: pathlib.Path,
    patch: pathlib.Path,
    reference: str,
    expected_sha256: str = PATCH_SHA256,
    expected_paths: frozenset[str] = EXPECTED_PATCH_PATHS,
) -> None:
    source = source.resolve()
    patch = patch.resolve()
    if source_is_patched(source, patch, reference, expected_sha256, expected_paths):
        print(f"P2LLVM_RUNTIME:SOURCE=PATCHED:SHA256={expected_sha256}")
        return
    if not source_is_base(source, patch, reference, expected_sha256, expected_paths):
        raise ValidationError(
            "p2llvm outer source is neither the pinned base nor the exact "
            "runtime-patched state"
        )

    git(source, "apply", "--unidiff-zero", str(patch))
    if not source_is_patched(source, patch, reference, expected_sha256, expected_paths):
        raise ValidationError("outer runtime patch did not produce its exact state")
    print(f"P2LLVM_RUNTIME:SOURCE=PATCHED:SHA256={expected_sha256}")


def verify_archive(toolchain_root: pathlib.Path) -> tuple[pathlib.Path, int, str]:
    toolchain_root = toolchain_root.resolve()
    archive = toolchain_root / "libp2" / "lib" / "libcompiler_builtins.a"
    llvm_ar = toolchain_root / "bin" / "llvm-ar"
    llvm_nm = toolchain_root / "bin" / "llvm-nm"
    llvm_readelf = toolchain_root / "bin" / "llvm-readelf"

    if not archive.is_file() or archive.stat().st_size <= len(b"!<arch>\n"):
        raise ValidationError(
            f"compiler builtins archive is missing or empty: {archive}"
        )
    if archive.read_bytes()[:8] != b"!<arch>\n":
        raise ValidationError(f"compiler builtins archive has invalid magic: {archive}")
    for tool in (llvm_ar, llvm_nm, llvm_readelf):
        if not tool.is_file() or not os.access(tool, os.X_OK):
            raise ValidationError(
                f"required archive inspection tool is missing: {tool}"
            )

    members_result = subprocess.run(
        [str(llvm_ar), "t", str(archive)], capture_output=True, text=True
    )
    if members_result.returncode != 0:
        raise ValidationError(members_result.stderr.strip() or "llvm-ar failed")
    members = {
        pathlib.PurePosixPath(name).name for name in lines(members_result.stdout)
    }
    missing_members = set(RUNTIME_MEMBERS) - members
    if missing_members:
        raise ValidationError(
            "compiler builtins archive lacks members: "
            + ", ".join(sorted(missing_members))
        )

    symbols_result = subprocess.run(
        [str(llvm_nm), "--defined-only", str(archive)],
        capture_output=True,
        text=True,
    )
    if symbols_result.returncode != 0:
        raise ValidationError(symbols_result.stderr.strip() or "llvm-nm failed")
    for symbol in RUNTIME_SYMBOLS:
        if (
            re.search(
                rf"(?m)^[0-9a-fA-F]+\s+T\s+{re.escape(symbol)}$",
                symbols_result.stdout,
            )
            is None
        ):
            raise ValidationError(f"compiler builtins archive does not export {symbol}")

    sections_result = subprocess.run(
        [str(llvm_readelf), "-SW", str(archive)],
        capture_output=True,
        text=True,
    )
    if sections_result.returncode != 0:
        raise ValidationError(
            sections_result.stderr.strip() or "llvm-readelf failed"
        )
    section_names = set(
        re.findall(
            r"(?m)^\s*\[\s*\d+\]\s+(\S+)",
            sections_result.stdout,
        )
    )
    forbidden_sections = section_names & FORBIDDEN_RUNTIME_SECTIONS
    if forbidden_sections:
        raise ValidationError(
            "compiler builtins archive contains callable LUT sections: "
            + ", ".join(sorted(forbidden_sections))
        )

    size = archive.stat().st_size
    digest = sha256(archive)
    print(
        f"P2LLVM_RUNTIME:ARCHIVE={archive}:BYTES={size}:"
        f"SHA256={digest}:SYMBOLS={','.join(RUNTIME_SYMBOLS)}:"
        "PLACEMENT=HUB_TEXT"
    )
    return archive, size, digest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    apply_parser = subparsers.add_parser("apply-outer")
    apply_parser.add_argument("--source", required=True, type=pathlib.Path)
    apply_parser.add_argument("--patch", required=True, type=pathlib.Path)
    apply_parser.add_argument("--ref", required=True)

    source_parser = subparsers.add_parser("verify-source")
    source_parser.add_argument("--source", required=True, type=pathlib.Path)
    source_parser.add_argument("--patch", required=True, type=pathlib.Path)
    source_parser.add_argument("--ref", required=True)

    archive_parser = subparsers.add_parser("verify-archive")
    archive_parser.add_argument("--toolchain-root", required=True, type=pathlib.Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        if arguments.command == "apply-outer":
            apply_outer_patch(arguments.source, arguments.patch, arguments.ref)
        elif arguments.command == "verify-source":
            if not source_is_patched(arguments.source, arguments.patch, arguments.ref):
                raise ValidationError("p2llvm outer source is not exactly patched")
            print(f"P2LLVM_RUNTIME:SOURCE=PATCHED:SHA256={PATCH_SHA256}")
        else:
            verify_archive(arguments.toolchain_root)
    except (OSError, ValidationError) as error:
        print(f"P2LLVM_RUNTIME:ERROR:{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
