#!/usr/bin/env python3
"""Build and run the locked P2 scheduler-stress HIL protocol."""

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import hil
from schedstress_protocol import parse_schedstress


LOCKED_CYCLES = 1
LOCKED_TIMEOUT = 600


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the deterministic P2 scheduler stress test",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--cycles", type=int, default=LOCKED_CYCLES)
    parser.add_argument("--timeout", type=float, default=LOCKED_TIMEOUT)
    parser.add_argument("--artifact-dir")
    parser.add_argument("--image")
    parser.add_argument("--port")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument(
        "--parse-log",
        type=pathlib.Path,
        help="validate a captured console log without touching hardware",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.parse_log is not None:
        result = parse_schedstress(
            args.parse_log.read_text(encoding="utf-8", errors="replace")
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["complete"] else 3

    if not args.execute:
        print(
            "DRY-RUN: no build, serial open, reset, or load was performed; "
            "pass --execute",
            file=sys.stderr,
        )
        return 2

    arguments = [
        "--execute",
        "--protocol",
        "schedstress",
        "--cycles",
        str(LOCKED_CYCLES),
        "--timeout",
        str(LOCKED_TIMEOUT),
    ]
    if not args.no_build:
        arguments.append("--build-standalone")
    if args.artifact_dir:
        arguments.extend(("--artifact-dir", args.artifact_dir))
    if args.image:
        arguments.extend(("--image", args.image))
    if args.port:
        arguments.extend(("--port", args.port))

    return hil.main(arguments, env=hil.local_environment(os.environ))


if __name__ == "__main__":
    raise SystemExit(main())
