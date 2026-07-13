#!/usr/bin/env python3
"""Validate generated flash layout and optionally plan an image range."""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).with_name("lib")))

from flash_layout import (generated_files, image_plan, validate,
                          validate_image_manifest)


ROOT = pathlib.Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=pathlib.Path)
    parser.add_argument("--require-manifest", action="store_true")
    args = parser.parse_args()
    if args.require_manifest and args.image is None:
        parser.error("--require-manifest requires --image")
    validate()
    for path, expected in generated_files(ROOT).items():
        if path.read_text(encoding="utf-8") != expected:
            parser.error(f"generated flash-layout file is stale: {path}")

    print("STATICALLY-VERIFIED: flash layout validates")
    if args.image:
        data = args.image.read_bytes()
        if data.startswith(b"\x7fELF"):
            parser.error("loadp2 -FLASH input must be a raw binary, not ELF")
        plan = image_plan(len(data))
        validate(image_size=len(data))
        print(f"image_size=0x{plan.image_size:08x}")
        print(f"payload_range=[0x{plan.payload_offset:08x},0x{plan.payload_end:08x})")
        print(f"program_range=[0x00000000,0x{plan.program_end:08x})")
        print(f"erase_range=[0x00000000,0x{plan.erase_end:08x})")
        if args.require_manifest:
            try:
                manifest = validate_image_manifest(args.image)
            except ValueError as exc:
                parser.error(str(exc))
            print(f"manifest_format={manifest['format']}")
            print(f"manifest_path={args.image.with_suffix(args.image.suffix + '.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
