#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Verify the standalone P2 hello ELF's required native memory layout."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def run(tool: Path, *args: str) -> str:
    result = subprocess.run(
        [str(tool), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{' '.join((str(tool), *args))} failed:\n{result.stdout}"
        )

    return result.stdout


def parse_symbols(output: str) -> tuple[dict[str, int], list[str]]:
    symbols: dict[str, int] = {}
    undefined: list[str] = []

    for line in output.splitlines():
        match = re.match(
            r"\s*\d+:\s+([0-9a-fA-F]+)\s+\d+\s+\S+\s+\S+\s+\S+\s+"
            r"(\S+)\s+(.+)$",
            line,
        )
        if match is None:
            continue

        value, section, name = match.groups()
        name = name.split("@", 1)[0]
        if section == "UND":
            undefined.append(name)
        else:
            symbols[name] = int(value, 16)

    return symbols, undefined


def first_load_paddr(output: str) -> int:
    for line in output.splitlines():
        match = re.match(
            r"\s*LOAD\s+0x[0-9a-fA-F]+\s+0x[0-9a-fA-F]+\s+"
            r"(0x[0-9a-fA-F]+)",
            line,
        )
        if match is not None:
            return int(match.group(1), 16)

    raise RuntimeError("ELF has no PT_LOAD program header")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("elf", type=Path)
    parser.add_argument("--readelf", required=True, type=Path)
    args = parser.parse_args()

    try:
        header = run(args.readelf, "--file-header", str(args.elf))
        program_headers = run(args.readelf, "--program-headers", str(args.elf))
        symbol_text = run(args.readelf, "--symbols", str(args.elf))
        symbols, undefined = parse_symbols(symbol_text)

        if re.search(r"Machine:\s+(?:Propeller|12c\b)", header) is None:
            raise RuntimeError("ELF machine is not Propeller")

        paddr = first_load_paddr(program_headers)
        if paddr != 0:
            raise RuntimeError(f"first PT_LOAD paddr is {paddr:#x}, expected 0")

        required = {
            "__entry": 0,
            "__start0": None,
            "__start": None,
            "main": None,
            "__stack": None,
            "_bss_start": None,
            "_bss_end": None,
        }
        missing = sorted(name for name in required if name not in symbols)
        if missing:
            raise RuntimeError("missing required symbols: " + ", ".join(missing))

        if symbols["__entry"] != 0:
            raise RuntimeError(
                f"__entry is {symbols['__entry']:#x}, expected address zero"
            )

        if symbols["main"] < 0x0A00:
            raise RuntimeError(
                f"main is {symbols['main']:#x}, below legal Hub C address 0xa00"
            )

        if symbols["__stack"] < symbols["_bss_end"]:
            raise RuntimeError("initial PTRA stack overlaps .bss")

        real_undefined = sorted(name for name in undefined if name)
        if real_undefined:
            raise RuntimeError("undefined symbols: " + ", ".join(real_undefined))

    except RuntimeError as error:
        print(f"VERIFY FAILED: {error}", file=sys.stderr)
        return 1

    print(
        "VERIFIED: native P2 ELF; first PT_LOAD paddr=0; "
        f"main={symbols['main']:#x}; stack={symbols['__stack']:#x}; "
        "no undefined symbols"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
