#!/usr/bin/env python3
"""Build and run the storage binding probe without data writes or mounts."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import hil


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    arguments.extend(
        (
            "--protocol",
            "storage",
            "--cycles",
            "10",
            "--timeout",
            "30",
            "--build-standalone",
        )
    )
    return hil.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
