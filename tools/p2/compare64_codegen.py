#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline regression probe for p2llvm 64-bit comparison lowering."""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools" / "p2" / "abi-probe" / "comparison64.c"
OPTIMIZATIONS = ("O0", "Os", "O2")
MASK32 = (1 << 32) - 1
MASK64 = (1 << 64) - 1


class ProbeError(RuntimeError):
    """The compiler output does not satisfy the comparison ABI contract."""


def _signed(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    value &= (1 << bits) - 1
    return value - (1 << bits) if value & sign else value


def lexicographic_compare(left: int, right: int, signed: bool) -> int:
    """Model high-word-first P2 comparison; return -1, 0, or 1."""

    left &= MASK64
    right &= MASK64
    left_high = left >> 32
    right_high = right >> 32
    if signed:
        left_high = _signed(left_high, 32)
        right_high = _signed(right_high, 32)

    if left_high != right_high:
        return -1 if left_high < right_high else 1

    left_low = left & MASK32
    right_low = right & MASK32
    if left_low == right_low:
        return 0
    return -1 if left_low < right_low else 1


def verify_boundary_semantics() -> int:
    """Exhaust boundary limbs against native signed/unsigned comparisons."""

    words = (
        0,
        1,
        2,
        0x12345678,
        0x7FFFFFFE,
        0x7FFFFFFF,
        0x80000000,
        0x80000001,
        0x89ABCDEF,
        0x92345678,
        0xFFFFFFFE,
        0xFFFFFFFF,
    )
    values = tuple((high << 32) | low for high in words for low in words)
    checked = 0

    for signed in (False, True):
        for left in values:
            native_left = _signed(left, 64) if signed else left
            for right in values:
                native_right = _signed(right, 64) if signed else right
                expected = (native_left > native_right) - (native_left < native_right)
                actual = lexicographic_compare(left, right, signed)
                if actual != expected:
                    kind = "signed" if signed else "unsigned"
                    raise ProbeError(
                        f"{kind} semantic mismatch: "
                        f"left=0x{left:016x} right=0x{right:016x} "
                        f"expected={expected} actual={actual}"
                    )
                checked += 1

    return checked


def _function_lines(disassembly: str, function: str) -> list[str]:
    match = re.search(
        rf"^[0-9a-fA-F]+ <{re.escape(function)}>:\s*$"
        rf"(?P<body>.*?)"
        rf"(?=^[0-9a-fA-F]+ <|^Disassembly of section|\Z)",
        disassembly,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise ProbeError(f"missing function in disassembly: {function}")

    return [
        re.sub(r"\s+", " ", line).strip().lower()
        for line in match.group("body").splitlines()
        if line.strip()
    ]


def _find_instruction(lines: list[str], pattern: str, description: str) -> int:
    matches = [index for index, line in enumerate(lines) if re.search(pattern, line)]
    if len(matches) != 1:
        raise ProbeError(
            f"expected exactly one {description}; found {len(matches)}\n"
            + "\n".join(lines)
        )
    return matches[0]


def _verify_register_compare(
    disassembly: str, function: str, high_mnemonic: str
) -> None:
    lines = _function_lines(disassembly, function)
    if any(re.search(r"\bcm?ps?x\b", line) for line in lines):
        raise ProbeError(f"{function} uses chained CMPX/CMPSX")

    high = _find_instruction(
        lines,
        rf"\b{high_mnemonic}\s+r1,\s*r3\s+wcz\b",
        f"high-word {high_mnemonic.upper()}",
    )
    low = _find_instruction(
        lines,
        r"\bif_z\s+cmp\s+r0,\s*r2\s+wcz\b",
        "IF_Z low-word CMP",
    )
    if low != high + 1:
        raise ProbeError(
            f"{function} does not compare high then conditionally compare low"
        )


def _verify_immediate_compare(
    disassembly: str, function: str, high_mnemonic: str
) -> None:
    lines = _function_lines(disassembly, function)
    if any(re.search(r"\bcm?ps?x\b", line) for line in lines):
        raise ProbeError(f"{function} uses chained CMPX/CMPSX")

    high = _find_instruction(
        lines,
        rf"\b{high_mnemonic}\s+r1,\s*#[0-9]+\s+wcz\b",
        f"immediate high-word {high_mnemonic.upper()}",
    )
    low_aug = _find_instruction(
        lines,
        r"\bif_z\s+augs\s+#[0-9]+\b",
        "IF_Z low-immediate AUGS",
    )
    low = _find_instruction(
        lines,
        r"\bif_z\s+cmp\s+r0,\s*#[0-9]+\s+wcz\b",
        "IF_Z low-word immediate CMP",
    )
    if low_aug <= high or low != low_aug + 1:
        raise ProbeError(
            f"{function} does not preserve IF_Z across the low immediate AUGS/CMP"
        )


def verify_disassembly(disassembly: str, optimization: str) -> None:
    """Verify the exact two-limb comparison sequence for one object."""

    for function in (
        "p2_probe_s64_lt",
        "p2_probe_s64_ge",
        "p2_probe_s64_le",
        "p2_probe_s64_gt",
    ):
        _verify_register_compare(disassembly, function, "cmps")
    for function in (
        "p2_probe_u64_lt",
        "p2_probe_u64_ge",
        "p2_probe_u64_le",
        "p2_probe_u64_gt",
    ):
        _verify_register_compare(disassembly, function, "cmp")
    _verify_immediate_compare(
        disassembly, "p2_probe_s64_lt_large_low", "cmps"
    )
    _verify_immediate_compare(
        disassembly, "p2_probe_u64_lt_large_low", "cmp"
    )

    if re.search(r"\b(?:cmpx|cmpsx)\b", disassembly, re.IGNORECASE):
        raise ProbeError(f"{optimization} disassembly contains CMPX/CMPSX")


def compile_and_verify(toolchain_root: pathlib.Path, source: pathlib.Path = SOURCE) -> int:
    """Compile and inspect the probe at O0, Os, and O2 without target I/O."""

    clang = toolchain_root / "bin" / "clang"
    objdump = toolchain_root / "bin" / "llvm-objdump"
    for tool in (clang, objdump):
        if not tool.is_file():
            raise ProbeError(f"required P2 tool is missing: {tool}")
    if not source.is_file():
        raise ProbeError(f"comparison probe source is missing: {source}")

    checked = verify_boundary_semantics()
    with tempfile.TemporaryDirectory(prefix="p2-compare64-") as temporary:
        directory = pathlib.Path(temporary)
        for optimization in OPTIMIZATIONS:
            objfile = directory / f"comparison64-{optimization}.o"
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
                "-c",
                str(source),
                "-o",
                str(objfile),
            ]
            try:
                subprocess.run(command, check=True, capture_output=True, text=True)
                result = subprocess.run(
                    [str(objdump), "-dr", str(objfile)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                detail = exc.stderr or exc.stdout or str(exc)
                raise ProbeError(
                    f"{optimization} comparison probe command failed:\n{detail}"
                ) from exc
            verify_disassembly(result.stdout, optimization)

    return checked


def main() -> int:
    default_root = pathlib.Path(
        os.environ.get(
            "P2LLVM_ROOT",
            str(ROOT.parent / ".p2-nuttx-cache" / "p2llvm" / "install"),
        )
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain-root", type=pathlib.Path, default=default_root)
    parser.add_argument("--source", type=pathlib.Path, default=SOURCE)
    args = parser.parse_args()

    try:
        checked = compile_and_verify(args.toolchain_root, args.source)
    except ProbeError as exc:
        parser.exit(1, f"FAILED: {exc}\n")

    print(
        "STATICALLY-VERIFIED: P2 64-bit compare lowering uses high-word-first "
        "lexicographic comparisons at O0, Os, and O2"
    )
    print(f"functional_boundary_pairs={checked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
