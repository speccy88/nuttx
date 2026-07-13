#!/usr/bin/env python3
"""Prepare a validated raw binary for pinned loadp2 -FLASH input."""

import argparse
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).with_name("lib")))

from flash_layout import image_plan, validate


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("binary", type=pathlib.Path)
parser.add_argument("-o", "--output", type=pathlib.Path,
                    default=pathlib.Path("p2-flash-input.bin"))
args = parser.parse_args()
data = args.binary.read_bytes()
if data.startswith(b"\x7fELF"):
    parser.error("input must be a raw binary, not ELF")
plan = image_plan(len(data))
validate(image_size=len(data))
args.output.write_bytes(data)
manifest = {
    "format": "loadp2-single-flash-input-v1",
    "image_size": len(data),
    "image_sha256": hashlib.sha256(data).hexdigest(),
    "payload_offset": plan.payload_offset,
    "payload_end": plan.payload_end,
    "program_end": plan.program_end,
    "erase_end": plan.erase_end,
}
args.output.with_suffix(args.output.suffix + ".json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print(f"HOST-VERIFIED loadp2 -FLASH input: {args.output}")
