#!/usr/bin/env python3
"""Build and run ten locked repetitions of the fixed P2HELLO protocol."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import hil


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    # Put fixed options last so this wrapper cannot be weakened by an earlier
    # --cycles value.  hil.py always requires every HELLO_MARKER, including
    # P2HELLO:ECHO=?, and accepts only additional --expect literals.
    arguments.extend(("--cycles", "10", "--build-standalone"))
    return hil.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
