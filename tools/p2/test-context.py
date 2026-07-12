#!/usr/bin/env python3
"""Build and run the locked one-million-switch P2 context protocol."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import hil


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)

    # Fixed options come last so callers cannot weaken the context protocol,
    # skip its build, or substitute the hello marker set.

    arguments.extend(
        (
            "--protocol",
            "context",
            "--cycles",
            "1",
            "--timeout",
            "600",
            "--build-standalone",
        )
    )
    return hil.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
