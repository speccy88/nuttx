#!/usr/bin/env python3
"""Generate or verify P2 flash-layout consumers from one canonical model."""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).with_name("lib")))

from flash_layout import generated_files, render_json, validate


ROOT = pathlib.Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    validate()

    stale = []
    for path, expected in generated_files(ROOT).items():
        if args.write:
            path.write_text(expected, encoding="utf-8")
        elif not path.is_file() or path.read_text(encoding="utf-8") != expected:
            stale.append(path)

    if stale:
        for path in stale:
            print(f"STALE: {path}", file=sys.stderr)
        return 1
    if args.json:
        print(render_json(), end="")
    print("STATICALLY-VERIFIED: generated P2 flash layout is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
