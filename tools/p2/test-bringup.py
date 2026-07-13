#!/usr/bin/env python3
"""Build and run 100 locked repetitions of the Phase 8 bring-up protocol."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import hil


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)

    arguments.extend(
        (
            "--protocol",
            "bringup",
            "--cycles",
            "100",
            "--timeout",
            "10",
            "--build-standalone",
        )
    )
    return hil.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
