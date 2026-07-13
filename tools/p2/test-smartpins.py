#!/usr/bin/env python3
"""Build and run the locked Phase 11 P2 Smart Pin HIL protocol."""

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import hil
from smartpins_protocol import parse_smartpins


LOCKED_CYCLES = 50
LOCKED_TIMEOUT = 15


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run deterministic P2 Smart Pin loopback tests",
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
    parser.add_argument(
        "--expected-stage",
        action="append",
        choices=("GPIO", "EDGE", "UART", "PWM_CAPTURE", "DAC_ADC", "SPI"),
        help="stage enabled in the exact image; repeat in canonical order",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    stages = tuple(args.expected_stage) if args.expected_stage else None

    if args.parse_log is not None:
        result = parse_smartpins(
            args.parse_log.read_text(encoding="utf-8", errors="replace"),
            expected_stages=stages,
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

    environment = hil.local_environment(os.environ)
    if environment.get("P2_ALLOW_LOOPBACK_TESTS", "0") != "1":
        print(
            "HIL REQUIRED: set P2_ALLOW_LOOPBACK_TESTS=1 before --execute",
            file=sys.stderr,
        )
        return 2

    arguments = [
        "--execute",
        "--protocol",
        "smartpins",
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

    return hil.main(arguments, env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
