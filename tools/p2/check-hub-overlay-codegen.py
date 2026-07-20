#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify stable P2 Hub-overlay identity and the complete link pipeline.

The probe is intentionally self-contained: it reads only the selected
toolchain, writes every generated input and output beneath one temporary
directory, and removes that directory before returning.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Iterable, Sequence


AUTO_SECTION_RE = re.compile(
    r"\.p2\.overlay\.auto\.([0-9a-f]{64})\."
    r"([0-9a-f]{8})\.([0-9a-f]{64})(?![0-9a-f])"
)
GROUPREF_RE = re.compile(
    r"__p2_overlay_groupref_([0-9a-f]{64})_([0-9a-f]{64})"
)
PACKING_PREFIX = "p2-overlay-link.py: link-assigned packing:"
SLOT_ADDRESS = "0x50000"
SLOT_SIZE = "0x10000"
LMA_ADDRESS = "0x80000"


class OverlayCodegenError(RuntimeError):
    """The selected toolchain violates the Hub-overlay link contract."""


@dataclasses.dataclass(frozen=True)
class Toolchain:
    root: pathlib.Path
    llc: pathlib.Path
    llvm_ar: pathlib.Path
    llvm_objdump: pathlib.Path
    llvm_readobj: pathlib.Path
    llvm_readelf: pathlib.Path
    ld_lld: pathlib.Path
    helper: pathlib.Path

    @classmethod
    def from_root(cls, root: pathlib.Path) -> "Toolchain":
        root = root.expanduser().resolve()
        binary = root / "bin"
        tools = cls(
            root=root,
            llc=binary / "llc",
            llvm_ar=binary / "llvm-ar",
            llvm_objdump=binary / "llvm-objdump",
            llvm_readobj=binary / "llvm-readobj",
            llvm_readelf=binary / "llvm-readelf",
            ld_lld=binary / "ld.lld",
            helper=binary / "p2-overlay-link.py",
        )
        for path in dataclasses.astuple(tools)[1:]:
            candidate = pathlib.Path(path)
            if not candidate.is_file():
                raise OverlayCodegenError(
                    "required Hub-overlay tool is missing: {}".format(candidate)
                )
        return tools


@dataclasses.dataclass(frozen=True)
class Identity:
    tu_token: str
    order: int
    function_token: str


def _run(
    command: Sequence[str],
    *,
    expect_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    rendered = " ".join(command)
    if expect_success and result.returncode != 0:
        raise OverlayCodegenError(
            "command failed with status {}: {}\n{}".format(
                result.returncode, rendered, result.stdout
            )
        )
    if not expect_success and result.returncode == 0:
        raise OverlayCodegenError(
            "command unexpectedly succeeded: {}\n{}".format(rendered, result.stdout)
        )
    return result


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _overlay_arguments(
    source_root: pathlib.Path,
    namespace: str,
    variant: str | None = None,
) -> list[str]:
    arguments = [
        "-p2-unified-memory",
        "-p2-hub-overlays",
        "-p2-hub-overlays-all",
        "-p2-hub-overlay-link-assigned",
        "-p2-hub-overlay-source-root={}".format(source_root),
        "-p2-hub-overlay-source-namespace={}".format(namespace),
    ]
    if variant is not None:
        arguments.append("-p2-hub-overlay-source-variant={}".format(variant))
    arguments.append("-p2-hub-overlay-slot-address={}".format(SLOT_ADDRESS))
    return arguments


def _compile_ir(
    tools: Toolchain,
    source: pathlib.Path,
    output: pathlib.Path,
    overlay_arguments: Iterable[str] = (),
) -> None:
    _run(
        [
            str(tools.llc),
            "-march=p2",
            "-O2",
            *overlay_arguments,
            "-filetype=obj",
            str(source),
            "-o",
            str(output),
        ]
    )


def _inspect_identity(tools: Toolchain, obj: pathlib.Path) -> Identity:
    report = _run(
        [str(tools.llvm_readobj), "--sections", "--symbols", str(obj)]
    ).stdout
    sections = {match.groups() for match in AUTO_SECTION_RE.finditer(report)}
    grouprefs = {match.groups() for match in GROUPREF_RE.finditer(report)}
    if len(sections) != 1:
        raise OverlayCodegenError(
            "{} exposes {} distinct automatic overlay sections, expected 1".format(
                obj, len(sections)
            )
        )
    tu_token, order_token, function_token = next(iter(sections))
    if int(order_token, 16) != 0:
        raise OverlayCodegenError(
            "first automatic function has nonzero call order: {}".format(order_token)
        )
    if grouprefs != {(tu_token, function_token)}:
        raise OverlayCodegenError(
            "automatic section/groupref identity mismatch in {}".format(obj)
        )
    return Identity(tu_token, int(order_token, 16), function_token)


def _helper_command(
    tools: Toolchain,
    output: pathlib.Path,
    inputs: Sequence[pathlib.Path],
    *,
    max_groups: int | None = None,
) -> list[str]:
    command = [
        sys.executable,
        str(tools.helper),
        "--fragment-only",
        "--slot-address",
        SLOT_ADDRESS,
        "--slot-size",
        SLOT_SIZE,
        "--lma-address",
        LMA_ADDRESS,
    ]
    if max_groups is not None:
        command.extend(("--max-groups", str(max_groups)))
    command.extend(("-o", str(output)))
    command.extend(str(path) for path in inputs)
    return command


def _identity_ir(source_filename: pathlib.Path) -> str:
    return '''source_filename = "{}"
target triple = "p2-unknown-nuttx"

define i32 @entry(i32 %value) #0 {{
entry:
  %result = add i32 %value, 1
  ret i32 %result
}}

define void @__p2_overlay_enter() #1 {{
entry:
  ret void
}}

attributes #0 = {{ noinline }}
attributes #1 = {{ noinline "p2-hub-resident" }}
'''.format(source_filename.as_posix())


def _driver_ir(source_filename: pathlib.Path) -> str:
    return '''source_filename = "{}"
target triple = "p2-unknown-nuttx"

declare i32 @auto_entry(i32)

define i32 @_start() {{
entry:
  %result = call i32 @auto_entry(i32 41)
  ret i32 %result
}}

define void @__p2_overlay_enter() {{
entry:
  ret void
}}
'''.format(source_filename.as_posix())


def _end_to_end_overlay_ir(source_filename: pathlib.Path) -> str:
    return '''source_filename = "{}"
target triple = "p2-unknown-nuttx"

define i32 @auto_entry(i32 %value) #0 {{
entry:
  %result = call i32 @auto_helper(i32 %value)
  ret i32 %result
}}

define internal i32 @auto_helper(i32 %value) #0 {{
entry:
  %result = add i32 %value, 1
  ret i32 %result
}}

define i32 @fixed_entry(i32 %value) #1 {{
entry:
  %result = xor i32 %value, 85
  ret i32 %result
}}

declare void @__p2_overlay_enter()

attributes #0 = {{ noinline }}
attributes #1 = {{ noinline "p2-hub-overlay-group"="4" }}
'''.format(source_filename.as_posix())


def _known_group_ir(source_filename: pathlib.Path) -> str:
    return '''source_filename = "{}"
target triple = "p2-unknown-nuttx"

@fixed_helper_pointer = global i32 (i32)* @fixed_helper, align 4

define i32 @fixed_entry(i32 %value) #0 {{
entry:
  %same = call i32 @fixed_helper(i32 %value)
  %cross = call i32 @fixed_other(i32 %same)
  ret i32 %cross
}}

define i32 @fixed_helper(i32 %value) #0 {{
entry:
  %result = add i32 %value, 1
  ret i32 %result
}}

define i32 @fixed_other(i32 %value) #1 {{
entry:
  %result = xor i32 %value, 85
  ret i32 %result
}}

define i32 @_start(i32 %value) #2 {{
entry:
  %result = call i32 @fixed_entry(i32 %value)
  ret i32 %result
}}

define void @__p2_overlay_enter() #2 {{
entry:
  ret void
}}

attributes #0 = {{ noinline "p2-hub-overlay-group"="4" }}
attributes #1 = {{ noinline "p2-hub-overlay-group"="5" }}
attributes #2 = {{ noinline "p2-hub-resident" }}
'''.format(source_filename.as_posix())


BASE_LINKER_SCRIPT = """\
__p2_overlay_slot_start = 0x00050000;
__p2_overlay_slot_end = 0x00060000;

SECTIONS
{
  . = 0x00001000;
  .text : { *(.text .text.*) }
  .rodata : { *(.rodata .rodata.*) }
  .data : { *(.data .data.*) }
  .bss (NOLOAD) : { *(.bss .bss.*) *(COMMON) }
  .p2.overlay.stubs : ALIGN(4) { KEEP(*(.p2.overlay.stubs)) }
  .p2.xdata 0x10000000 : AT(0x02000000)
  {
    KEEP(*(.p2.xdata.ro.overlay.entries))
  }
}
"""


def _verify_identity_contract(
    tools: Toolchain, temporary: pathlib.Path
) -> tuple[Identity, int]:
    root_a = temporary / "checkout-a"
    root_b = temporary / "checkout-b"
    source_a = root_a / "input.ll"
    source_b = root_b / "input.ll"
    _write(source_a, _identity_ir(root_a / "src/./sub/../foo.c"))
    _write(source_b, _identity_ir(root_b / "src/foo.c"))

    object_a = temporary / "checkout-a.o"
    object_b = temporary / "checkout-b.o"
    _compile_ir(
        tools,
        source_a,
        object_a,
        _overlay_arguments(root_a, "runtime"),
    )
    _compile_ir(
        tools,
        source_b,
        object_b,
        _overlay_arguments(root_b, "runtime"),
    )
    if object_a.read_bytes() != object_b.read_bytes():
        raise OverlayCodegenError(
            "equivalent relative sources in two checkout roots produced "
            "different objects"
        )
    identity = _inspect_identity(tools, object_a)
    if _inspect_identity(tools, object_b) != identity:
        raise OverlayCodegenError("checkout-root normalization changed TU identity")

    other = temporary / "namespace-other.o"
    _compile_ir(
        tools,
        source_a,
        other,
        _overlay_arguments(root_a, "other"),
    )
    other_identity = _inspect_identity(tools, other)
    if other_identity.tu_token == identity.tu_token:
        raise OverlayCodegenError("source namespace did not separate TU identity")

    variant_identities = []
    for variant in ("v1", "v2"):
        output = temporary / "variant-{}.o".format(variant)
        _compile_ir(
            tools,
            source_a,
            output,
            _overlay_arguments(root_a, "runtime", variant),
        )
        variant_identities.append(_inspect_identity(tools, output))
    if variant_identities[0].tu_token == variant_identities[1].tu_token:
        raise OverlayCodegenError("source variant did not separate TU identity")

    distinct_script = temporary / "distinct.ld"
    distinct = _run(_helper_command(tools, distinct_script, (object_a, other)))
    if not distinct.stdout.startswith(PACKING_PREFIX):
        raise OverlayCodegenError(
            "overlay helper success output lacks the packing contract: {}".format(
                distinct.stdout
            )
        )

    duplicate_script = temporary / "duplicate.ld"
    duplicate = _run(
        _helper_command(tools, duplicate_script, (object_a, object_b)),
        expect_success=False,
    )
    if (
        "duplicate link-assigned section '.p2.overlay.auto." not in duplicate.stdout
        or AUTO_SECTION_RE.search(duplicate.stdout) is None
    ):
        raise OverlayCodegenError(
            "overlay helper duplicate diagnostic is not exact: {}".format(
                duplicate.stdout
            )
        )

    return identity, 8


def _section_size(report: str, name: str) -> int:
    escaped = re.escape(name)
    match = re.search(
        rf"\[\s*\d+\]\s+{escaped}\s+\S+\s+[0-9a-fA-F]+\s+"
        rf"[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+",
        report,
    )
    if match is not None:
        return int(match.group(1), 16)
    raise OverlayCodegenError("linked ELF is missing section {}".format(name))


def _verify_end_to_end(tools: Toolchain, temporary: pathlib.Path) -> int:
    source_root = temporary / "src"
    driver_source = temporary / "driver.ll"
    overlay_source = temporary / "overlay.ll"
    _write(driver_source, _driver_ir(source_root / "driver.c"))
    _write(overlay_source, _end_to_end_overlay_ir(source_root / "runtime.c"))

    driver_object = temporary / "driver.o"
    overlay_object = temporary / "overlay.o"
    _compile_ir(tools, driver_source, driver_object, ("-p2-unified-memory",))
    _compile_ir(
        tools,
        overlay_source,
        overlay_object,
        _overlay_arguments(source_root, "runtime", "v1"),
    )

    relocations = _run(
        [str(tools.llvm_readobj), "--relocations", str(overlay_object)]
    ).stdout
    if GROUPREF_RE.search(relocations) is None:
        raise OverlayCodegenError(
            "compiler output has no immutable overlay-entry groupref relocation"
        )

    archive = temporary / "overlay.a"
    _run([str(tools.llvm_ar), "cr", str(archive), str(overlay_object)])
    fragment = temporary / "packed.ld"
    packed = _run(
        _helper_command(tools, fragment, (archive,), max_groups=4)
    )
    if not packed.stdout.startswith(PACKING_PREFIX):
        raise OverlayCodegenError("end-to-end helper did not report link packing")
    if "functions=2, automatic-groups=1, dense-groups=4" not in packed.stdout:
        raise OverlayCodegenError(
            "end-to-end helper packing summary changed: {}".format(packed.stdout)
        )

    fragment_text = fragment.read_text(encoding="utf-8")
    if fragment_text.count("LONG(0)") != 4:
        raise OverlayCodegenError(
            "dense groups do not each contain one four-byte LONG anchor"
        )
    for group in range(1, 5):
        section = ".p2.overlay.group.{:08x}".format(group)
        if section not in fragment_text:
            raise OverlayCodegenError(
                "generated fragment omits dense group {}".format(group)
            )

    base_script = temporary / "base.ld"
    linked = temporary / "linked.elf"
    _write(base_script, BASE_LINKER_SCRIPT)
    _run(
        [
            str(tools.ld_lld),
            "-T",
            str(base_script),
            "-T",
            str(fragment),
            "-e",
            "_start",
            "--gc-sections",
            "-o",
            str(linked),
            str(driver_object),
            str(archive),
        ]
    )

    linked_report = _run(
        [str(tools.llvm_readelf), "-SW", "-s", str(linked)]
    ).stdout
    for group in (2, 3):
        name = ".p2.overlay.group.{:08x}".format(group)
        if _section_size(linked_report, name) != 4:
            raise OverlayCodegenError(
                "anchor-only group {} is not exactly four bytes".format(group)
            )
    if re.search(r"\bUND\s+__p2_overlay_groupref_", linked_report):
        raise OverlayCodegenError("linked ELF retains an undefined groupref")
    absolute_grouprefs = re.findall(
        r"\bABS\s+(__p2_overlay_groupref_[0-9a-f]{64}_[0-9a-f]{64})",
        linked_report,
    )
    if len(set(absolute_grouprefs)) != 2:
        raise OverlayCodegenError(
            "linked ELF has {} absolute grouprefs, expected 2".format(
                len(set(absolute_grouprefs))
            )
        )

    descriptors = _run(
        [str(tools.llvm_readelf), "-x", ".p2.xdata", str(linked)]
    ).stdout.lower()
    if (
        re.search(r"01000000\s+04000000", descriptors) is None
        or re.search(r"04000000\s+04000000", descriptors) is None
    ):
        raise OverlayCodegenError(
            "overlay descriptor bytes do not encode automatic group 1 and fixed group 4"
        )
    return 10


def _verify_known_group_calls(tools: Toolchain, temporary: pathlib.Path) -> int:
    source = temporary / "known-group.ll"
    obj = temporary / "known-group.o"
    _write(source, _known_group_ir(source))
    _compile_ir(
        tools,
        source,
        obj,
        (
            "-p2-unified-memory",
            "-p2-hub-overlays",
            "-p2-hub-overlay-slot-address={}".format(SLOT_ADDRESS),
        ),
    )

    disassembly = _run([str(tools.llvm_objdump), "-dr", str(obj)]).stdout
    if re.search(
        r"R_P2_20\s+\.p2\.overlay\.body\.00000004\+0x[0-9a-fA-F]+",
        disassembly,
    ) is None:
        raise OverlayCodegenError(
            "same-known-group call did not target the private body"
        )
    if re.search(r"R_P2_20\s+fixed_helper(?:\s|$)", disassembly):
        raise OverlayCodegenError(
            "same-known-group call still enters the public helper veneer"
        )
    if re.search(r"R_P2_20\s+fixed_other(?:\s|$)", disassembly) is None:
        raise OverlayCodegenError("cross-group call no longer uses its veneer")
    if re.search(r"R_P2_20\s+fixed_entry(?:\s|$)", disassembly) is None:
        raise OverlayCodegenError("resident caller no longer uses its veneer")
    if len(re.findall(r"R_P2_20\s+__p2_overlay_enter", disassembly)) != 3:
        raise OverlayCodegenError("public veneer count or dispatcher target changed")

    relocations = _run(
        [str(tools.llvm_readobj), "--relocations", str(obj)]
    ).stdout
    if re.search(r"R_P2_32\s+fixed_helper(?:\s|$)", relocations) is None:
        raise OverlayCodegenError("function pointer no longer names the public veneer")
    return 6


def verify(toolchain_root: pathlib.Path) -> tuple[int, Identity, str]:
    tools = Toolchain.from_root(toolchain_root)
    with tempfile.TemporaryDirectory(prefix="p2-hub-overlay-codegen-") as raw:
        temporary = pathlib.Path(raw)
        identity, identity_checks = _verify_identity_contract(tools, temporary)
        end_to_end_checks = _verify_end_to_end(tools, temporary)
        known_group_checks = _verify_known_group_calls(tools, temporary)
        linked_sha256 = _sha256(temporary / "linked.elf")
    return (
        identity_checks + end_to_end_checks + known_group_checks,
        identity,
        linked_sha256,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain-root", required=True, type=pathlib.Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        checks, identity, linked_sha256 = verify(args.toolchain_root)
    except (OSError, OverlayCodegenError) as exc:
        print("P2OVERLAYCODEGEN:FAIL:{}".format(exc), file=sys.stderr)
        return 1
    print(
        "P2OVERLAYCODEGEN:PASS:CHECKS={}:TU={}:LINKED_SHA256={}".format(
            checks, identity.tu_token, linked_sha256
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
