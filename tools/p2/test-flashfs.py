#!/usr/bin/env python3
"""Run guarded P2 SmartFS write and reset-persistence HIL stages."""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import storage_plan


LOCKED_TIMEOUT = 3600.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run destructive P2 flash-filesystem HIL",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--format",
        action="store_true",
        help=(
            "explicitly include flash-format; formatting is never inferred "
            "from mount failure"
        ),
    )
    parser.add_argument("--sequence", help="exact 8-uppercase-hex run nonce")
    parser.add_argument("--artifact-dir")
    parser.add_argument("--image")
    parser.add_argument("--port")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--timeout", type=float, default=LOCKED_TIMEOUT)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout <= 0 or args.timeout > LOCKED_TIMEOUT:
        print("--timeout must be in (0, 3600]", file=sys.stderr)
        return 2
    try:
        sequence = storage_plan.storage_sequence(args.sequence)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    actions = ["probe"]
    if args.format:
        actions.append("flash-format")
    actions.extend(
        (
            "flash-write",
            "flash-verify",
            "flash-cycle",
            "flash-full",
            "flash-interrupt-arm",
            "flash-interrupt-verify",
        )
    )
    return storage_plan.run_plan(
        kind="flashfs",
        actions=actions,
        sequence=sequence,
        artifact_dir=args.artifact_dir,
        image=args.image,
        port=args.port,
        no_build=args.no_build,
        timeout=args.timeout,
        execute=args.execute,
    )


if __name__ == "__main__":
    raise SystemExit(main())
