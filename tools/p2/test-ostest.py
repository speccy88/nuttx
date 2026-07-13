#!/usr/bin/env python3
"""Run one exact P2 flat-UP ostest HIL profile.

The PI and non-PI condition-variable matrices each have immutable assertion
and production defconfigs.  Assertion profiles run once; production profiles
run through five consecutive RAM-load/reset cycles.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import hil


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--assertion-run", action="store_true")
    parser.add_argument("--profile", choices=("pi", "cond"), default="pi")
    options, arguments = parser.parse_known_args(arguments)

    fixed = ["--protocol", "ostest"]
    if options.assertion_run:
        fixed.extend(
            (
                "--cycles",
                "1",
                "--timeout",
                "3600",
                "--ostest-assertions",
                "enabled",
                "--ostest-profile",
                "ostest-{}-assert".format(options.profile),
                "--build-standalone",
            )
        )
    else:
        fixed.extend(
            (
                "--cycles",
                "5",
                "--timeout",
                "3600",
                "--ostest-assertions",
                "disabled",
                "--ostest-profile",
                "ostest-{}-production".format(options.profile),
                "--build-standalone",
            )
        )

    # Fixed options come last so a caller cannot weaken the marker protocol,
    # reset coverage, timeout, assertion classification, or production build.

    return hil.main(arguments + fixed)


if __name__ == "__main__":
    raise SystemExit(main())
