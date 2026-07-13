#!/usr/bin/env python3
"""Prepare a validated raw binary for pinned loadp2 -FLASH input."""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).with_name("lib")))

from flash_layout import image_manifest


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("binary", type=pathlib.Path)
parser.add_argument("-o", "--output", type=pathlib.Path,
                    default=pathlib.Path("p2-flash-input.bin"))
args = parser.parse_args()
data = args.binary.read_bytes()
if data.startswith(b"\x7fELF"):
    parser.error("input must be a raw binary, not ELF")
manifest = image_manifest(data)
args.output.write_bytes(data)
args.output.with_suffix(args.output.suffix + ".json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print(f"HOST-VERIFIED loadp2 -FLASH input: {args.output}")
