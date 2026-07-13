#!/usr/bin/env python3
"""Build and run the destructive P2 external-PSRAM HIL protocol."""

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import hil
from psram_protocol import normalize_sequence, parse_psram


LOCKED_TIMEOUT = 1800


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the bounded 32-MiB P2 PSRAM service test",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--sequence", default="A55A0713")
    parser.add_argument("--timeout", type=float, default=LOCKED_TIMEOUT)
    parser.add_argument("--artifact-dir")
    parser.add_argument("--image")
    parser.add_argument("--port")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument(
        "--parse-log",
        type=pathlib.Path,
        help="validate an existing console transcript without hardware",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sequence = normalize_sequence(args.sequence)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.parse_log is not None:
        result = parse_psram(
            args.parse_log.read_text(encoding="utf-8", errors="replace"),
            sequence,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["complete"] else 3

    if not args.execute:
        print(
            "DRY-RUN: no build, serial open, reset, load, or PSRAM write was "
            "performed; pass --execute",
            file=sys.stderr,
        )
        return 2

    environment = hil.local_environment(os.environ)
    if environment.get("P2_ALLOW_PSRAM_WRITE", "0") != "1":
        print(
            "Refusing PSRAM writes: set P2_ALLOW_PSRAM_WRITE=1 before --execute",
            file=sys.stderr,
        )
        return 2

    arguments = [
        "--execute",
        "--protocol",
        "psram",
        "--cycles",
        "1",
        "--timeout",
        str(LOCKED_TIMEOUT),
        "--psram-sequence",
        sequence,
    ]
    if not args.no_build:
        arguments.append("--build-standalone")
    if args.artifact_dir:
        arguments.extend(("--artifact-dir", args.artifact_dir))
    if args.image:
        arguments.extend(("--image", args.image))
    if args.port:
        arguments.extend(("--port", args.port))
    return hil.main(arguments, env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
