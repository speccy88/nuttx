#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compile-only verifier for the opt-in P2 unified-memory lowering pass."""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools" / "p2" / "probes" / "unified-memory.c"
PROBE_DIRECTORY = SOURCE.parent
NEGATIVE_C_PROBES = {
    "atomic operation": PROBE_DIRECTORY / "unified-memory-atomicrmw.c",
    "compare exchange": PROBE_DIRECTORY / "unified-memory-cmpxchg.c",
    "inline asm": PROBE_DIRECTORY / "unified-memory-inline-asm.c",
}
NEGATIVE_IR_PROBES = {
    "atomic load": PROBE_DIRECTORY / "unified-memory-atomic-load.ll",
    "atomic store": PROBE_DIRECTORY / "unified-memory-atomic-store.ll",
    "atomicrmw": PROBE_DIRECTORY / "unified-memory-atomicrmw.ll",
    "cmpxchg": PROBE_DIRECTORY / "unified-memory-cmpxchg.ll",
    "va_arg": PROBE_DIRECTORY / "unified-memory-vaarg.ll",
}
PROVENANCE_IR_PROBE = PROBE_DIRECTORY / "unified-memory-provenance.ll"
OPTIMIZATIONS = ("O0", "Os", "O2")
PASS_ARGUMENTS = ("-mllvm", "-p2-unified-memory")

SCALAR_FUNCTIONS = {
    "p2_probe_dynamic_load8": "__p2_xmem_load8",
    "p2_probe_dynamic_load16": "__p2_xmem_load16",
    "p2_probe_dynamic_load32": "__p2_xmem_load32",
    "p2_probe_dynamic_load64": "__p2_xmem_load64",
    "p2_probe_dynamic_store8": "__p2_xmem_store8",
    "p2_probe_dynamic_store16": "__p2_xmem_store16",
    "p2_probe_dynamic_store32": "__p2_xmem_store32",
    "p2_probe_dynamic_store64": "__p2_xmem_store64",
}
BULK_FUNCTIONS = {
    "p2_probe_dynamic_memcpy": "__p2_xmem_memcpy",
    "p2_probe_dynamic_memmove": "__p2_xmem_memmove",
    "p2_probe_dynamic_memset": "__p2_xmem_memset",
    "p2_probe_dynamic_libc_memcpy": "__p2_xmem_memcpy",
    "p2_probe_dynamic_libc_memmove": "__p2_xmem_memmove",
    "p2_probe_dynamic_libc_memset": "__p2_xmem_memset",
}
HUB_FUNCTIONS = (
    "p2_probe_hub_global_load",
    "p2_probe_hub_global_store",
    "p2_probe_hub_stack_roundtrip",
)
PROVENANCE_FUNCTIONS = {
    "p2_probe_integer_derived_tag": "__p2_xmem_load8",
    "p2_probe_non_inbounds_gep_escape": "__p2_xmem_load8",
    "p2_probe_out_of_range_global_alias": "__p2_xmem_load8",
}
PROVENANCE_HUB_FUNCTIONS = ("p2_probe_hub_byval_vaarg",)
EXPECTED_FUNCTIONS = {**SCALAR_FUNCTIONS, **BULK_FUNCTIONS}
HELPER_PATTERN = re.compile(r"\b__p2_xmem_[A-Za-z0-9_]+\b")
NATIVE_MEMORY_PATTERN = re.compile(
    r"\b(?:rdbyte|rdword|rdlong|wrbyte|wrword|wrlong)\b", re.IGNORECASE
)


class CodegenError(RuntimeError):
    """The compiler output does not satisfy the unified-memory contract."""


class UnsupportedCompiler(CodegenError):
    """The selected compiler does not provide the opt-in lowering pass."""


def compiler_command(
    clang: pathlib.Path,
    source: pathlib.Path,
    output: pathlib.Path,
    optimization: str,
    enabled: bool,
) -> list[str]:
    """Return one reproducible compile-only probe command."""

    command = [
        str(clang),
        "--target=p2",
        "-std=c11",
        "-ffreestanding",
        "-fno-builtin",
        "-fno-jump-tables",
        "-ffunction-sections",
        "-fdata-sections",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-nostdlib",
        f"-{optimization}",
    ]
    if enabled:
        command.extend(PASS_ARGUMENTS)
    command.extend(("-S", str(source), "-o", str(output)))
    return command


def function_body(assembly: str, function: str) -> str:
    """Extract a named P2 function body from compiler-generated assembly."""

    match = re.search(
        rf"^{re.escape(function)}:[^\n]*\n"
        rf"(?P<body>.*?)"
        rf"^\s*\.size\s+{re.escape(function)}\s*,",
        assembly,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise CodegenError(f"missing function in assembly: {function}")
    return match.group("body")


def helper_references(text: str) -> set[str]:
    """Return all external-memory helper symbols mentioned in text."""

    return set(HELPER_PATTERN.findall(text))


def verify_enabled_assembly(assembly: str, optimization: str) -> int:
    """Verify helper lowering and native Hub provenance in one assembly file."""

    for function, expected in EXPECTED_FUNCTIONS.items():
        body = function_body(assembly, function)
        actual = helper_references(body)
        if actual != {expected}:
            raise CodegenError(
                f"{optimization} {function} helper mismatch: "
                f"expected {expected}, found {sorted(actual)}"
            )

    for function in HUB_FUNCTIONS:
        body = function_body(assembly, function)
        actual = helper_references(body)
        if actual:
            raise CodegenError(
                f"{optimization} {function} incorrectly calls "
                f"external-memory helpers: {sorted(actual)}"
            )
        if NATIVE_MEMORY_PATTERN.search(body) is None:
            raise CodegenError(
                f"{optimization} {function} has no native Hub memory access"
            )

    return len(EXPECTED_FUNCTIONS) + len(HUB_FUNCTIONS)


def verify_disabled_assembly(assembly: str, optimization: str) -> None:
    """Verify that the compiler does not enable the ABI without the flag."""

    helpers = helper_references(assembly)
    if helpers:
        raise CodegenError(
            f"{optimization} default-disabled compile references helpers: "
            f"{sorted(helpers)}"
        )


def verify_provenance_assembly(assembly: str) -> int:
    """Require deceptive Hub-derived tag addresses to remain conservative."""

    for function, expected in PROVENANCE_FUNCTIONS.items():
        body = function_body(assembly, function)
        actual = helper_references(body)
        if actual != {expected}:
            raise CodegenError(
                f"provenance escape in {function}: expected {expected}, "
                f"found {sorted(actual)}"
            )

    for function in PROVENANCE_HUB_FUNCTIONS:
        body = function_body(assembly, function)
        actual = helper_references(body)
        if actual:
            raise CodegenError(
                f"proven Hub byval object {function} incorrectly calls "
                f"helpers: {sorted(actual)}"
            )

    return len(PROVENANCE_FUNCTIONS) + len(PROVENANCE_HUB_FUNCTIONS)


def verify_rejection_diagnostic(stderr: str, operation: str) -> None:
    """Require a deliberate unified-memory rejection, not an incidental error."""

    lowered = stderr.lower()
    operation_markers = {
        "atomic operation": ("atomic operation", "atomicrmw", "__atomic_"),
        "compare exchange": (
            "compare exchange",
            "cmpxchg",
            "atomic_compare_exchange",
        ),
        "inline asm": ("inline asm", "inline assembly", "inlineasm"),
        "atomicrmw": ("atomicrmw",),
        "cmpxchg": ("cmpxchg", "compare exchange"),
        "atomic load": ("atomic load",),
        "atomic store": ("atomic store",),
        "va_arg": ("va_arg", "va list", "va_list"),
    }
    markers = operation_markers[operation]
    if "unified" not in lowered or not any(
        marker in lowered for marker in markers
    ):
        raise CodegenError(
            f"{operation} failed without an explicit unified-memory "
            f"diagnostic:\n{stderr}"
        )


def _looks_like_missing_pass(stderr: str) -> bool:
    lowered = stderr.lower()
    return "p2-unified-memory" in lowered and any(
        marker in lowered
        for marker in (
            "unknown command line argument",
            "unknown argument",
            "unrecognized command line option",
            "did you mean",
        )
    )


def _compile(command: list[str], enabled: bool) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr or exc.stdout or str(exc)
        if enabled and _looks_like_missing_pass(detail):
            raise UnsupportedCompiler(
                "P2 compiler does not recognize -mllvm -p2-unified-memory"
            ) from exc
        raise CodegenError(f"compiler command failed:\n{detail}") from exc


def _expect_rejection(command: list[str], operation: str) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    detail = result.stderr or result.stdout
    if result.returncode == 0:
        raise CodegenError(
            f"dynamic {operation} compiled in unified mode; it could silently "
            "access a tagged pointer"
        )
    verify_rejection_diagnostic(detail, operation)


def _file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compile_and_verify(
    clang: pathlib.Path,
    source: pathlib.Path = SOURCE,
    output_directory: pathlib.Path | None = None,
    llc: pathlib.Path | None = None,
) -> int:
    """Compile and inspect enabled/default-disabled output at O0, Os, and O2."""

    if not clang.is_file():
        raise CodegenError(f"required P2 clang is missing: {clang}")
    if not source.is_file():
        raise CodegenError(f"unified-memory probe source is missing: {source}")
    if llc is None:
        llc = clang.parent / "llc"
    if not llc.is_file():
        raise CodegenError(f"required P2 llc is missing: {llc}")
    for probe in (
        *NEGATIVE_C_PROBES.values(),
        *NEGATIVE_IR_PROBES.values(),
        PROVENANCE_IR_PROBE,
    ):
        if not probe.is_file():
            raise CodegenError(f"unified-memory probe is missing: {probe}")

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if output_directory is None:
        temporary = tempfile.TemporaryDirectory(prefix="p2-unified-memory-")
        directory = pathlib.Path(temporary.name)
    else:
        directory = output_directory
        directory.mkdir(parents=True, exist_ok=True)

    checked = 0
    try:
        for optimization in OPTIMIZATIONS:
            disabled = directory / f"unified-memory-{optimization}-disabled.s"
            enabled = directory / f"unified-memory-{optimization}-enabled.s"

            _compile(
                compiler_command(clang, source, disabled, optimization, False),
                enabled=False,
            )
            disabled_assembly = disabled.read_text()
            verify_disabled_assembly(disabled_assembly, optimization)

            _compile(
                compiler_command(clang, source, enabled, optimization, True),
                enabled=True,
            )
            enabled_assembly = enabled.read_text()
            checked += verify_enabled_assembly(enabled_assembly, optimization)

            for operation, probe in NEGATIVE_C_PROBES.items():
                rejected = directory / (
                    f"{probe.stem}-{optimization}-must-reject.s"
                )
                command = compiler_command(
                    clang, probe, rejected, optimization, True
                )
                if "atomic" in operation or operation == "compare exchange":
                    command.insert(
                        command.index("-S"), "-Wno-error=atomic-alignment"
                    )
                _expect_rejection(command, operation)
                checked += 1

        for operation, probe in NEGATIVE_IR_PROBES.items():
            rejected = directory / f"{probe.stem}-must-reject.s"
            _expect_rejection(
                [
                    str(llc),
                    "-mtriple=p2",
                    "-p2-unified-memory",
                    str(probe),
                    "-o",
                    str(rejected),
                ],
                operation,
            )
            checked += 1

        provenance = directory / "unified-memory-provenance-enabled.s"
        _compile(
            [
                str(llc),
                "-mtriple=p2",
                "-p2-unified-memory",
                str(PROVENANCE_IR_PROBE),
                "-o",
                str(provenance),
            ],
            enabled=True,
        )
        checked += verify_provenance_assembly(provenance.read_text())
    finally:
        if temporary is not None:
            temporary.cleanup()

    return checked


def main() -> int:
    default_root = pathlib.Path(
        os.environ.get(
            "P2LLVM_ROOT",
            str(ROOT.parent / ".p2-nuttx-cache" / "p2llvm" / "install"),
        )
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clang", type=pathlib.Path, default=default_root / "bin" / "clang"
    )
    parser.add_argument("--llc", type=pathlib.Path)
    parser.add_argument("--source", type=pathlib.Path, default=SOURCE)
    parser.add_argument("--output-directory", type=pathlib.Path)
    args = parser.parse_args()

    try:
        checked = compile_and_verify(
            args.clang, args.source, args.output_directory, args.llc
        )
    except UnsupportedCompiler as exc:
        parser.exit(2, f"BLOCKED: {exc}\n")
    except CodegenError as exc:
        parser.exit(1, f"FAILED: {exc}\n")

    print(
        "COMPILED: unified-memory probe at O0, Os, and O2 with the opt-in "
        "P2 compiler pass"
    )
    print(
        "STATICALLY-VERIFIED: dynamic scalar and bulk accesses call the "
        "__p2_xmem helpers while globals and stack objects remain native Hub "
        "accesses"
    )
    print(
        "STATICALLY-VERIFIED: integer-derived tags, non-inbounds GEP escapes, "
        "and out-of-range global aliases remain conservative"
    )
    print(
        "STATICALLY-VERIFIED: dynamic atomic operations, compare-exchange, "
        "and inline-assembly pointer operands are rejected explicitly"
    )
    print(
        "STATICALLY-VERIFIED: bounded formal byval va_list storage remains "
        "native Hub while arbitrary va_arg cursors are rejected explicitly"
    )
    version = subprocess.run(
        [str(args.clang), "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()[0]
    print(f"clang={args.clang}")
    print(f"clang_sha256={_file_sha256(args.clang)}")
    print(f"compiler={version}")
    print(f"pass_arguments={' '.join(PASS_ARGUMENTS)}")
    if args.output_directory is not None:
        print(f"assembly_directory={args.output_directory}")
    print(f"function_checks={checked}")
    print("HIL-REQUIRED: compile-only checking does not exercise PSRAM hardware")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
