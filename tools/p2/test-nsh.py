#!/usr/bin/env python3
"""Build and run 50 locked repetitions of the Phase 9 NSH protocol."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import hil


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)

    # Fixed options come last so callers cannot weaken the Phase 9 protocol,
    # reduce its 50-cycle reset coverage, skip the build, or shorten the
    # per-cycle window containing the measured one-second sleep.

    arguments.extend(
        (
            "--protocol",
            "nsh",
            "--cycles",
            "50",
            "--timeout",
            "30",
            "--build-standalone",
        )
    )
    return hil.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
